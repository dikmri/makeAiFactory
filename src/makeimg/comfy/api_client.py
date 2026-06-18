from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import AsyncIterator

import httpx
import websockets

from ..domain.errors import GenerationError, PromptValidationError
from ..domain.progress import ComfyProgressEvent

logger = logging.getLogger(__name__)

_MSG_TIMEOUT = 60.0


class ComfyApiClient:
    def __init__(self, base_url: str, client_id: str | None = None):
        self._base = base_url.rstrip("/")
        self._client_id = client_id or f"makeimg-{uuid.uuid4().hex[:8]}"

    @property
    def client_id(self) -> str:
        return self._client_id

    async def wait_until_ready(self, timeout_sec: int = 120) -> None:
        deadline = asyncio.get_event_loop().time() + timeout_sec
        async with httpx.AsyncClient(timeout=5) as client:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    resp = await client.get(f"{self._base}/system_stats")
                    if resp.status_code == 200:
                        logger.info("ComfyUI起動確認OK")
                        return
                except Exception:
                    pass
                await asyncio.sleep(2)
        raise TimeoutError(f"ComfyUIが{timeout_sec}秒以内に起動しませんでした")

    async def upload_image(self, path: Path) -> str:
        """画像をComfyUI inputへアップロードし、ComfyUI上のファイル名を返す。"""
        async with httpx.AsyncClient(timeout=60) as client:
            with path.open("rb") as f:
                files = {"image": (path.name, f, "image/png")}
                data = {"overwrite": "true", "type": "input"}
                resp = await client.post(f"{self._base}/upload/image", files=files, data=data)
            resp.raise_for_status()
            result = resp.json()
            name = result.get("name", path.name)
            logger.debug("画像アップロード完了: %s → %s", path.name, name)
            return name

    async def queue_prompt(self, workflow: dict) -> str:
        """workflowをキューに投入し、prompt_idを返す。

        ComfyUIが200を返してもnode_errorsが含まれる場合は
        PromptValidationErrorを投げる。
        """
        payload = {
            "prompt": workflow,
            "client_id": self._client_id,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{self._base}/prompt", json=payload)
        if resp.status_code != 200:
            detail = resp.text[:500]
            raise PromptValidationError(f"prompt投入失敗 ({resp.status_code}): {detail}")
        result = resp.json()
        node_errors = result.get("node_errors", {})
        if node_errors:
            lines = []
            for node_id, errors in node_errors.items():
                for e in errors:
                    lines.append(f"  node {node_id}: {e}")
            detail = "\n".join(lines)
            raise PromptValidationError(f"バリデーションエラー:\n{detail}")
        prompt_id = result.get("prompt_id", "")
        if not prompt_id:
            raise PromptValidationError(f"prompt_idが取得できませんでした: {result}")
        logger.info("prompt投入完了: %s", prompt_id)
        return prompt_id

    async def submit_and_watch(
        self,
        workflow: dict,
        timeout_sec: float = 1800.0,
    ) -> AsyncIterator[ComfyProgressEvent]:
        """WebSocketを先に接続してからpromptを投入し、進捗を監視する。

        レースコンディション（prompt投入→WS接続の間にイベントを逃す）を防ぐため、
        WebSocket接続を先に行う。

        Args:
            workflow: 投入するワークフロー
            timeout_sec: 全体タイムアウト（秒）。デフォルト30分。
        """
        ws_url = f"{self._base.replace('http', 'ws')}/ws?clientId={self._client_id}"
        start = asyncio.get_event_loop().time()

        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                # 最初のstatusメッセージを消費
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    _ = json.loads(raw) if isinstance(raw, str) else None
                except asyncio.TimeoutError:
                    pass

                # WebSocket接続後にprompt投入（レースコンディション防止）
                prompt_id = await self.queue_prompt(workflow)

                async for event in self._recv_loop(ws, prompt_id, start, timeout_sec):
                    yield event

        except TimeoutError:
            raise
        except websockets.exceptions.ConnectionClosed as e:
            logger.error("WebSocket接続が切断されました: %s", e)
            raise GenerationError(f"ComfyUIとの接続が切断されました: {e}")
        except GenerationError:
            raise
        except PromptValidationError:
            raise
        except Exception as e:
            logger.error("submit_and_watchで予期しないエラー: %s", e)
            raise GenerationError(f"進捗監視中にエラーが発生しました: {e}")

    async def _recv_loop(
        self,
        ws,
        prompt_id: str,
        start: float,
        timeout_sec: float,
    ) -> AsyncIterator[ComfyProgressEvent]:
        """WebSocketからメッセージを受信しイベントに変換する。

        メッセージが_MSG_TIMEOUT秒来ない場合はhistory APIでフォールバック確認する。
        """
        no_msg_count = 0

        while True:
            # 全体タイムアウトチェック
            if asyncio.get_event_loop().time() - start > timeout_sec:
                raise TimeoutError(f"生成が{timeout_sec}秒以内に完了しませんでした")

            # メッセージ受信（タイムアウト付き）
            try:
                raw_msg = await asyncio.wait_for(ws.recv(), timeout=_MSG_TIMEOUT)
            except asyncio.TimeoutError:
                no_msg_count += 1
                logger.warning("WebSocketメッセージが%.0f秒間なし (%d回目)、history APIで確認", _MSG_TIMEOUT, no_msg_count)
                done, error = await self._check_history(prompt_id)
                if done:
                    if error:
                        raise GenerationError(f"生成エラー(history確認): {error}")
                    return
                if no_msg_count >= 5:
                    raise TimeoutError(f"WebSocketメッセージが{no_msg_count * _MSG_TIMEOUT:.0f}秒間受信できませんでした")
                continue
            except websockets.exceptions.ConnectionClosed as e:
                logger.error("WebSocket接続が切断されました: %s", e)
                done, error = await self._check_history(prompt_id)
                if done and error:
                    raise GenerationError(f"生成エラー: {error}")
                if done:
                    return
                raise GenerationError(f"ComfyUIとの接続が切断されました: {e}")

            no_msg_count = 0

            if isinstance(raw_msg, bytes):
                event = ComfyProgressEvent(
                    event_type="preview",
                    preview_data=raw_msg,
                )
                yield event
                continue
            try:
                msg = json.loads(raw_msg)
            except Exception:
                continue

            event_type = msg.get("type", "")
            data = msg.get("data", {})
            event_prompt_id = data.get("prompt_id", "")

            # validation_failedイベントの処理
            if event_type == "validation_failed":
                msgs = data.get("message", "")
                raise GenerationError(f"プロンプト検証失敗: {msgs}")

            # prompt_outputs_failed_validationも処理
            if event_type == "prompt_outputs_failed_validation":
                raise GenerationError("プロンプト検証失敗: 出力ノードの検証に失敗しました")

            if event_prompt_id and event_prompt_id != prompt_id:
                continue

            event = ComfyProgressEvent(
                event_type=event_type,
                prompt_id=event_prompt_id or prompt_id,
                node_id=str(data.get("node", "")),
                step=data.get("value", 0),
                max_steps=data.get("max", 0),
                raw=msg,
            )
            yield event

            if event_type == "execution_error":
                err_msg = data.get("exception_message", data.get("message", "不明なエラー"))
                raise GenerationError(f"生成エラー: {err_msg}")

            if event_type == "executing" and data.get("node") is None:
                break

    async def _check_history(self, prompt_id: str) -> tuple[bool, str]:
        """history APIでプロンプトの完了状態を確認する。

        Returns:
            (done, error_message): 完了していればdone=True, エラーがあればerror_messageに内容
        """
        try:
            history = await self.get_history(prompt_id)
            entry = history.get(prompt_id)
            if entry is None:
                return False, ""
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                msgs = status.get("messages", [])
                err_text = "; ".join(str(m) for m in msgs) if msgs else "不明なエラー"
                return True, err_text
            outputs = entry.get("outputs", {})
            if outputs:
                return True, ""
            return False, ""
        except Exception as e:
            logger.debug("history確認失敗: %s", e)
            return False, ""

    async def watch_progress(self, prompt_id: str, timeout_sec: float = 1800.0) -> AsyncIterator[ComfyProgressEvent]:
        """生成進捗を監視する（レガシーAPI、submit_and_watchを推奨）。"""
        ws_url = f"{self._base.replace('http', 'ws')}/ws?clientId={self._client_id}"
        start = asyncio.get_event_loop().time()

        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                async for event in self._recv_loop(ws, prompt_id, start, timeout_sec):
                    yield event
        except TimeoutError:
            raise
        except websockets.exceptions.ConnectionClosed as e:
            logger.error("WebSocket接続が切断されました: %s", e)
            raise GenerationError(f"ComfyUIとの接続が切断されました: {e}")
        except GenerationError:
            raise
        except PromptValidationError:
            raise
        except Exception as e:
            logger.error("watch_progressで予期しないエラー: %s", e)
            raise GenerationError(f"進捗監視中にエラーが発生しました: {e}")

    async def get_history(self, prompt_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self._base}/history/{prompt_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_object_info(self) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self._base}/object_info")
        resp.raise_for_status()
        return resp.json()

    async def interrupt(self) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                await client.post(f"{self._base}/interrupt")
            except Exception as e:
                logger.warning("interrupt失敗: %s", e)

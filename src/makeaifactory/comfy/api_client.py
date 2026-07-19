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


class ComfyApiClient:
    def __init__(self, base_url: str, client_id: str | None = None):
        self._base = base_url.rstrip("/")
        self._client_id = client_id or f"makeaifactory-{uuid.uuid4().hex[:8]}"

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def base_url(self) -> str:
        """SCH-01 PR4: GenerationExecutorの実行中レジストリがinterrupt発行先を
        特定するために参照する (owner/job_id照合済みのcancelのみに使う)。"""
        return self._base

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
        """workflowをキューに投入し、prompt_idを返す。"""
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
        prompt_id = result.get("prompt_id", "")
        if not prompt_id:
            raise PromptValidationError(f"prompt_idが取得できませんでした: {result}")
        logger.info("prompt投入完了: %s", prompt_id)
        return prompt_id

    async def watch_progress(self, prompt_id: str) -> AsyncIterator[ComfyProgressEvent]:
        ws_url = f"{self._base.replace('http', 'ws')}/ws?clientId={self._client_id}"
        async with websockets.connect(ws_url) as ws:
            async for raw_msg in ws:
                if isinstance(raw_msg, bytes):
                    continue
                try:
                    msg = json.loads(raw_msg)
                except Exception:
                    continue

                event_type = msg.get("type", "")
                data = msg.get("data", {})
                event_prompt_id = data.get("prompt_id", "")

                if event_prompt_id and event_prompt_id != prompt_id:
                    continue

                node_raw = data.get("node")
                event = ComfyProgressEvent(
                    event_type=event_type,
                    prompt_id=event_prompt_id or prompt_id,
                    node_id="" if node_raw is None else str(node_raw),
                    step=data.get("value", 0),
                    max_steps=data.get("max", 0),
                    raw=msg,
                )
                yield event

                if event_type == "execution_error":
                    raise GenerationError(
                        f"生成エラー: {data.get('exception_message', '不明なエラー')}"
                    )
                # ComfyUI は全ノード完了後に executing: {node: null} を送信する
                if event_type == "executing" and data.get("node") is None:
                    break

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

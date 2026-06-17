"""Discord Bot をアプリ内プロセス（デーモンスレッド + 独自 asyncio ループ）として実行するコントローラ。

Qt メインスレッドとは DiscordBotSignals (QObject の Signal) 経由で通信する。
Signal は PySide6 の内部でスレッドセーフに処理される（QueuedConnection）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..comfy.api_client import ComfyApiClient
from ..comfy.output_resolver import resolve_output_mp4
from ..comfy.workflow_patcher import WorkflowPatchContext, make_output_prefix, patch_workflow
from ..constants import COMFY_HOST, MODEL_PRESETS
from ..core.bot_state import read_bot_state, write_bot_state
from ..core.paths import AppPaths
from ..core.settings_store import SettingsStore

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_MAX_QUEUE_SIZE = 3


class _CancelledError(Exception):
    pass


class DiscordBotSignals(QObject):
    job_started   = Signal(str, str)   # image_local_path, discord_username
    job_progress  = Signal(float, str) # percent(0-100), status_text
    job_done      = Signal(str)        # output_mp4_path
    job_cancelled = Signal()
    job_error     = Signal(str)        # error_message
    status_changed = Signal(str)       # "接続中..." | "接続完了: BotName" | "停止" | "エラー: ..."


class DiscordBotController:
    def __init__(self, settings: SettingsStore, paths: AppPaths) -> None:
        self._settings = settings
        self._paths = paths
        self._signals = DiscordBotSignals()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._discord_client = None          # discord.Client (set in bot thread)
        self._current_comfy_client: ComfyApiClient | None = None
        self._cancel_requested = threading.Event()
        self._running = False

    @property
    def signals(self) -> DiscordBotSignals:
        return self._signals

    @property
    def is_running(self) -> bool:
        return self._running and (self._thread is not None) and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        try:
            import discord as _dc  # noqa: F401 — early check
        except ImportError:
            self._signals.status_changed.emit("エラー: discord.py がインストールされていません")
            logger.error("discord.py がインストールされていません")
            return

        self._running = True
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name="DiscordBotThread")
        self._thread.start()
        logger.info("Discord Bot スレッド起動")

    def stop(self) -> None:
        self._running = False
        self._cancel_requested.set()
        if self._loop and self._current_comfy_client:
            asyncio.run_coroutine_threadsafe(self._current_comfy_client.interrupt(), self._loop)
        if self._loop and self._discord_client is not None:
            asyncio.run_coroutine_threadsafe(self._discord_client.close(), self._loop)
        self._signals.status_changed.emit("停止")
        logger.info("Discord Bot 停止要求")

    def cancel_current_job(self) -> None:
        """現在実行中の ComfyUI ジョブをキャンセルする。"""
        self._cancel_requested.set()
        if self._loop and self._current_comfy_client:
            asyncio.run_coroutine_threadsafe(self._current_comfy_client.interrupt(), self._loop)
        logger.info("Discord ジョブキャンセル要求")

    # ── スレッドエントリポイント ───────────────────────────────────────────────

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._bot_main())
        except Exception as e:
            if self._running:
                logger.exception("Discord Bot 予期しないエラー")
                self._signals.status_changed.emit(f"エラー: {e}")
        finally:
            self._running = False
            self._loop.close()
            logger.info("Discord Bot スレッド終了")

    # ── Bot メインコルーチン ────────────────────────────────────────────────

    async def _bot_main(self) -> None:
        import discord

        intents = discord.Intents.default()
        intents.message_content = True
        self._discord_client = discord.Client(intents=intents)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)

        @self._discord_client.event
        async def on_ready() -> None:
            user = self._discord_client.user
            status = f"接続完了: {user}"
            logger.info("Discord Bot 起動: %s", user)
            self._signals.status_changed.emit(status)
            # bot_state.json を更新して「アイドル状態・準備完了」を記録する
            write_bot_state(self._paths.runtime_root, "idle")

        @self._discord_client.event
        async def on_disconnect() -> None:
            if self._running:
                logger.warning("Discord Bot 切断")
                self._signals.status_changed.emit("切断 (再接続中...)")

        @self._discord_client.event
        async def on_message(message) -> None:
            await self._handle_message(message)

        self._signals.status_changed.emit("接続中...")
        worker_task = asyncio.create_task(self._worker())
        state_task = asyncio.create_task(self._keep_state_alive())
        try:
            await self._discord_client.start(self._settings.discord_token)
        except Exception as e:
            import discord as dc
            if isinstance(e, dc.LoginFailure):
                self._signals.status_changed.emit("エラー: トークンが無効です")
                logger.error("Discord ログイン失敗: %s", e)
            elif self._running:
                self._signals.status_changed.emit(f"エラー: {e}")
                logger.error("Discord Bot エラー: %s", e)
        finally:
            worker_task.cancel()
            state_task.cancel()

    # ── メッセージ受信 ─────────────────────────────────────────────────────

    async def _handle_message(self, message) -> None:
        import discord

        if message.author.bot:
            return

        channel_ids = self._settings.discord_channel_ids
        if channel_ids and message.channel.id not in channel_ids:
            return

        image_att = next(
            (a for a in message.attachments
             if Path(a.filename).suffix.lower() in SUPPORTED_EXTENSIONS),
            None,
        )
        if image_att is None:
            return

        state, _ = read_bot_state(self._paths.runtime_root)

        if state == "batch":
            await message.reply(
                "フォルダ生成中のため、現在リクエストを受け付けられません。\n"
                "フォルダ生成が完了してからもう一度お試しください。"
            )
            return

        # "offline" はタイムスタンプ期限切れを意味するが、アプリ内 Bot は
        # 同一プロセスなので期限切れでも処理を続行する（comfy_port は _process で再確認）

        if self._queue.full():
            await message.reply("リクエストが集中しています。しばらく待ってからもう一度お試しください。")
            return

        pos = self._queue.qsize() + 1
        if pos == 1:
            await message.reply("受け付けました。生成を開始します...")
        else:
            await message.reply(f"受け付けました。現在 {pos} 番目に並んでいます。しばらくお待ちください。")

        await self._queue.put((message, image_att))

    # ── 状態ファイル定期更新 ───────────────────────────────────────────────

    async def _keep_state_alive(self) -> None:
        """bot_state.json のタイムスタンプを 90 秒ごとに更新する。

        read_bot_state() は 5 分以上更新がないと "offline" を返すため、
        Bot が接続済みでアイドル状態でも定期的に "idle" を書き続ける。
        """
        while True:
            await asyncio.sleep(90)
            if self._running:
                write_bot_state(self._paths.runtime_root, "idle")
                logger.debug("bot_state.json 更新 (keep-alive)")

    # ── ワーカーループ ─────────────────────────────────────────────────────

    async def _worker(self) -> None:
        while True:
            message, attachment = await self._queue.get()
            try:
                await self._process(message, attachment)
            except asyncio.CancelledError:
                raise
            except _CancelledError:
                pass  # job_cancelled は _process 内で emit 済み
            except Exception as e:
                logger.exception("Discord 生成処理エラー")
                self._signals.job_error.emit(str(e))
                try:
                    await message.reply(f"生成中にエラーが発生しました。\n`{e}`")
                except Exception:
                    pass
            finally:
                self._cancel_requested.clear()
                self._queue.task_done()

    # ── 1件の生成処理 ──────────────────────────────────────────────────────

    async def _process(self, message, attachment) -> None:
        self._cancel_requested.clear()

        # デキュー直前の状態確認
        state, comfy_port = read_bot_state(self._paths.runtime_root)
        if state == "batch":
            await message.reply("フォルダ生成が始まったため、このリクエストはキャンセルされました。")
            return
        if comfy_port == 0:
            await message.reply("ComfyUI のポートが不明です。アプリを再起動してください。")
            return

        suffix = Path(attachment.filename).suffix.lower() or ".png"
        tmp_dir = self._paths.runtime_root / "downloads" / f"discord_{uuid.uuid4().hex[:6]}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        image_path = tmp_dir / f"input{suffix}"

        try:
            await attachment.save(image_path)
            username = str(message.author.display_name)
            self._signals.job_started.emit(str(image_path), username)

            output_path = await self._generate_video(image_path, comfy_port)

            self._signals.job_done.emit(str(output_path))
            import discord
            await message.reply(file=discord.File(str(output_path), filename="output.mp4"))
            logger.info("Discord 返信完了: %s", output_path)
        except _CancelledError:
            self._signals.job_cancelled.emit()
            try:
                await message.reply("生成がキャンセルされました。")
            except Exception:
                pass
            raise
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── ComfyUI 動画生成 ───────────────────────────────────────────────────

    async def _generate_video(self, image_path: Path, comfy_port: int) -> Path:
        model_preset = self._settings.model_preset
        preset_def = MODEL_PRESETS.get(model_preset, MODEL_PRESETS["normal"])

        template_path = self._paths.runtime_template_json()
        if not template_path.exists():
            raise FileNotFoundError(f"ワークフローテンプレートが見つかりません: {template_path}")
        with template_path.open(encoding="utf-8") as f:
            template = json.load(f)

        job_id = uuid.uuid4().hex[:8]
        date_str = datetime.now().strftime("%Y%m%d")
        job_dir = self._paths.job_dir(job_id, date_str)
        job_dir.mkdir(parents=True, exist_ok=True)

        input_copy = job_dir / ("input" + image_path.suffix)
        shutil.copy2(image_path, input_copy)

        base_url = f"http://{COMFY_HOST}:{comfy_port}"
        client = ComfyApiClient(base_url)
        self._current_comfy_client = client

        try:
            logger.info("ComfyUI 接続確認: %s", base_url)
            await client.wait_until_ready(timeout_sec=30)

            upload_name_src = job_dir / (f"discord_{job_id}{image_path.suffix}")
            shutil.copy2(image_path, upload_name_src)
            uploaded_name = await client.upload_image(upload_name_src)
            logger.info("画像アップロード: %s", uploaded_name)

            seed = random.randint(0, 2**32 - 1)
            ctx = WorkflowPatchContext(
                job_id=job_id,
                uploaded_image_name=uploaded_name,
                output_prefix=make_output_prefix(job_id),
                seed=seed,
                unet_high_name=preset_def["unet_high"],
                unet_low_name=preset_def["unet_low"],
                sage_attention_mode="disabled",
            )
            patched = patch_workflow(template, ctx)

            prompt_id = await client.queue_prompt(patched)
            logger.info("生成開始: job=%s prompt=%s", job_id, prompt_id)

            self._signals.job_progress.emit(0.0, "生成中...")
            async for event in client.watch_progress(prompt_id):
                if event.event_type == "progress" and event.max_steps > 0:
                    pct = event.step / event.max_steps * 100
                    self._signals.job_progress.emit(pct, f"生成中... {int(pct)}%")

            if self._cancel_requested.is_set():
                raise _CancelledError()

            history = await client.get_history(prompt_id)
            output_mp4 = resolve_output_mp4(history, prompt_id, self._paths.comfyui_output_dir, job_id)

            final_output = job_dir / "output.mp4"
            shutil.copy2(output_mp4, final_output)
            logger.info("生成完了: %s → %s", job_id, final_output)
            return final_output
        finally:
            self._current_comfy_client = None

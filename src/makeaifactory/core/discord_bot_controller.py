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
from ..comfy.progress_tracker import StageProgressEstimator, count_progress_stages
from ..comfy.workflow_patcher import WorkflowPatchContext, make_output_prefix, patch_workflow
from ..constants import COMFY_HOST, MODEL_PRESETS
from ..core.bot_state import read_bot_state, write_bot_state
from ..core.paths import AppPaths
from ..core.settings_store import SettingsStore

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

# プリセット別の1動画あたり推定生成時間（分）
_PRESET_WAIT_MIN: dict[str, int] = {"normal": 15, "lite": 10, "ultralite": 8}


def _fmt_minutes(total_min: int) -> str:
    if total_min < 60:
        return f"約{total_min}分"
    h, m = divmod(total_min, 60)
    return f"約{h}時間{m}分" if m else f"約{h}時間"


def _patch_broken_orjson() -> None:
    """discord/utils.py は try/except ImportError で orjson をオプション使用するが、
    PyInstaller が orjson.pyd を不完全収集すると AttributeError が素通りして
    import discord 全体が失敗する。
    orjson.loads が存在しない場合は sys.modules に None をセットして
    次回の import orjson を ImportError にし、discord を標準 json にフォールバックさせる。
    """
    import sys
    try:
        import orjson as _orj
        if not hasattr(_orj, 'loads'):
            sys.modules['orjson'] = None  # type: ignore[assignment]
            logger.warning("orjson が不完全バンドル (loads 欠落) のため除外 → discord が標準 json にフォールバック")
    except ImportError:
        pass


_patch_broken_orjson()
del _patch_broken_orjson
_MAX_QUEUE_SIZE = 100


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
        # 割り込み生成用: バッチ中に Discord リクエストが来たことを示す threading.Event
        self._interrupt_active = threading.Event()
        self._batch_mode: bool = False       # app.py がバッチ開始/終了時にセット

    @property
    def signals(self) -> DiscordBotSignals:
        return self._signals

    @property
    def is_running(self) -> bool:
        return self._running and (self._thread is not None) and self._thread.is_alive()

    @property
    def interrupt_active(self) -> threading.Event:
        return self._interrupt_active

    def set_batch_mode(self, active: bool) -> None:
        """バッチ生成の開始/終了を通知する。割り込みモードのキュー判定に使用。"""
        self._batch_mode = active
        if not active:
            self._interrupt_active.clear()

    def _receipt_msg(self, pos: int, is_interrupt: bool = False) -> str:
        """受付時の Discord 返信メッセージを生成する。"""
        preset = self._settings.model_preset
        label = MODEL_PRESETS.get(preset, MODEL_PRESETS["normal"]).get("label", preset)
        wait_min = pos * _PRESET_WAIT_MIN.get(preset, 12)
        header = "⚡ 割り込み受付しました" if is_interrupt else "🏭 受付しました"
        lines = [
            header,
            f"現在の待ち: {pos}件",
            f"推定時間: {_fmt_minutes(wait_min)}",
            f"使用プリセット: {label}",
        ]
        if is_interrupt:
            lines.append("（現在の動画完了後に割り込み生成します）")
        return "\n".join(lines)

    def _done_msg(self, elapsed_sec: float) -> str:
        """完成時の Discord 返信メッセージを生成する。"""
        preset = self._settings.model_preset
        label = MODEL_PRESETS.get(preset, MODEL_PRESETS["normal"]).get("label", preset)
        mins = int(elapsed_sec // 60)
        secs = int(elapsed_sec % 60)
        time_str = f"{mins}分{secs}秒" if mins > 0 else f"{secs}秒"
        return f"🎬 完成しました\n生成時間: {time_str}\nプリセット: {label}"

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
        async def on_resumed() -> None:
            # セッション再開（Resume）時は on_ready が発火しないため個別に処理する
            user = self._discord_client.user
            logger.info("Discord Bot セッション再開: %s", user)
            self._signals.status_changed.emit(f"接続完了: {user}")
            write_bot_state(self._paths.runtime_root, "idle")

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

        # バッチ生成中の処理: 割り込みモードか否かで分岐
        if self._batch_mode:
            if self._settings.discord_bot_interrupt:
                # 割り込みモード: バッチ中でも受け付けてキューに追加
                if self._queue.full():
                    await message.reply("リクエストが集中しています。しばらく待ってからもう一度お試しください。")
                    return
                self._interrupt_active.set()
                pos = self._queue.qsize() + 1
                await message.reply(self._receipt_msg(pos, is_interrupt=True))
                await self._queue.put((message, image_att, True))
                return
            else:
                await message.reply(
                    "フォルダ生成中のため、現在リクエストを受け付けられません。\n"
                    "フォルダ生成が完了してからもう一度お試しください。"
                )
                return

        # 通常モード (idle / single)
        if self._queue.full():
            await message.reply("リクエストが集中しています。しばらく待ってからもう一度お試しください。")
            return

        pos = self._queue.qsize() + 1
        await message.reply(self._receipt_msg(pos, is_interrupt=False))
        await self._queue.put((message, image_att, False))

    # ── 状態ファイル定期更新 ───────────────────────────────────────────────

    async def _keep_state_alive(self) -> None:
        """bot_state.json のタイムスタンプを 90 秒ごとに更新する。

        read_bot_state() は 5 分以上更新がないと "offline" を返すため、
        現在の state ("idle" / "single" / "batch") を維持したまま
        タイムスタンプだけ更新する (固定で "idle" に書き換えると
        バッチ実行中の状態を誤って消してしまうため)。
        """
        while True:
            await asyncio.sleep(90)
            if self._running:
                current_state, current_port = read_bot_state(self._paths.runtime_root)
                if current_state == "offline":
                    current_state = "idle"
                write_bot_state(self._paths.runtime_root, current_state, current_port)
                logger.debug("bot_state.json 更新 (keep-alive, state=%s)", current_state)

    # ── ワーカーループ ─────────────────────────────────────────────────────

    async def _worker(self) -> None:
        while True:
            message, attachment, is_interrupt = await self._queue.get()
            try:
                await self._process(message, attachment, is_interrupt)
            except asyncio.CancelledError:
                raise
            except _CancelledError:
                pass  # job_cancelled は _process 内で emit 済み
            except Exception as e:
                logger.exception("Discord 生成処理エラー")
                self._signals.job_error.emit(str(e))
                try:
                    await message.reply(
                        "申し訳ありません🙏\n"
                        "生成中にエラーが発生してしまいました。\n"
                        "管理者にお知らせください。"
                    )
                except Exception:
                    pass
            finally:
                self._cancel_requested.clear()
                self._queue.task_done()
                # 割り込みキューが空になったら待機フラグを解除
                if self._queue.empty():
                    self._interrupt_active.clear()

    # ── 1件の生成処理 ──────────────────────────────────────────────────────

    async def _process(self, message, attachment, is_interrupt: bool = False) -> None:
        self._cancel_requested.clear()

        # デキュー直前の状態確認 (割り込みキュー経由のリクエストは常に処理する)
        state, comfy_port = read_bot_state(self._paths.runtime_root)
        if state == "batch" and not is_interrupt:
            await message.reply(
                "申し訳ありません🙏\n"
                "フォルダ生成が始まったため、このリクエストはキャンセルされてしまいました。\n"
                "フォルダ生成が完了してから再度お試しください。"
            )
            return
        if comfy_port == 0:
            await message.reply("ComfyUI のポートが不明です。アプリを再起動してください。")
            return

        suffix = Path(attachment.filename).suffix.lower() or ".png"
        tmp_dir = self._paths.runtime_root / "downloads" / f"discord_{uuid.uuid4().hex[:6]}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        image_path = tmp_dir / f"input{suffix}"

        import time as _time
        try:
            await attachment.save(image_path)
            username = str(message.author.display_name)

            started_emitted = False

            def _emit_started() -> None:
                nonlocal started_emitted
                if not started_emitted:
                    started_emitted = True
                    self._signals.job_started.emit(str(image_path), username)

            if not is_interrupt:
                # 通常モードは即座に表示 (他の画像と競合しないため早期表示で問題ない)
                _emit_started()

            gen_start = _time.monotonic()
            # 割り込みの場合、実際に ComfyUI が生成を開始するまでプレビュー切替を遅らせる
            # (前の画像がまだ生成中のうちにプレビューが切り替わってしまうのを防ぐ)
            output_path = await self._generate_video(
                image_path, comfy_port, on_started=None if not is_interrupt else _emit_started,
            )
            _emit_started()  # フォールバック: execution_start を観測できなかった場合も必ず発火
            elapsed = _time.monotonic() - gen_start

            self._signals.job_done.emit(str(output_path))
            import discord
            await message.reply(
                self._done_msg(elapsed),
                file=discord.File(str(output_path), filename="output.mp4"),
            )
            logger.info("Discord 返信完了: %s", output_path)
        except _CancelledError:
            self._signals.job_cancelled.emit()
            try:
                await message.reply(
                    "申し訳ありません🙏\n"
                    "生成がキャンセルされてしまいました。\n"
                    "再度画像をお送りいただければ対応します。"
                )
            except Exception:
                pass
            raise
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── ComfyUI 動画生成 ───────────────────────────────────────────────────

    async def _generate_video(
        self, image_path: Path, comfy_port: int, on_started=None,
    ) -> Path:
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
            stage_estimator = StageProgressEstimator(count_progress_stages(template))
            async for event in client.watch_progress(prompt_id):
                if on_started is not None and event.event_type == "execution_start":
                    on_started()
                if event.event_type == "progress" and event.max_steps > 0:
                    pct = stage_estimator.update(event.node_id, event.step, event.max_steps)
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

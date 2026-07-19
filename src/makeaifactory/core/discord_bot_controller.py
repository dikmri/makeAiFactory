"""Discord Bot をアプリ内プロセス（デーモンスレッド + 独自 asyncio ループ）として実行するコントローラ。

Qt メインスレッドとは DiscordBotSignals (QObject の Signal) 経由で通信する。
Signal は PySide6 の内部でスレッドセーフに処理される（QueuedConnection）。
"""
from __future__ import annotations

import asyncio
import logging
import random
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..comfy.api_client import ComfyApiClient
from ..comfy.progress_tracker import StageProgressEstimator, count_progress_stages
from ..constants import COMFY_HOST, MODEL_PRESETS
from ..core.bot_state import read_bot_state
from ..core.generation_executor import GenerationExecutor, GenerationRequest, load_template_for_workflow
from ..core.generation_gate import GenerationGate
from ..core.paths import AppPaths
from ..core.settings_store import SettingsStore
from ..domain.errors import JobCancelledError
from ..i18n import tr
from ..remote_room.rate_limiter import RateLimiter
from ..remote_room.upload_validator import validate_upload

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

# 添付画像の実体検証(サイズ・実形式・総画素)に使う既定値。
# Remote Room (RemoteRoomConfig) の既定値と概ね同等にしている。
_UPLOAD_MAX_MB = 20
_UPLOAD_MAX_PX = 4096
_UPLOAD_ALLOWED_EXTENSIONS = ("jpg", "jpeg", "png", "webp")

# ユーザー単位のクールダウン秒数(連投による負荷/乱用を防ぐ fail-closed 対策)
_USER_COOLDOWN_SECONDS = 90

# プリセット別の1動画あたり推定生成時間（分）
_PRESET_WAIT_MIN: dict[str, int] = {"normal": 15, "lite": 10, "ultralite": 8}


def _fmt_minutes(total_min: int) -> str:
    if total_min < 60:
        return f"約{total_min}分"
    h, m = divmod(total_min, 60)
    return f"約{h}時間{m}分" if m else f"約{h}時間"


def _resolve_workflow(content: str) -> str | None:
    """メッセージ本文からワークフロー指定を抽出する。

    本文を空白 (全角スペースを含む) で分割したトークン列を作り、各トークンが
    WORKFLOW_PRESETS の key (大文字小文字無視) または label と一致すれば、
    最初に一致したワークフローキーを返す。一致しなければ None (=既定ワークフロー)。
    """
    from ..constants import WORKFLOW_PRESETS

    if not content:
        return None
    for token in content.split():
        for key, info in WORKFLOW_PRESETS.items():
            if token.lower() == key.lower() or token == info.get("label"):
                return key
    return None


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
# fail-closed 化: 際限なく受け付けず、混雑時は早めに「集中しています」で断る
_MAX_QUEUE_SIZE = 8


class _CancelledError(Exception):
    pass


def is_channel_allowed(channel_id: int, allowed: list[int]) -> bool:
    """チャンネル許可判定 (fail-closed)。

    allowed が空の場合は「未設定」ではなく「どのチャンネルも許可しない」とみなす。
    設定ダイアログ側で有効化時に1件以上のチャンネルIDを必須にしているが、
    ここでも防御的に空リストを拒否側として扱う。
    """
    if not allowed:
        return False
    return channel_id in allowed


def is_dm(guild) -> bool:
    """DM (ギルド外のメッセージ) かどうかを判定する。

    discord.py では guild 外 (DM) のメッセージは Message.guild が None になる。
    """
    return guild is None


class DiscordBotSignals(QObject):
    job_started   = Signal(str, str)   # image_local_path, discord_username
    job_progress  = Signal(float, str) # percent(0-100), status_text
    job_done      = Signal(str)        # output_mp4_path
    job_cancelled = Signal()
    job_error     = Signal(str)        # error_message
    # status_code ("connecting"|"connected"|"stopped"|"error"|"reconnecting"), 表示用テキスト(翻訳済み)
    # status_code は表示色の判定に使う。テキストの言語に依存させないための分離。
    status_changed = Signal(str, str)


class DiscordBotController:
    def __init__(self, settings: SettingsStore, paths: AppPaths, gate: GenerationGate | None = None) -> None:
        self._settings = settings
        self._paths = paths
        # 生成admissionゲート。app.py から共有インスタンスを受け取るのが通常経路。
        # 未指定 (単体テスト等) の場合はbot_stateミラー無しの単独インスタンスを持つ
        # (スタンドアロン discord_bot.py はこのクラスを使わず bot_state.json を
        # 直接読むだけなので、この分岐の有無に影響されない)。
        self._gate = gate if gate is not None else GenerationGate(None)
        # SCH-01 PR3: 3経路共通の生成実行本体。生成フロー自体はここに委譲する。
        self._executor = GenerationExecutor(paths)
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
        # ユーザー単位のクールダウン管理 (fail-closed 化: 連投・乱用を防ぐ)
        self._rate_limiter = RateLimiter(cooldown_seconds=_USER_COOLDOWN_SECONDS)

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

    def _receipt_msg(self, pos: int, is_interrupt: bool = False, workflow_label: str | None = None) -> str:
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
        if workflow_label:
            lines.append(f"🎬 ワークフロー: {workflow_label}")
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
            self._signals.status_changed.emit("error", tr("エラー: discord.py がインストールされていません"))
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
        self._signals.status_changed.emit("stopped", tr("停止"))
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
                self._signals.status_changed.emit("error", tr("エラー: {e}").format(e=e))
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
            logger.info("Discord Bot 起動: %s", user)
            self._signals.status_changed.emit("connected", tr("接続完了: {user}").format(user=user))
            # bot_state.json を gate の現在状態で書き直す (固定で"idle"にすると、
            # 生成実行中の再接続で他経路の状態をclobberしてしまうため)
            self._gate.refresh_bot_state()

        @self._discord_client.event
        async def on_disconnect() -> None:
            if self._running:
                logger.warning("Discord Bot 切断")
                self._signals.status_changed.emit("reconnecting", tr("切断 (再接続中...)"))

        @self._discord_client.event
        async def on_resumed() -> None:
            # セッション再開（Resume）時は on_ready が発火しないため個別に処理する
            user = self._discord_client.user
            logger.info("Discord Bot セッション再開: %s", user)
            self._signals.status_changed.emit("connected", tr("接続完了: {user}").format(user=user))
            self._gate.refresh_bot_state()

        @self._discord_client.event
        async def on_message(message) -> None:
            await self._handle_message(message)

        self._signals.status_changed.emit("connecting", tr("接続中..."))
        worker_task = asyncio.create_task(self._worker())
        state_task = asyncio.create_task(self._keep_state_alive())
        try:
            await self._discord_client.start(self._settings.discord_token)
        except Exception as e:
            import discord as dc
            if isinstance(e, dc.LoginFailure):
                self._signals.status_changed.emit("error", tr("エラー: トークンが無効です"))
                logger.error("Discord ログイン失敗: %s", e)
            elif self._running:
                self._signals.status_changed.emit("error", tr("エラー: {e}").format(e=e))
                logger.error("Discord Bot エラー: %s", e)
        finally:
            worker_task.cancel()
            state_task.cancel()

    # ── メッセージ受信 ─────────────────────────────────────────────────────

    async def _handle_message(self, message) -> None:
        import discord

        if message.author.bot:
            return

        # DM (ギルド外) からのリクエストは fail-closed で無視する
        if is_dm(message.guild):
            return

        channel_ids = self._settings.discord_channel_ids
        if not is_channel_allowed(message.channel.id, channel_ids):
            return

        image_att = next(
            (a for a in message.attachments
             if Path(a.filename).suffix.lower() in SUPPORTED_EXTENSIONS),
            None,
        )
        if image_att is None:
            return

        # ユーザー単位のクールダウン (連投による負荷/乱用を防ぐ)
        user_key = str(message.author.id)
        if not self._rate_limiter.is_allowed(user_key):
            remaining = self._rate_limiter.seconds_until_allowed(user_key)
            await message.reply(
                f"連続でのリクエストはご遠慮ください🙏\nあと {remaining} 秒ほどお待ちください。"
            )
            return

        # 添付画像の実体検証 (拡張子偽装・サイズ超過・圧縮爆弾等を拒否)
        raw_bytes = await image_att.read()
        _validated_png, validation_err = await validate_upload(
            raw_bytes,
            image_att.filename,
            _UPLOAD_MAX_MB,
            _UPLOAD_MAX_PX,
            _UPLOAD_ALLOWED_EXTENSIONS,
        )
        if validation_err:
            logger.warning(
                "Discord 添付画像を却下: code=%s file=%s", validation_err, image_att.filename
            )
            await message.reply(
                "申し訳ありません🙏\n"
                "この画像は受け付けられませんでした。別の画像でお試しください。"
            )
            return

        workflow = _resolve_workflow(message.content)
        logger.info("Discord ワークフロー指定: %s", workflow or "(既定)")
        workflow_label = None
        if workflow:
            from ..constants import WORKFLOW_PRESETS
            workflow_label = WORKFLOW_PRESETS.get(workflow, {}).get("label")

        # バッチ生成中の処理: 割り込みモードか否かで分岐
        if self._batch_mode:
            if self._settings.discord_bot_interrupt:
                # 割り込みモード: バッチ中でも受け付けてキューに追加
                if self._queue.full():
                    await message.reply("リクエストが集中しています。しばらく待ってからもう一度お試しください。")
                    return
                self._interrupt_active.set()
                pos = self._queue.qsize() + 1
                self._rate_limiter.record_job(user_key)
                await message.reply(self._receipt_msg(pos, is_interrupt=True, workflow_label=workflow_label))
                await self._queue.put((message, image_att, True, workflow))
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
        self._rate_limiter.record_job(user_key)
        await message.reply(self._receipt_msg(pos, is_interrupt=False, workflow_label=workflow_label))
        await self._queue.put((message, image_att, False, workflow))

    # ── 状態ファイル定期更新 ───────────────────────────────────────────────

    async def _keep_state_alive(self) -> None:
        """bot_state.json のタイムスタンプを 90 秒ごとに更新する。

        read_bot_state() は 5 分以上更新がないと "offline" を返すため、定期的な
        更新が必要。以前は read-modify-write (現在の状態を読んでから書き戻す)
        していたが、これだとメインスレッド (Desktop/バッチ) が直後に書いた
        最新状態を、ここで読み取った古い値で上書きしてしまう競合があった。
        GenerationGate.refresh_bot_state() はプロセス内で保持している現在状態
        (holder/batch_active) をそのまま書き直すだけなので、read-modify-write
        自体が発生せず、この競合が起きない。
        """
        while True:
            await asyncio.sleep(90)
            if self._running:
                self._gate.refresh_bot_state()
                logger.debug("bot_state.json 更新 (keep-alive)")

    # ── ワーカーループ ─────────────────────────────────────────────────────

    async def _worker(self) -> None:
        while True:
            message, attachment, is_interrupt, workflow = await self._queue.get()
            try:
                await self._process(message, attachment, is_interrupt, workflow)
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

    async def _process(self, message, attachment, is_interrupt: bool = False, workflow: str | None = None) -> None:
        self._cancel_requested.clear()

        # デキュー直前の状態確認 (割り込みキュー経由のリクエストは常に処理する)
        if self._gate.batch_active and not is_interrupt:
            await message.reply(
                "申し訳ありません🙏\n"
                "フォルダ生成が始まったため、このリクエストはキャンセルされてしまいました。\n"
                "フォルダ生成が完了してから再度お試しください。"
            )
            return
        comfy_port = self._gate.comfy_port
        if comfy_port == 0:
            # gate.set_comfy_port() が未実行 (runtime_root未指定のスタンドアロン相当の
            # 呼び出し等) の場合は、従来どおり bot_state.json から直接フォールバックする
            _fallback_state, comfy_port = read_bot_state(self._paths.runtime_root)
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

            # 実際の生成 (ComfyUIへのqueue_prompt) 直前でgateを取得する。取れなければ
            # (待機中にBotが停止された等でキャンセル扱いになった場合) は既存の
            # _CancelledError系フローに合流させる。
            lease = await self._gate.wait_acquire(
                "discord", cancel_check=self._cancel_requested.is_set,
            )
            if lease is None:
                raise _CancelledError()

            gen_start = _time.monotonic()
            try:
                # 割り込みの場合、実際に ComfyUI が生成を開始するまでプレビュー切替を遅らせる
                # (前の画像がまだ生成中のうちにプレビューが切り替わってしまうのを防ぐ)
                output_path = await self._generate_video(
                    image_path, comfy_port, on_started=None if not is_interrupt else _emit_started,
                    workflow=workflow,
                )
            finally:
                self._gate.release(lease)
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

    def _make_client(self, comfy_port: int) -> ComfyApiClient:
        """ComfyApiClient を生成する。テストでの差し替え用に切り出したフック。"""
        base_url = f"http://{COMFY_HOST}:{comfy_port}"
        return ComfyApiClient(base_url)

    async def _generate_video(
        self, image_path: Path, comfy_port: int, on_started=None, workflow: str | None = None,
    ) -> Path:
        model_preset = self._settings.model_preset
        preset_def = MODEL_PRESETS.get(model_preset, MODEL_PRESETS["normal"])

        # ジョブにワークフロー指定があれば、そのワークフロー用にサニタイズ済みの
        # テンプレートを使う (build_workflow_templates() で templates/<wf>.json へ生成済み)。
        # 無ければアプリでアクティブな runtime_template にフォールバックする。
        template = load_template_for_workflow(self._paths, workflow)

        job_id = uuid.uuid4().hex[:8]
        date_str = datetime.now().strftime("%Y%m%d")
        job_dir = self._paths.job_dir(job_id, date_str)
        job_dir.mkdir(parents=True, exist_ok=True)

        # アーカイブ用の入力画像コピー (ComfyUIへのアップロード用コピーは
        # GenerationExecutorが別名 (discord_<job_id><suffix>) で行う)
        input_copy = job_dir / ("input" + image_path.suffix)
        shutil.copy2(image_path, input_copy)

        client = self._make_client(comfy_port)
        self._current_comfy_client = client

        seed = random.randint(0, 2**32 - 1)
        req = GenerationRequest(
            owner="discord",
            job_id=job_id,
            input_image=image_path,
            job_dir=job_dir,
            template=template,
            seed=seed,
            unet_high_name=preset_def["unet_high"],
            unet_low_name=preset_def["unet_low"],
            sage_attention_mode="disabled",
            upload_basename=f"discord_{job_id}{image_path.suffix}",
            ready_timeout_sec=30,
        )

        stage_estimator = StageProgressEstimator(count_progress_stages(template))

        def _on_stage(stage: str) -> None:
            # 既存実装はqueue投入後・監視開始直前に0%「生成中...」を1回emitするのみ
            if stage == "generating":
                self._signals.job_progress.emit(0.0, tr("生成中..."))

        def _on_event(event) -> None:
            if on_started is not None and event.event_type == "execution_start":
                on_started()
            if event.event_type == "progress" and event.max_steps > 0:
                pct = stage_estimator.update(event.node_id, event.step, event.max_steps)
                self._signals.job_progress.emit(pct, tr("生成中... {pct}%").format(pct=int(pct)))

        try:
            logger.info("ComfyUI 接続確認: port=%d", comfy_port)
            try:
                result = await self._executor.run(
                    req, client,
                    on_stage=_on_stage,
                    on_event=_on_event,
                    cancel_check=self._cancel_requested.is_set,
                )
            except JobCancelledError as e:
                # 既存挙動を維持: Discord経路のキャンセルは_CancelledErrorで表現する
                raise _CancelledError() from e

            logger.info("生成完了: %s → %s", job_id, result.output_path)
            return result.output_path
        finally:
            self._current_comfy_client = None

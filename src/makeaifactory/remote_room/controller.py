"""RemoteRoomController — インターネット投入口 β のメインコントローラ。

Discord Bot Controller と同様の設計:
- デーモンスレッド + 独自 asyncio ループ
- Qt メインスレッドとは RemoteRoomSignals (QObject の Signal) 経由で通信
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import secrets
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal

from ..comfy.api_client import ComfyApiClient
from ..comfy.output_resolver import resolve_output_mp4
from ..comfy.workflow_patcher import WorkflowPatchContext, make_output_prefix, patch_workflow
from ..constants import COMFY_HOST, MODEL_PRESETS
from ..core.bot_state import read_bot_state
from ..core.paths import AppPaths
from ..core.settings_store import SettingsStore
from .auth import AuthManager
from .rate_limiter import RateLimiter
from .room_config import RemoteRoomConfig
from .room_server import RemoteJob, RoomServer, find_free_port
from .tunnel_manager import TunnelManager

logger = logging.getLogger(__name__)


class RemoteRoomSignals(QObject):
    status_changed  = Signal(str, str)   # status_code, display_message
    public_url_ready = Signal(str, str)  # url, pin
    job_started     = Signal(str, str)   # job_id, image_path
    job_progress    = Signal(str, float, str)  # job_id, pct, label
    job_done        = Signal(str, str)   # job_id, output_path
    job_error       = Signal(str, str)   # job_id, error_message
    stats_changed   = Signal(dict)       # {"queued": N, "running": N, "completed": N, "failed": N}
    error           = Signal(str)        # fatal error message


def _generate_pin() -> str:
    return f"{random.randint(0, 999999):06d}"


class RemoteRoomController:
    def __init__(self, settings: SettingsStore, paths: AppPaths) -> None:
        self._settings = settings
        self._paths = paths
        self._signals = RemoteRoomSignals()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._stop_event: asyncio.Event | None = None
        self._current_comfy_client: ComfyApiClient | None = None
        self._config: RemoteRoomConfig | None = None
        self._pin: str = ""
        self._ip_salt = secrets.token_hex(16)
        # サーバーとコントローラで共有するジョブ辞書とキュー（同一asyncioループ内）
        self._jobs: dict[str, RemoteJob] = {}
        self._job_queue: asyncio.Queue | None = None
        self._accepting = [True]  # ミュータブルフラグ（サーバー参照用）

    @property
    def signals(self) -> RemoteRoomSignals:
        return self._signals

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def pin(self) -> str:
        return self._pin

    def start(self, config: RemoteRoomConfig) -> None:
        if self.is_running:
            return
        self._config = config
        self._pin = _generate_pin() if config.require_pin else ""
        self._jobs.clear()
        self._accepting[0] = True
        self._running = True
        self._thread = threading.Thread(
            target=self._thread_main, daemon=True, name="RemoteRoomThread"
        )
        self._thread.start()
        logger.info("RemoteRoomController スレッド起動")

    def stop(self) -> None:
        self._running = False
        self._accepting[0] = False
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._loop and self._current_comfy_client:
            asyncio.run_coroutine_threadsafe(
                self._current_comfy_client.interrupt(), self._loop
            )
        logger.info("RemoteRoomController 停止要求")

    def stop_accepting(self) -> None:
        """新規リクエストの受付を停止する（現在の生成は続行）。"""
        self._accepting[0] = False

    def cancel_current_job(self) -> None:
        if self._loop and self._current_comfy_client:
            asyncio.run_coroutine_threadsafe(
                self._current_comfy_client.interrupt(), self._loop
            )

    def clear_queue(self) -> None:
        """キュー内の待機ジョブをすべてキャンセルする。"""
        if self._loop and self._job_queue:
            asyncio.run_coroutine_threadsafe(self._do_clear_queue(), self._loop)

    async def _do_clear_queue(self) -> None:
        if not self._job_queue:
            return
        cancelled_count = 0
        while not self._job_queue.empty():
            try:
                job_id = self._job_queue.get_nowait()
                job = self._jobs.get(job_id)
                if job:
                    job.status = "cancelled"
                    job.completed_at = datetime.now()
                cancelled_count += 1
            except asyncio.QueueEmpty:
                break
        logger.info("キュー消去: %d 件キャンセル", cancelled_count)
        self._emit_stats()

    def get_stats(self) -> dict:
        return {
            "queued": sum(1 for j in self._jobs.values() if j.status == "queued"),
            "running": sum(1 for j in self._jobs.values() if j.status == "running"),
            "completed": sum(1 for j in self._jobs.values() if j.status == "completed"),
            "failed": sum(1 for j in self._jobs.values() if j.status in ("failed", "cancelled")),
        }

    def _emit_stats(self) -> None:
        self._signals.stats_changed.emit(self.get_stats())

    # ── スレッドエントリポイント ──────────────────────────────────────────────

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._room_main())
        except Exception as e:
            if self._running:
                logger.exception("RemoteRoom 予期しないエラー")
                self._signals.status_changed.emit("error", f"エラー: {e}")
                self._signals.error.emit(str(e))
        finally:
            self._running = False
            self._loop.close()
            logger.info("RemoteRoomController スレッド終了")

    # ── メインコルーチン ──────────────────────────────────────────────────────

    async def _room_main(self) -> None:
        assert self._config is not None
        config = self._config
        self._stop_event = asyncio.Event()
        self._job_queue = asyncio.Queue(maxsize=config.max_queue_size * 2)

        self._signals.status_changed.emit("starting", "起動中...")

        # ポートを確保
        port = config.local_port or find_free_port(config.local_host)
        local_url = f"http://{config.local_host}:{port}"

        # ジョブ保存先ディレクトリを設定（サーバーがアクセスできるように）
        job_base_dir = self._paths.runtime_root / "remote_room" / "jobs"
        job_base_dir.mkdir(parents=True, exist_ok=True)
        config._job_base_dir = str(job_base_dir)  # type: ignore[attr-defined]

        # 認証マネージャとレートリミッタ
        auth = AuthManager(
            pin=self._pin,
            require_pin=config.require_pin,
            ttl_seconds=config.room_ttl_minutes * 60,
        )
        limiter = RateLimiter(cooldown_seconds=config.per_session_cooldown_seconds)

        # Room server (aiohttp)
        try:
            import aiohttp  # noqa: F401 — early availability check
        except ImportError:
            self._signals.status_changed.emit("error", "エラー: aiohttp が未インストールです")
            self._signals.error.emit(
                "aiohttp がインストールされていません。\n"
                "pip install aiohttp を実行してください。"
            )
            return

        server = RoomServer(
            config=config,
            auth_manager=auth,
            rate_limiter=limiter,
            jobs=self._jobs,
            job_queue=self._job_queue,
            static_dir=Path(__file__).parent / "static",
            ip_salt=self._ip_salt,
            on_stats_changed=lambda s: self._signals.stats_changed.emit(s),
            accepting_ref=self._accepting,
        )

        try:
            await server.start(config.local_host, port)
        except Exception as e:
            self._signals.status_changed.emit("error", f"サーバー起動失敗: {e}")
            self._signals.error.emit(f"ローカルサーバーの起動に失敗しました:\n{e}")
            return

        # cloudflared バイナリを確保 (なければ自動ダウンロード)
        from .cloudflared_installer import ensure_cloudflared

        def _dl_progress(msg: str, pct: float) -> None:
            self._signals.status_changed.emit("starting", msg)

        try:
            cloudflared_exe = await ensure_cloudflared(self._paths.runtime_root, _dl_progress)
        except Exception as e:
            self._signals.status_changed.emit("error", f"cloudflared 取得失敗: {e}")
            self._signals.error.emit(str(e))
            await server.stop()
            return

        self._signals.status_changed.emit("starting", "トンネルを起動中...")

        # Tunnel
        tunnel = TunnelManager()
        public_url: str | None = None
        try:
            public_url = await tunnel.start(port, exe_path=cloudflared_exe)
            self._signals.public_url_ready.emit(public_url, self._pin)
            self._signals.status_changed.emit("running", f"公開中: {public_url}")
            logger.info("投入口 公開: %s (PIN: %s)", public_url, "あり" if self._pin else "なし")
        except asyncio.TimeoutError:
            err = (
                "Cloudflare Quick Tunnel の起動に失敗しました。\n"
                "ネットワーク接続、セキュリティソフト、会社/学校のネットワーク制限を確認してください。"
            )
            self._signals.status_changed.emit("error", "Tunnel 起動タイムアウト")
            self._signals.error.emit(err)
            await server.stop()
            return
        except Exception as e:
            self._signals.status_changed.emit("error", f"Tunnel エラー: {e}")
            self._signals.error.emit(str(e))
            await server.stop()
            return

        # ジョブワーカー + TTL 監視
        worker_task = asyncio.create_task(self._job_worker())
        ttl_task = asyncio.create_task(self._ttl_watcher(config.room_ttl_minutes))

        # 停止シグナル待機
        await self._stop_event.wait()

        logger.info("RemoteRoom 停止シーケンス開始")
        worker_task.cancel()
        ttl_task.cancel()
        self._accepting[0] = False

        await tunnel.stop()
        await server.stop()

        # 出力ファイルの保持期間を考慮 (入力画像のみ即時削除)
        self._cleanup_inputs()
        self._signals.status_changed.emit("stopped", "停止しました")

    # ── ジョブワーカーループ ───────────────────────────────────────────────────

    async def _job_worker(self) -> None:
        while True:
            job_id = await self._job_queue.get()
            job = self._jobs.get(job_id)
            if job is None:
                self._job_queue.task_done()
                continue
            if job.status == "cancelled":
                self._job_queue.task_done()
                continue
            try:
                await self._process_job(job)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("RemoteRoom ジョブエラー: %s", job_id)
                job.status = "failed"
                job.completed_at = datetime.now()
                job.error_message = str(e)
                self._signals.job_error.emit(job_id, str(e))
                self._emit_stats()
            finally:
                self._job_queue.task_done()

    async def _process_job(self, job: RemoteJob) -> None:
        assert self._config is not None

        # フォルダバッチ生成中はリジェクト
        state, comfy_port = read_bot_state(self._paths.runtime_root)
        if state == "batch":
            job.status = "failed"
            job.completed_at = datetime.now()
            job.error_message = "現在フォルダ一括生成中のため受付できません。"
            self._emit_stats()
            return
        if comfy_port == 0:
            job.status = "failed"
            job.completed_at = datetime.now()
            job.error_message = "ComfyUI のポートが不明です。アプリを再起動してください。"
            self._emit_stats()
            return

        job.status = "running"
        job.progress_label = "生成準備中"
        self._signals.job_started.emit(job.job_id, job.image_path)
        self._emit_stats()

        try:
            output_path = await self._generate_video(
                job=job,
                comfy_port=comfy_port,
                on_progress=lambda pct, label: self._on_job_progress(job, pct, label),
            )
            job.status = "completed"
            job.output_path = str(output_path)
            job.video_url = f"/api/jobs/{job.job_id}/video"
            job.completed_at = datetime.now()
            job.progress_pct = 100.0
            job.progress_label = "完了"
            self._signals.job_done.emit(job.job_id, str(output_path))
            self._emit_stats()
            logger.info("RemoteRoom ジョブ完了: %s → %s", job.job_id, output_path)
        except Exception as e:
            job.status = "failed"
            job.completed_at = datetime.now()
            job.error_message = str(e)
            self._signals.job_error.emit(job.job_id, str(e))
            self._emit_stats()
            raise

    def _on_job_progress(self, job: RemoteJob, pct: float, label: str) -> None:
        job.progress_pct = pct
        job.progress_label = label
        self._signals.job_progress.emit(job.job_id, pct, label)

    # ── ComfyUI 動画生成 (DiscordBotController._generate_video と同様の構造) ──

    async def _generate_video(
        self,
        job: RemoteJob,
        comfy_port: int,
        on_progress: Callable[[float, str], None],
    ) -> Path:
        assert self._config is not None
        model_preset = self._settings.model_preset
        preset_def = MODEL_PRESETS.get(model_preset, MODEL_PRESETS["normal"])

        template_path = self._paths.runtime_template_json()
        if not template_path.exists():
            raise FileNotFoundError(f"ワークフローテンプレートが見つかりません: {template_path}")
        with template_path.open(encoding="utf-8") as f:
            template = json.load(f)

        job_dir = Path(job.image_path).parent
        base_url = f"http://{COMFY_HOST}:{comfy_port}"
        client = ComfyApiClient(base_url)
        self._current_comfy_client = client

        try:
            on_progress(0.0, "ComfyUI 接続中...")
            await client.wait_until_ready(timeout_sec=30)

            upload_src = job_dir / f"remote_{job.job_id}.png"
            shutil.copy2(job.image_path, upload_src)
            on_progress(5.0, "画像アップロード中...")
            uploaded_name = await client.upload_image(upload_src)

            seed = random.randint(0, 2**32 - 1)
            ctx = WorkflowPatchContext(
                job_id=job.job_id,
                uploaded_image_name=uploaded_name,
                output_prefix=make_output_prefix(job.job_id),
                seed=seed,
                unet_high_name=preset_def["unet_high"],
                unet_low_name=preset_def["unet_low"],
                sage_attention_mode="disabled",
            )
            patched = patch_workflow(template, ctx)

            on_progress(8.0, "生成キュー追加中...")
            prompt_id = await client.queue_prompt(patched)
            logger.info("RemoteRoom 生成開始: job=%s prompt=%s", job.job_id, prompt_id)

            on_progress(10.0, "生成中...")
            async for event in client.watch_progress(prompt_id):
                if event.event_type == "progress" and event.max_steps > 0:
                    pct = 10.0 + (event.step / event.max_steps) * 80.0
                    on_progress(pct, f"生成中... {int(pct)}%")
                elif event.event_type == "execution_error":
                    break

            on_progress(92.0, "動画を保存中...")
            history = await client.get_history(prompt_id)
            output_mp4 = resolve_output_mp4(history, prompt_id, self._paths.comfyui_output_dir, job.job_id)

            final_output = job_dir / "output.mp4"
            shutil.copy2(output_mp4, final_output)

            with (job_dir / "job.json").open("w", encoding="utf-8") as f:
                json.dump({
                    "job_id": job.job_id,
                    "status": "completed",
                    "created_at": job.created_at.isoformat(),
                    "completed_at": datetime.now().isoformat(),
                }, f, ensure_ascii=False, indent=2)

            on_progress(100.0, "完了")
            return final_output
        finally:
            self._current_comfy_client = None

    # ── TTL 監視 ──────────────────────────────────────────────────────────────

    async def _ttl_watcher(self, ttl_minutes: int) -> None:
        await asyncio.sleep(ttl_minutes * 60)
        logger.info("ルーム有効期限切れ — 停止します")
        self._accepting[0] = False
        self._signals.status_changed.emit("stopped", "有効期限切れで停止しました")
        if self._stop_event:
            self._stop_event.set()

    # ── クリーンアップ ────────────────────────────────────────────────────────

    def _cleanup_inputs(self) -> None:
        """入力画像を即時削除する。出力動画は保持期間まで残す。"""
        for job in self._jobs.values():
            if job.image_path:
                try:
                    Path(job.image_path).unlink(missing_ok=True)
                except Exception:
                    pass
        logger.info("RemoteRoom: 入力画像を削除しました")

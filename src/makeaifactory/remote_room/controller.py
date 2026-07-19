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
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

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
    # 総当たり耐性のため暗号学的乱数で生成する
    return f"{secrets.randbelow(1000000):06d}"


class RemoteRoomController:
    def __init__(
        self,
        settings: SettingsStore,
        paths: AppPaths,
        gate: GenerationGate | None = None,
        owner: str = "remote",
        executor: GenerationExecutor | None = None,
    ) -> None:
        self._settings = settings
        self._paths = paths
        # 生成admissionゲート。app.py/AppController から共有インスタンスを受け取るのが
        # 通常経路。未指定 (単体テスト等) の場合はbot_stateミラー無しの単独インスタンス。
        self._gate = gate if gate is not None else GenerationGate(None)
        # インターネット投入口 (owner="remote") とローカルブリッジ (owner="bridge") は
        # 同じ RemoteRoomController 実装を使い回すため、gate上のowner名で区別する。
        self._owner = owner
        # SCH-01 PR4: gate取得(submit)〜release、request_cancelによる照合キャンセルまで
        # 一括で担う共有executor。app.py/AppController から共有インスタンスを受け取るのが
        # 通常経路。未指定 (単体テスト等) の場合は上のgateと組にした自前のexecutorを持つ。
        self._executor = executor if executor is not None else GenerationExecutor(paths, self._gate)
        self._signals = RemoteRoomSignals()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._stop_event: asyncio.Event | None = None
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
        # SCH-01 PR4: 実行中ジョブも止める。executor.request_cancel は
        # 実行中レジストリと owner=self._owner を照合できた時だけ interrupt を
        # 発行するため、他経路のジョブ実行中に誤って止めてしまうことがない
        # (従来の self._current_comfy_client.interrupt() 無条件発行を置き換え)。
        # request_cancel はどのスレッドからでも呼べるため、専用スレッドの
        # イベントループ (self._loop) 経由にする必要はない。
        self._executor.request_cancel(self._owner)
        logger.info("RemoteRoomController 停止要求")

    def stop_accepting(self) -> None:
        """新規リクエストの受付を停止する（現在の生成は続行）。"""
        self._accepting[0] = False

    def cancel_current_job(self) -> None:
        """現在実行中の ComfyUI ジョブをキャンセルする。

        SCH-01 PR4: owner=self._owner のジョブが実際に実行中の時だけ
        executor.request_cancel が interrupt を発行する (誤爆防止)。
        """
        self._executor.request_cancel(self._owner)

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
                self._signals.status_changed.emit("error", tr("エラー: {e}").format(e=e))
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

        self._signals.status_changed.emit("starting", tr("起動中..."))

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
            self._signals.status_changed.emit("error", tr("エラー: aiohttp が未インストールです"))
            self._signals.error.emit(tr(
                "aiohttp がインストールされていません。\n"
                "pip install aiohttp を実行してください。"
            ))
            return

        import sys
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            # PyInstaller バンドル: _MEIPASS 内の固定パスを使用
            static_dir = Path(sys._MEIPASS) / "makeaifactory" / "remote_room" / "static"
        else:
            # 開発モード: ソースファイルからの相対パス
            static_dir = Path(__file__).resolve().parent / "static"
        logger.info("static_dir: %s (exists: %s, index: %s)",
                    static_dir, static_dir.exists(), (static_dir / "index.html").exists())

        server = RoomServer(
            config=config,
            auth_manager=auth,
            rate_limiter=limiter,
            jobs=self._jobs,
            job_queue=self._job_queue,
            static_dir=static_dir,
            ip_salt=self._ip_salt,
            on_stats_changed=lambda s: self._signals.stats_changed.emit(s),
            accepting_ref=self._accepting,
        )

        try:
            await server.start(config.local_host, port)
        except Exception as e:
            self._signals.status_changed.emit("error", tr("サーバー起動失敗: {e}").format(e=e))
            self._signals.error.emit(tr("ローカルサーバーの起動に失敗しました:\n{e}").format(e=e))
            return

        # === Tunnel(インターネット投入口)経路。ブラウザ連携(tunnel_enabled=False)では張らない ===
        tunnel: TunnelManager | None = None
        if not config.tunnel_enabled:
            # ローカルブリッジ: トンネルを張らずローカルのみ待受 (ブラウザ連携用)
            self._signals.status_changed.emit(
                "running",
                tr("ブラウザ連携: ローカル待受中 (127.0.0.1:{port})").format(port=port),
            )
            logger.info("ローカルブリッジ 待受開始: 127.0.0.1:%d", port)
        else:
            # cloudflared バイナリを確保 (なければ自動ダウンロード)
            from .cloudflared_installer import ensure_cloudflared

            def _dl_progress(msg: str, pct: float) -> None:
                self._signals.status_changed.emit("starting", msg)

            try:
                cloudflared_exe = await ensure_cloudflared(self._paths.runtime_root, _dl_progress)
            except Exception as e:
                self._signals.status_changed.emit("error", tr("cloudflared 取得失敗: {e}").format(e=e))
                self._signals.error.emit(str(e))
                await server.stop()
                return

            self._signals.status_changed.emit("starting", tr("トンネルを起動中..."))

            tunnel = TunnelManager()
            public_url: str | None = None
            try:
                public_url = await tunnel.start(port, exe_path=cloudflared_exe)
                self._signals.public_url_ready.emit(public_url, self._pin)
                self._signals.status_changed.emit("running", tr("公開中: {public_url}").format(public_url=public_url))
                logger.info("投入口 公開: %s (PIN: %s)", public_url, "あり" if self._pin else "なし")
            except asyncio.TimeoutError:
                err = tr(
                    "Cloudflare Quick Tunnel の起動に失敗しました。\n"
                    "ネットワーク接続、セキュリティソフト、会社/学校のネットワーク制限を確認してください。"
                )
                self._signals.status_changed.emit("error", tr("Tunnel 起動タイムアウト"))
                self._signals.error.emit(err)
                await server.stop()
                return
            except Exception as e:
                self._signals.status_changed.emit("error", tr("Tunnel エラー: {e}").format(e=e))
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

        if tunnel is not None:
            await tunnel.stop()
        await server.stop()

        # 出力ファイルの保持期間を考慮 (入力画像のみ即時削除)
        self._cleanup_inputs()
        self._signals.status_changed.emit("stopped", tr("停止しました"))

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

        try:
            # フォルダバッチ生成中はリジェクト
            if self._gate.batch_active:
                job.status = "failed"
                job.completed_at = datetime.now()
                job.error_message = "現在フォルダ一括生成中のため受付できません。"
                self._emit_stats()
                return
            comfy_port = self._gate.comfy_port
            if comfy_port == 0:
                # gate.set_comfy_port() が未実行 (単体テスト等) の場合は、従来どおり
                # bot_state.json から直接フォールバックする
                _fallback_state, comfy_port = read_bot_state(self._paths.runtime_root)
            if comfy_port == 0:
                job.status = "failed"
                job.completed_at = datetime.now()
                job.error_message = "ComfyUI のポートが不明です。アプリを再起動してください。"
                self._emit_stats()
                return

            # SCH-01 PR4: gate取得(wait_acquire)〜release は _generate_video 内の
            # executor.submit() へ集約した (以前はここで個別にwait_acquire/releaseして
            # いた)。取得待ち中・生成中いずれのキャンセルもJobCancelledErrorとして
            # 届くため、下のexcept節で統一的に「cancelled」状態として扱う。
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
            except JobCancelledError:
                # gate取得待ち中・生成中いずれのキャンセルもここに合流する。
                # _job_worker側の汎用except Exceptionでfailed扱いに上書きされない
                # よう、ここで完結させ再送出しない。
                job.status = "cancelled"
                job.completed_at = datetime.now()
                job.error_message = "生成がキャンセルされました。"
                self._emit_stats()
                logger.info("RemoteRoom ジョブキャンセル: %s", job.job_id)
            except Exception as e:
                job.status = "failed"
                job.completed_at = datetime.now()
                job.error_message = str(e)
                self._signals.job_error.emit(job.job_id, str(e))
                self._emit_stats()
                raise
        finally:
            # RET-01: 完了・失敗・キャンセル・早期リジェクトいずれの経路を通っても、
            # 入力画像(input.png)とComfyUIアップロード用コピー(remote_<id>.png)は
            # ここで都度削除する。output.mp4/job.jsonは保持期間(cleanup_remote_jobs)
            # まで残す (ブラウザが /api/jobs/<id>/video で output.mp4 を取得するため)。
            self._cleanup_job_inputs(job)

    def _on_job_progress(self, job: RemoteJob, pct: float, label: str) -> None:
        job.progress_pct = pct
        job.progress_label = label
        self._signals.job_progress.emit(job.job_id, pct, label)

    # ── ComfyUI 動画生成 (DiscordBotController._generate_video と同様の構造) ──

    def _make_client(self, comfy_port: int) -> ComfyApiClient:
        """ComfyApiClient を生成する。テストでの差し替え用に切り出したフック。"""
        base_url = f"http://{COMFY_HOST}:{comfy_port}"
        return ComfyApiClient(base_url)

    async def _generate_video(
        self,
        job: RemoteJob,
        comfy_port: int,
        on_progress: Callable[[float, str], None],
    ) -> Path:
        assert self._config is not None
        model_preset = self._settings.model_preset
        preset_def = MODEL_PRESETS.get(model_preset, MODEL_PRESETS["normal"])

        # ジョブにワークフロー指定があれば、そのワークフロー用にサニタイズ済みの
        # テンプレートを使う (ローカルブリッジ起動時に templates/<wf>.json へ生成済み)。
        # 無ければアプリでアクティブな runtime_template にフォールバックする。
        template = load_template_for_workflow(self._paths, job.workflow)

        job_dir = Path(job.image_path).parent
        client = self._make_client(comfy_port)

        seed = random.randint(0, 2**32 - 1)
        req = GenerationRequest(
            owner=self._owner,
            job_id=job.job_id,
            input_image=Path(job.image_path),
            job_dir=job_dir,
            template=template,
            seed=seed,
            unet_high_name=preset_def["unet_high"],
            unet_low_name=preset_def["unet_low"],
            sage_attention_mode="disabled",
            upload_basename=f"remote_{job.job_id}.png",
            ready_timeout_sec=30,
        )

        stage_estimator = StageProgressEstimator(count_progress_stages(template))

        def _on_stage(stage: str) -> None:
            if stage == "connecting":
                on_progress(0.0, tr("ComfyUI 接続中..."))
            elif stage == "uploading":
                on_progress(5.0, tr("画像アップロード中..."))
            elif stage == "queueing":
                on_progress(8.0, tr("生成キュー追加中..."))
            elif stage == "generating":
                on_progress(10.0, tr("生成中..."))
            elif stage == "resolving":
                on_progress(92.0, tr("動画を保存中..."))

        def _on_event(event) -> None:
            if event.event_type == "progress" and event.max_steps > 0:
                stage_pct = stage_estimator.update(event.node_id, event.step, event.max_steps)
                pct = 10.0 + stage_pct * 0.80
                on_progress(pct, tr("生成中... {pct}%").format(pct=int(pct)))

        # SCH-01 PR4: gate取得(wait_acquire)〜release をここへ集約する (submit()。
        # 以前は_process_job側で個別にwait_acquire/releaseしていた)。cancel_check は
        # PR1相当 (job.status=="cancelled" またはコントローラ停止) をそのまま渡す。
        result = await self._executor.submit(
            req, client, on_stage=_on_stage, on_event=_on_event,
            cancel_check=lambda: job.status == "cancelled" or not self._running,
        )

        final_output = result.output_path
        with (job_dir / "job.json").open("w", encoding="utf-8") as f:
            json.dump({
                "job_id": job.job_id,
                "status": "completed",
                "created_at": job.created_at.isoformat(),
                "completed_at": datetime.now().isoformat(),
            }, f, ensure_ascii=False, indent=2)

        on_progress(100.0, tr("完了"))
        return final_output

    # ── TTL 監視 ──────────────────────────────────────────────────────────────

    async def _ttl_watcher(self, ttl_minutes: int) -> None:
        await asyncio.sleep(ttl_minutes * 60)
        logger.info("ルーム有効期限切れ — 停止します")
        self._accepting[0] = False
        self._signals.status_changed.emit("stopped", tr("有効期限切れで停止しました"))
        if self._stop_event:
            self._stop_event.set()

    # ── クリーンアップ ────────────────────────────────────────────────────────

    def _cleanup_job_inputs(self, job: "RemoteJob") -> None:
        """RET-01: 1ジョブ分の入力画像(input.png)とComfyUIアップロード用コピー
        (remote_<job_id>.png)を削除する。`_process_job` の完了/失敗/キャンセル/
        早期リジェクトいずれの経路からも finally 経由で呼ばれる (都度削除)。
        output.mp4/job.json は保持期間 (cleanup_remote_jobs) まで残すため
        ここでは触らない。失敗してもジョブ処理自体には影響させない (ログのみ)。
        """
        if not job.image_path:
            return
        try:
            input_path = Path(job.image_path)
            input_path.unlink(missing_ok=True)
            upload_copy = input_path.parent / f"remote_{job.job_id}.png"
            upload_copy.unlink(missing_ok=True)
        except Exception:
            logger.warning("RemoteRoom: ジョブ入力の削除に失敗しました: %s", job.job_id, exc_info=True)

    def _cleanup_inputs(self) -> None:
        """停止時: 未処理分も含め残っている入力画像を即時削除する。出力動画は保持期間まで残す。

        通常は `_process_job` の finally (`_cleanup_job_inputs`) で都度削除
        されるが、キュー投入されたまま処理されなかったジョブの入力が
        残る可能性があるため、停止時にまとめて掃除する保険。
        """
        for job in self._jobs.values():
            if job.image_path:
                try:
                    Path(job.image_path).unlink(missing_ok=True)
                except Exception:
                    pass
        logger.info("RemoteRoom: 入力画像を削除しました")

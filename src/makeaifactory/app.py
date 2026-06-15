from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QApplication

from .constants import APP_NAME
from .core.app_controller import AppController
from .core.install_config import load_runtime_config, save_runtime_config
from .core.log_manager import setup_logging
from .core.paths import AppPaths, _exe_dir
from .core.settings_store import SettingsStore
from .domain.errors import MakeAiFactoryError, SystemUnsupportedError
from .domain.progress import JobProgress, JobState, SetupProgress, SetupState
from .gui.first_run_dialog import FirstRunDialog
from .gui.install_location_dialog import InstallLocationDialog
from .gui.main_window import MainWindow

logger = logging.getLogger(__name__)


class _AsyncSignals(QObject):
    setup_progress = Signal(SetupProgress)
    job_progress = Signal(JobProgress)
    job_done = Signal(Path)
    error = Signal(str, str, str, bool)
    app_quit = Signal()


class _Worker(QRunnable):
    def __init__(self, coro, signals: _AsyncSignals):
        super().__init__()
        self._coro = coro
        self._signals = signals

    def run(self):
        asyncio.run(self._coro)


def run_app() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")

    exe_dir = _exe_dir()

    # インストール先の決定（初回は選択ダイアログ、2回目以降は設定ファイルから読む）
    runtime_root = load_runtime_config(exe_dir)
    if runtime_root is None:
        # デフォルト候補: exe横(ASCII)か安全なフォールバックを提案
        try:
            from .core.paths import get_runtime_root
            default_candidate = get_runtime_root()
        except RuntimeError:
            default_candidate = Path("C:/makeAiFactory/runtime")

        dlg = InstallLocationDialog(default_candidate)
        if dlg.exec() != InstallLocationDialog.DialogCode.Accepted:
            return 0
        runtime_root = dlg.chosen_path()
        save_runtime_config(exe_dir, runtime_root)

    paths = AppPaths(runtime_root=runtime_root)
    paths.ensure_dirs()
    setup_logging(paths.logs_dir)

    settings = SettingsStore(paths.runtime_root / "settings.json")

    if not settings.agreed_to_terms:
        dlg = FirstRunDialog()
        if dlg.exec() != FirstRunDialog.DialogCode.Accepted:
            return 0
        settings.agree_to_terms()

    ctrl = AppController(paths, settings)
    window = MainWindow()
    window.set_paths(paths.logs_dir, paths.outputs_dir)
    window.set_repair_callback(lambda: _trigger_repair(ctrl, window, paths, settings))

    def _change_install_location() -> None:
        from PySide6.QtWidgets import QMessageBox
        dlg = InstallLocationDialog(runtime_root, window)
        if dlg.exec() == InstallLocationDialog.DialogCode.Accepted:
            new_path = dlg.chosen_path()
            save_runtime_config(exe_dir, new_path)
            QMessageBox.information(
                window,
                "インストール場所を変更しました",
                f"次回起動時に以下の場所を使用します:\n{new_path}\n\nアプリを再起動してください。",
            )

    window.set_change_location_callback(_change_install_location)
    signals = _AsyncSignals()

    @Slot(SetupProgress)
    def _on_setup_progress(p: SetupProgress) -> None:
        if p.state == SetupState.READY:
            window.show_drop_page()
        elif p.state == SetupState.FAILED:
            pass
        else:
            pct = p.percent
            window.show_progress(p.message, pct, p.detail)
            if pct == 0:
                window.show_progress_indeterminate(p.message)

    @Slot(JobProgress)
    def _on_job_progress(p: JobProgress) -> None:
        if p.state == JobProgress.__class__:
            pass
        window.show_progress(p.message, p.percent)

    @Slot(Path)
    def _on_job_done(output: Path) -> None:
        window.show_result(output)

    @Slot(str, str, str, bool)
    def _on_error(title: str, msg: str, detail: str, show_repair: bool) -> None:
        window.show_drop_page()
        window.show_error(title, msg, detail, show_repair)

    signals.setup_progress.connect(_on_setup_progress, Qt.ConnectionType.QueuedConnection)
    signals.job_progress.connect(_on_job_progress, Qt.ConnectionType.QueuedConnection)
    signals.job_done.connect(_on_job_done, Qt.ConnectionType.QueuedConnection)
    signals.error.connect(_on_error, Qt.ConnectionType.QueuedConnection)
    signals.app_quit.connect(app.quit, Qt.ConnectionType.QueuedConnection)

    async def _check_and_apply_update() -> None:
        """GitHub 最新リリースを確認し、新しいバージョンがあれば自動ダウンロード・適用・再起動する。"""
        if not getattr(sys, "frozen", False):
            return
        try:
            from .core.updater import apply_update_and_restart, check_for_update, download_update

            release = await asyncio.wait_for(check_for_update(), timeout=5.0)
            if release is None:
                return

            def _upd_pct(pct: float) -> None:
                signals.setup_progress.emit(SetupProgress(
                    state=SetupState.DOWNLOADING_MODELS,
                    message=f"v{release.version} をダウンロード中... {pct * 100:.0f}%",
                    percent=pct * 100,
                ))

            signals.setup_progress.emit(SetupProgress(
                state=SetupState.DOWNLOADING_MODELS,
                message=f"新しいバージョン v{release.version} があります。ダウンロード中...",
                percent=0,
            ))

            zip_path = await download_update(release, progress_cb=_upd_pct)

            signals.setup_progress.emit(SetupProgress(
                state=SetupState.DOWNLOADING_MODELS,
                message="アップデートを適用して再起動します...",
                percent=100,
            ))

            apply_update_and_restart(zip_path)
            signals.app_quit.emit()

        except asyncio.TimeoutError:
            logger.debug("アップデート確認タイムアウト (5秒)")
        except Exception as e:
            logger.debug("アップデート確認スキップ: %s", e)

    async def _run_setup():
        await _check_and_apply_update()

        def _cb(p: SetupProgress):
            signals.setup_progress.emit(p)
        try:
            await ctrl.ensure_ready(on_progress=_cb)
            window.set_system_info(ctrl.get_system_info_text())
            signals.setup_progress.emit(SetupProgress(state=SetupState.READY, message="準備完了"))
        except SystemUnsupportedError as e:
            signals.error.emit("対応環境外", str(e), "", False)
        except MakeAiFactoryError as e:
            signals.error.emit("セットアップ失敗", str(e), "", True)
        except Exception as e:
            logger.exception("セットアップ中に予期しないエラー")
            signals.error.emit("セットアップ失敗", str(e), "", True)

    async def _run_job(image_path: Path):
        def _cb(p: JobProgress):
            signals.job_progress.emit(p)
        try:
            job_ctrl = ctrl.get_job_controller()
            output = await job_ctrl.run_job(image_path, on_progress=_cb)
            signals.job_done.emit(output)
        except MakeAiFactoryError as e:
            signals.error.emit("生成失敗", str(e), "", True)
        except Exception as e:
            logger.exception("生成中に予期しないエラー")
            signals.error.emit("生成失敗", str(e), "", False)

    @Slot(Path)
    def _on_image_dropped(path: Path) -> None:
        window.show_progress_indeterminate("生成を準備しています...")
        pool = QThreadPool.globalInstance()
        pool.start(_Worker(_run_job(path), signals))

    window.image_dropped.connect(_on_image_dropped, Qt.ConnectionType.QueuedConnection)

    window.show_progress_indeterminate("セットアップを確認しています...")
    window.show()

    pool = QThreadPool.globalInstance()
    pool.start(_Worker(_run_setup(), signals))

    result = app.exec()
    ctrl.stop_server()
    return result


def _trigger_repair(ctrl: AppController, window: MainWindow, paths: AppPaths, settings: SettingsStore) -> None:
    from .runtime.repair_manager import RepairManager
    from .runtime.runtime_state import RuntimeState
    state = RuntimeState(paths.runtime_root)
    repair = RepairManager(paths.runtime_root, state)
    repair.reset_custom_nodes()
    window.show_progress_indeterminate("修復中...")
    signals = _AsyncSignals()

    async def _repair():
        try:
            ctrl2 = AppController(paths, settings)
            await ctrl2.setup()
            signals.setup_progress.emit(SetupProgress(state=SetupState.READY, message="修復完了"))
        except Exception as e:
            signals.error.emit("修復失敗", str(e), "", False)

    pool = QThreadPool.globalInstance()
    pool.start(_Worker(_repair(), signals))

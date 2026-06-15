from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox

from .constants import APP_NAME, SUPPORTED_IMAGE_EXTENSIONS
from .core.app_controller import AppController
from .core.install_config import load_runtime_config, save_runtime_config
from .core.log_manager import setup_logging
from .core.paths import AppPaths, _exe_dir
from .core.settings_store import SettingsStore
from .domain.errors import MakeAiFactoryError, SystemUnsupportedError
from .domain.progress import JobProgress, JobState, SetupProgress, SetupState
from .gui.batch_dialog import BatchDialog
from .gui.first_run_dialog import FirstRunDialog
from .gui.install_location_dialog import InstallLocationDialog
from .gui.main_window import MainWindow

logger = logging.getLogger(__name__)


class _AsyncSignals(QObject):
    setup_progress = Signal(SetupProgress)
    job_progress   = Signal(JobProgress)
    job_done       = Signal(Path, str, float)   # output, source_stem, elapsed_sec
    batch_progress = Signal(str, float, str)     # message, pct, detail
    batch_done     = Signal(int, int, float)     # completed, total, elapsed_sec
    error          = Signal(str, str, str, bool)
    app_quit       = Signal()


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

    # インストール先の決定
    runtime_root = load_runtime_config(exe_dir)
    if runtime_root is None:
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

    # バッチキャンセル用フラグ（スレッドセーフ）
    _batch_cancel = threading.Event()

    # ─────────────────────────────────────────────────────────────────────
    # Slots
    # ─────────────────────────────────────────────────────────────────────

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
        window.show_progress(p.message, p.percent)

    @Slot(Path, str, float)
    def _on_job_done(output: Path, source_stem: str, elapsed_sec: float) -> None:
        window.show_result(output, source_stem, elapsed_sec)

    @Slot(str, float, str)
    def _on_batch_progress(message: str, pct: float, detail: str) -> None:
        window.show_progress(message, pct, detail)

    @Slot(int, int, float)
    def _on_batch_done(completed: int, total: int, elapsed_sec: float) -> None:
        window.stop_elapsed_timer()
        window.hide_cancel_btn()
        mins = int(elapsed_sec // 60)
        secs = int(elapsed_sec % 60)
        time_str = f"{mins}分{secs}秒" if mins > 0 else f"{secs}秒"
        cancelled = _batch_cancel.is_set()
        if cancelled:
            title = "バッチ処理を中断しました"
            msg = f"{completed}/{total}枚 を処理して中断しました\n経過時間: {time_str}"
        else:
            title = "バッチ処理が完了しました"
            msg = f"{completed}枚 の動画を生成しました\n経過時間: {time_str}"
        QMessageBox.information(window, title, msg)
        window.show_drop_page()

    @Slot(str, str, str, bool)
    def _on_error(title: str, msg: str, detail: str, show_repair: bool) -> None:
        window.stop_elapsed_timer()
        window.hide_cancel_btn()
        window.show_drop_page()
        window.show_error(title, msg, detail, show_repair)

    signals.setup_progress.connect(_on_setup_progress, Qt.ConnectionType.QueuedConnection)
    signals.job_progress.connect(_on_job_progress,  Qt.ConnectionType.QueuedConnection)
    signals.job_done.connect(_on_job_done,           Qt.ConnectionType.QueuedConnection)
    signals.batch_progress.connect(_on_batch_progress, Qt.ConnectionType.QueuedConnection)
    signals.batch_done.connect(_on_batch_done,       Qt.ConnectionType.QueuedConnection)
    signals.error.connect(_on_error,                 Qt.ConnectionType.QueuedConnection)
    signals.app_quit.connect(app.quit,               Qt.ConnectionType.QueuedConnection)

    # ─────────────────────────────────────────────────────────────────────
    # Async tasks
    # ─────────────────────────────────────────────────────────────────────

    async def _check_and_apply_update() -> None:
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

    async def _run_setup() -> None:
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

    async def _run_job(image_path: Path) -> None:
        start = time.monotonic()

        def _cb(p: JobProgress):
            signals.job_progress.emit(p)
        try:
            job_ctrl = ctrl.get_job_controller()
            output = await job_ctrl.run_job(image_path, on_progress=_cb)
            elapsed = time.monotonic() - start
            signals.job_done.emit(output, image_path.stem, elapsed)
        except MakeAiFactoryError as e:
            signals.error.emit("生成失敗", str(e), "", True)
        except Exception as e:
            logger.exception("生成中に予期しないエラー")
            signals.error.emit("生成失敗", str(e), "", False)

    async def _run_batch(input_folder: Path, output_folder: Path) -> None:
        images = sorted(
            p for p in input_folder.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
        )
        total = len(images)
        if total == 0:
            signals.error.emit("画像が見つかりません", f"{input_folder} に対応画像がありません", "", False)
            return

        end_dir = input_folder / "end"
        end_dir.mkdir(exist_ok=True)
        output_folder.mkdir(parents=True, exist_ok=True)

        start = time.monotonic()
        completed = 0

        for i, image_path in enumerate(images):
            if _batch_cancel.is_set():
                break

            def _cb(p: JobProgress, idx: int = i, name: str = image_path.name) -> None:
                outer_pct = (idx + p.percent / 100) / total * 100
                signals.batch_progress.emit(
                    f"バッチ処理 ({idx + 1}/{total}): {name}",
                    outer_pct,
                    p.message,
                )

            try:
                job_ctrl = ctrl.get_job_controller()
                output = await job_ctrl.run_job(image_path, on_progress=_cb)
                shutil.move(str(image_path), str(end_dir / image_path.name))
                shutil.copy2(str(output), str(output_folder / f"{image_path.stem}.mp4"))
                completed += 1
            except Exception as e:
                logger.error("バッチ処理エラー (%s): %s", image_path.name, e)

        elapsed = time.monotonic() - start
        signals.batch_done.emit(completed, total, elapsed)

    async def _cancel_current_job() -> None:
        try:
            job_ctrl = ctrl.get_job_controller()
            await job_ctrl.cancel_current()
        except Exception as e:
            logger.debug("ジョブキャンセル失敗: %s", e)

    # ─────────────────────────────────────────────────────────────────────
    # UI event handlers
    # ─────────────────────────────────────────────────────────────────────

    @Slot(Path)
    def _on_image_dropped(path: Path) -> None:
        window.show_progress_indeterminate("生成を準備しています...")
        window.start_elapsed_timer()
        pool = QThreadPool.globalInstance()
        pool.start(_Worker(_run_job(path), signals))

    @Slot()
    def _on_batch_requested() -> None:
        dlg = BatchDialog(parent=window)
        if dlg.exec() != BatchDialog.DialogCode.Accepted:
            return
        input_folder = dlg.input_folder()
        output_folder = dlg.output_folder()

        _batch_cancel.clear()

        def _do_cancel() -> None:
            _batch_cancel.set()
            window.update_status("中断中...")
            pool = QThreadPool.globalInstance()
            pool.start(_Worker(_cancel_current_job(), signals))

        window.show_progress_indeterminate(f"バッチ処理を開始しています...")
        window.start_elapsed_timer()
        window.show_cancel_btn(_do_cancel)

        pool = QThreadPool.globalInstance()
        pool.start(_Worker(_run_batch(input_folder, output_folder), signals))

    window.image_dropped.connect(_on_image_dropped, Qt.ConnectionType.QueuedConnection)
    window.batch_requested.connect(_on_batch_requested, Qt.ConnectionType.QueuedConnection)

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

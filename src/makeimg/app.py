from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QUrl, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from .gui.icon_data import app_icon

from .constants import APP_NAME
from .core.app_controller import AppController
from .core.install_config import load_runtime_config, save_runtime_config
from .core.log_manager import setup_logging
from .core.paths import AppPaths, _exe_dir
from .core.settings_store import SettingsStore
from .domain.errors import MakeAiFactoryError, SystemUnsupportedError
from .domain.progress import JobProgress, JobState, SetupProgress, SetupState

from .gui.main_window import MainWindow

logger = logging.getLogger(__name__)


def _job_overall_pct(p: JobProgress) -> float:
    if p.state == JobState.QUEUED:
        return 5.0
    if p.state == JobState.GENERATING:
        return 10.0 + p.percent * 0.80
    if p.state == JobState.RESOLVING_OUTPUT:
        return 92.0
    if p.state == JobState.COMPLETED:
        return 100.0
    return 0.0


class _AsyncSignals(QObject):
    setup_progress = Signal(SetupProgress)
    setup_ready = Signal(str, list, str, bool)
    job_progress = Signal(JobProgress)
    job_done = Signal(Path, float)
    job_cancelled = Signal()
    batch_progress = Signal(str, float)
    batch_done = Signal(int, int, float)
    error = Signal(str, str, str, bool)
    app_quit = Signal()
    preview_image = Signal(object)


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
    app.setWindowIcon(app_icon())

    exe_dir = _exe_dir()

    runtime_root = load_runtime_config(exe_dir)
    if runtime_root is None:
        try:
            from .core.paths import get_runtime_root
            default_candidate = get_runtime_root()
        except RuntimeError:
            default_candidate = Path("C:/makeImg/runtime")

        from .gui.first_run_dialog_stub import ask_install_location
        chosen = ask_install_location(default_candidate)
        if chosen is None:
            return 0
        runtime_root = chosen
        save_runtime_config(exe_dir, runtime_root)

    paths = AppPaths(runtime_root=runtime_root)
    paths.ensure_dirs()
    setup_logging(paths.logs_dir)

    settings = SettingsStore(paths.runtime_root / "settings.json")

    if not settings.agreed_to_terms:
        from .gui.first_run_dialog_stub import show_terms_dialog
        if not show_terms_dialog():
            return 0
        settings.agree_to_terms()

    ctrl = AppController(paths, settings)
    window = MainWindow()
    window.set_paths(paths.logs_dir, paths.outputs_dir)

    window.set_prompts_without_signal(settings.positive_prompt, settings.negative_prompt)
    window.set_resolution(settings.width, settings.height)
    window.set_seed_mode(settings.seed_mode, settings.seed_value)
    window.update_workflow_list(ctrl.list_workflows(), settings.active_workflow)
    window.update_preset_list(settings.prompt_presets, settings.last_preset)

    last_preset_name = settings.last_preset
    if last_preset_name:
        for p in settings.prompt_presets:
            if p.get("name") == last_preset_name:
                window.set_prompts_without_signal(p.get("positive", ""), p.get("negative", ""))
                settings.set_positive_prompt(p.get("positive", ""))
                settings.set_negative_prompt(p.get("negative", ""))
                break

    def _on_prompt_changed(positive: str, negative: str) -> None:
        settings.set_positive_prompt(positive)
        settings.set_negative_prompt(negative)

    def _on_preset_save_requested(name: str, positive: str, negative: str) -> None:
        settings.add_prompt_preset(name, positive, negative)
        settings.set_last_preset(name)
        window.update_preset_list(settings.prompt_presets, name)
        window.update_status(f"プリセット「{name}」を保存しました")

    def _on_preset_overwrite_requested(name: str, positive: str, negative: str) -> None:
        settings.add_prompt_preset(name, positive, negative)
        settings.set_last_preset(name)
        window.update_preset_list(settings.prompt_presets, name)
        window.update_status(f"プリセット「{name}」を上書き保存しました")

    def _on_preset_load_requested(name: str) -> None:
        for p in settings.prompt_presets:
            if p.get("name") == name:
                window.set_prompts_without_signal(p.get("positive", ""), p.get("negative", ""))
                settings.set_positive_prompt(p.get("positive", ""))
                settings.set_negative_prompt(p.get("negative", ""))
                settings.set_last_preset(name)
                window.update_status(f"プリセット「{name}」を読み込みました")
                break

    def _on_preset_delete_requested(name: str) -> None:
        settings.remove_prompt_preset(name)
        settings.set_last_preset("")
        window.update_preset_list(settings.prompt_presets)
        window.update_status(f"プリセット「{name}」を削除しました")

    window.prompt_changed.connect(_on_prompt_changed)
    window.preset_save_requested.connect(_on_preset_save_requested)
    window.preset_overwrite_requested.connect(_on_preset_overwrite_requested)
    window.preset_load_requested.connect(_on_preset_load_requested)
    window.preset_delete_requested.connect(_on_preset_delete_requested)

    def _change_install_location() -> None:
        from .gui.first_run_dialog_stub import ask_install_location
        new_path = ask_install_location(runtime_root, window)
        if new_path:
            save_runtime_config(exe_dir, new_path)
            QMessageBox.information(
                window,
                "インストール場所を変更しました",
                f"次回起動時に以下の場所を使用します:\n{new_path}\n\nアプリを再起動してください。",
            )

    window.set_change_location_callback(_change_install_location)

    def _change_auto_save_folder() -> None:
        current = settings.auto_save_folder
        folder = QFileDialog.getExistingDirectory(
            window, "自動保存先フォルダを選択", current or str(paths.outputs_dir),
        )
        if not folder:
            return
        settings.set_auto_save_folder(folder)
        QMessageBox.information(window, "自動保存先を設定しました", f"画像完成時に以下のフォルダへ自動保存します:\n{folder}")

    window.set_auto_save_folder_callback(_change_auto_save_folder)

    def _on_auto_save_toggled(checked: bool) -> None:
        if checked and not settings.auto_save_folder:
            folder = QFileDialog.getExistingDirectory(
                window, "自動保存先フォルダを選択", str(paths.outputs_dir),
            )
            if not folder:
                window.set_auto_save_checked(False)
                return
            settings.set_auto_save_folder(folder)
        settings.set_auto_save_enabled(checked)

    window.set_auto_save_toggle_callback(_on_auto_save_toggled)
    window.set_auto_save_checked(settings.auto_save_enabled)

    se = QSoundEffect()
    se.setSource(QUrl.fromLocalFile(str(paths.complete_se_wav)))
    se.setVolume(settings.se_volume / 100)

    def _play_complete_se() -> None:
        if not settings.se_enabled:
            return
        se.setVolume(settings.se_volume / 100)
        se.play()

    def _on_se_enabled_toggle(checked: bool) -> None:
        settings.set_se_enabled(checked)

    def _on_se_volume_change(volume: int) -> None:
        settings.set_se_volume(volume)

    window.set_se_enabled_callback(_on_se_enabled_toggle)
    window.set_se_volume_callback(_on_se_volume_change)
    window.set_se_enabled_checked(settings.se_enabled)
    window.set_se_volume_checked(settings.se_volume)

    def _on_se_batch_mode(mode: str) -> None:
        settings.set_se_batch_mode(mode)

    window.set_se_batch_callback(_on_se_batch_mode)
    window.set_se_batch_mode_checked(settings.se_batch_mode)

    def _on_vram_mode_change(mode: str) -> None:
        settings.set("vram_mode", mode)
        from .constants import VRAM_MODE_LABELS
        label = VRAM_MODE_LABELS.get(mode, mode)
        QMessageBox.information(
            window, "VRAMモードを変更しました",
            f"{label} に設定しました。\n\n次回起動時に反映されます。",
        )

    window.set_vram_mode_callback(_on_vram_mode_change)
    window.set_current_vram_mode(settings.vram_mode)

    def _on_always_on_top_toggle(checked: bool) -> None:
        settings.set_always_on_top(checked)

    window.set_always_on_top_callback(_on_always_on_top_toggle)
    window.set_always_on_top(settings.always_on_top)

    def _on_gaming_skin_toggle(checked: bool) -> None:
        settings.set_gaming_skin(checked)

    window.set_gaming_skin_callback(_on_gaming_skin_toggle)
    window.set_gaming_skin_checked(settings.gaming_skin)

    def _on_naming_pattern_change(pattern: str) -> None:
        settings.set_naming_pattern(pattern)

    window.set_naming_pattern_callback(_on_naming_pattern_change)

    signals = _AsyncSignals()
    _cancel_flag = threading.Event()
    _gen_mode = "once"
    _gen_total = 1
    _gen_completed = 0

    @Slot(SetupProgress)
    def _on_setup_progress(p: SetupProgress) -> None:
        if p.state == SetupState.READY:
            window.hide_generating()
        else:
            pct = p.percent
            window.update_progress(p.message, pct, p.detail)

    @Slot(str, list, str, bool)
    def _on_setup_ready(system_info: str, installed_presets: list, model_preset: str, sage_attention_available: bool) -> None:
        window.set_system_info(system_info)

    @Slot(object)
    def _on_preview_image(img: QImage) -> None:
        if img is not None and not img.isNull():
            window.show_preview_image(img)

    @Slot(JobProgress)
    def _on_job_progress(p: JobProgress) -> None:
        window.update_progress(p.message, _job_overall_pct(p))
        if p.preview_data:
            img = QImage()
            img.loadFromData(p.preview_data)
            if not img.isNull():
                try:
                    signals.preview_image.emit(img)
                except RuntimeError:
                    pass

    @Slot(Path, float)
    def _on_job_done(output: Path, elapsed_sec: float) -> None:
        nonlocal _gen_completed
        _gen_completed += 1
        window.show_preview(output)
        window.add_to_gallery(output)

        if _gen_mode == "once" or settings.se_batch_mode == "each":
            _play_complete_se()

        if _gen_mode == "once":
            window.hide_generating()

        auto_folder = settings.auto_save_folder
        if settings.auto_save_enabled and auto_folder:
            try:
                dest_dir = Path(auto_folder)
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(output), str(dest_dir / output.name))
            except Exception as e:
                logger.warning("自動保存に失敗しました: %s", e)

        mins = int(elapsed_sec // 60)
        secs = int(elapsed_sec % 60)
        elapsed_str = f" ({mins}分{secs}秒)" if mins > 0 else f" ({secs}秒)"
        window.update_status(f"完成: {output.name}{elapsed_str} [{_gen_completed}/{_gen_total if _gen_mode != 'inf' else '∞'}]")

    @Slot()
    def _on_job_cancelled() -> None:
        window.hide_generating()
        window.update_status(f"中断しました ({_gen_completed}枚生成済み)")

    @Slot(str, float)
    def _on_batch_progress(message: str, pct: float) -> None:
        window.update_progress(message, pct)

    @Slot(int, int, float)
    def _on_batch_done(completed: int, total: int, elapsed_sec: float) -> None:
        window.hide_generating()
        mins = int(elapsed_sec // 60)
        secs = int(elapsed_sec % 60)
        time_str = f"{mins}分{secs}秒" if mins > 0 else f"{secs}秒"
        cancelled = _cancel_flag.is_set()
        if cancelled:
            window.update_status(f"中断: {completed}枚生成 ({time_str})")
        else:
            window.update_status(f"完了: {completed}枚生成 ({time_str})")
            _play_complete_se()

    @Slot(str, str, str, bool)
    def _on_error(title: str, msg: str, detail: str, show_repair: bool) -> None:
        window.hide_generating()
        window.show_error(title, msg, detail, show_repair)

    signals.setup_progress.connect(_on_setup_progress, Qt.ConnectionType.QueuedConnection)
    signals.setup_ready.connect(_on_setup_ready, Qt.ConnectionType.QueuedConnection)
    signals.job_progress.connect(_on_job_progress, Qt.ConnectionType.QueuedConnection)
    signals.preview_image.connect(_on_preview_image, Qt.ConnectionType.QueuedConnection)
    signals.job_done.connect(_on_job_done, Qt.ConnectionType.QueuedConnection)
    signals.job_cancelled.connect(_on_job_cancelled, Qt.ConnectionType.QueuedConnection)
    signals.batch_progress.connect(_on_batch_progress, Qt.ConnectionType.QueuedConnection)
    signals.batch_done.connect(_on_batch_done, Qt.ConnectionType.QueuedConnection)
    signals.error.connect(_on_error, Qt.ConnectionType.QueuedConnection)
    signals.app_quit.connect(app.quit, Qt.ConnectionType.QueuedConnection)

    async def _run_setup() -> None:
        def _cb(p: SetupProgress):
            try:
                signals.setup_progress.emit(p)
            except RuntimeError:
                pass
        try:
            await ctrl.ensure_ready(on_progress=_cb)
            try:
                signals.setup_ready.emit(
                    ctrl.get_system_info_text(), settings.installed_presets, settings.model_preset,
                    ctrl.sage_attention_available,
                )
                signals.setup_progress.emit(SetupProgress(state=SetupState.READY, message="準備完了"))
            except RuntimeError:
                pass
        except SystemUnsupportedError as e:
            try:
                signals.error.emit("対応環境外", str(e), "", False)
            except RuntimeError:
                pass
        except MakeAiFactoryError as e:
            try:
                signals.error.emit("セットアップ失敗", str(e), "", True)
            except RuntimeError:
                pass
        except Exception as e:
            logger.exception("セットアップ中に予期しないエラー")
            try:
                signals.error.emit("セットアップ失敗", str(e), "", True)
            except RuntimeError:
                pass

    async def _run_single_job(positive: str, negative: str, workflow: str) -> None:
        def _cb(p: JobProgress):
            try:
                signals.job_progress.emit(p)
            except RuntimeError:
                pass
        try:
            settings.set_active_workflow(workflow)
            settings.set_positive_prompt(positive)
            settings.set_negative_prompt(negative)
            job_ctrl = ctrl.get_job_controller()
            output, bench = await job_ctrl.run_job(positive, negative, on_progress=_cb)
            try:
                signals.job_done.emit(output, bench.elapsed_sec)
            except RuntimeError:
                pass
        except MakeAiFactoryError as e:
            if _cancel_flag.is_set():
                try:
                    signals.job_cancelled.emit()
                except RuntimeError:
                    pass
            else:
                try:
                    signals.error.emit("生成失敗", str(e), "", True)
                except RuntimeError:
                    pass
        except Exception as e:
            if _cancel_flag.is_set():
                try:
                    signals.job_cancelled.emit()
                except RuntimeError:
                    pass
            else:
                logger.exception("生成中に予期しないエラー")
                try:
                    signals.error.emit("生成失敗", str(e), "", False)
                except RuntimeError:
                    pass

    async def _run_batch_jobs(positive: str, negative: str, workflow: str, total: int, infinite: bool) -> None:
        start = time.monotonic()
        batch_completed = 0

        i = 0
        while True:
            if _cancel_flag.is_set():
                break
            if not infinite and i >= total:
                break

            try:
                signals.batch_progress.emit(f"生成中 ({i + 1}/{total if not infinite else '∞'})", 0)
            except RuntimeError:
                pass

            def _cb(p: JobProgress, idx: int = i) -> None:
                pct = _job_overall_pct(p)
                if infinite:
                    msg = f"生成中 (∞: {idx + 1}枚目)"
                else:
                    msg = f"生成中 ({idx + 1}/{total})"
                try:
                    signals.batch_progress.emit(msg, pct)
                except RuntimeError:
                    pass

            try:
                settings.set_active_workflow(workflow)
                settings.set_positive_prompt(positive)
                settings.set_negative_prompt(negative)
                job_ctrl = ctrl.get_job_controller()
                output, bench = await job_ctrl.run_job(positive, negative, on_progress=_cb)
                batch_completed += 1
                try:
                    signals.job_done.emit(output, bench.elapsed_sec)
                except RuntimeError:
                    pass
            except MakeAiFactoryError as e:
                if _cancel_flag.is_set():
                    break
                logger.error("生成エラー: %s", e)
            except Exception as e:
                if _cancel_flag.is_set():
                    break
                logger.exception("生成中に予期しないエラー")

            i += 1

        elapsed = time.monotonic() - start
        try:
            signals.batch_done.emit(batch_completed, total if not infinite else batch_completed, elapsed)
        except RuntimeError:
            pass

    async def _cancel_current_job() -> None:
        try:
            job_ctrl = ctrl.get_job_controller()
            await job_ctrl.cancel_current()
        except Exception as e:
            logger.debug("ジョブキャンセル失敗: %s", e)

    @Slot(str, str, str, int)
    def _on_generate_requested(positive: str, negative: str, mode: str, n: int) -> None:
        nonlocal _gen_mode, _gen_total, _gen_completed
        _gen_mode = mode
        _gen_total = n if mode == "n" else 1
        _gen_completed = 0
        _cancel_flag.clear()

        workflow = window.get_active_workflow()

        window.show_generating(cancellable=True)

        pool = QThreadPool.globalInstance()

        if mode == "once":
            pool.start(_Worker(_run_single_job(positive, negative, workflow), signals))
        elif mode == "n":
            pool.start(_Worker(_run_batch_jobs(positive, negative, workflow, n, False), signals))
        elif mode == "inf":
            pool.start(_Worker(_run_batch_jobs(positive, negative, workflow, 0, True), signals))

    def _on_cancel_requested() -> None:
        _cancel_flag.set()
        window.update_status("中断中...")
        pool = QThreadPool.globalInstance()
        pool.start(_Worker(_cancel_current_job(), signals))

    window.generate_requested.connect(_on_generate_requested, Qt.ConnectionType.QueuedConnection)
    window.cancel_requested.connect(_on_cancel_requested, Qt.ConnectionType.QueuedConnection)

    window.show()
    window.show_generating(cancellable=False)

    pool = QThreadPool.globalInstance()
    pool.start(_Worker(_run_setup(), signals))

    result = app.exec()
    ctrl.stop_server()
    return result

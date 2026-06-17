from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QUrl, Signal, Slot
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from .gui.icon_data import app_icon

from .constants import APP_NAME, SUPPORTED_IMAGE_EXTENSIONS
from .core.app_controller import AppController
from .core.bot_state import write_bot_state
from .core.install_config import load_runtime_config, save_runtime_config
from .core.log_manager import setup_logging
from .core.paths import AppPaths, _exe_dir
from .core.settings_store import SettingsStore
from .domain.errors import MakeAiFactoryError, SystemUnsupportedError
from .domain.progress import JobProgress, JobState, SetupProgress, SetupState


def _job_overall_pct(p: JobProgress) -> float:
    """JobProgress から単体ジョブの全体進捗 (0-100) を算出する。
    全体バーは 100% に到達するのが完了時の1回だけになるよう設計。"""
    if p.state == JobState.UPLOADING:
        return 5.0
    if p.state == JobState.QUEUED:
        return 8.0
    if p.state == JobState.GENERATING:
        return 10.0 + p.percent * 0.80   # 10-90%
    if p.state == JobState.RESOLVING_OUTPUT:
        return 92.0
    if p.state == JobState.COMPLETED:
        return 100.0
    return 0.0


def _task_pct(p: JobProgress) -> float:
    """現在のステップバーに表示する進捗。生成中以外は不定 (-1)。"""
    return p.percent if p.state == JobState.GENERATING else -1.0
from .core.discord_bot_controller import DiscordBotController
from .gui.batch_dialog import BatchDialog
from .gui.discord_settings_dialog import DiscordSettingsDialog
from .gui.first_run_dialog import FirstRunDialog
from .gui.install_location_dialog import InstallLocationDialog
from .gui.main_window import MainWindow

logger = logging.getLogger(__name__)


class _AsyncSignals(QObject):
    setup_progress = Signal(SetupProgress)
    setup_ready    = Signal(str, list, str, bool)  # system_info_text, installed_presets, model_preset, sage_attention_available
    job_progress   = Signal(JobProgress)
    job_done       = Signal(Path, str, float, float, float)  # output, stem, elapsed, vram_peak, vram_avg
    job_cancelled  = Signal()
    batch_progress      = Signal(str, float, float, float, str)  # message, all_pct, image_pct, task_pct, detail
    batch_current_image = Signal(Path)  # バッチ処理で処理中の画像が切り替わった
    batch_done          = Signal(int, int, float)     # completed, total, elapsed_sec
    error               = Signal(str, str, str, bool)
    app_quit            = Signal()


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

    def _change_auto_save_folder() -> None:
        current = settings.auto_save_folder
        folder = QFileDialog.getExistingDirectory(
            window, "自動保存先フォルダを選択", current or str(paths.outputs_dir),
        )
        if not folder:
            return
        settings.set_auto_save_folder(folder)
        QMessageBox.information(
            window,
            "自動保存先を設定しました",
            f"動画完成時に以下のフォルダへ自動保存します:\n{folder}",
        )

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

    def _play_complete_se(is_batch: bool = False) -> None:
        if not settings.se_enabled:
            return
        if is_batch and not settings.se_on_batch_complete:
            return
        se.setVolume(settings.se_volume / 100)
        se.play()

    def _on_se_enabled_toggle(checked: bool) -> None:
        settings.set_se_enabled(checked)

    def _on_se_batch_toggle(checked: bool) -> None:
        settings.set_se_on_batch_complete(checked)

    def _on_se_volume_change(volume: int) -> None:
        settings.set_se_volume(volume)

    window.set_se_enabled_callback(_on_se_enabled_toggle)
    window.set_se_batch_callback(_on_se_batch_toggle)
    window.set_se_volume_callback(_on_se_volume_change)
    window.set_se_enabled_checked(settings.se_enabled)
    window.set_se_batch_checked(settings.se_on_batch_complete)
    window.set_se_volume_checked(settings.se_volume)

    def _on_vram_mode_change(mode: str) -> None:
        settings.set("vram_mode", mode)
        from .constants import VRAM_MODE_LABELS
        label = VRAM_MODE_LABELS.get(mode, mode)
        QMessageBox.information(
            window,
            "VRAMモードを変更しました",
            f"{label} に設定しました。\n\n次回起動時に反映されます。\n"
            "（今すぐ反映するにはアプリを再起動してください）",
        )

    window.set_vram_mode_callback(_on_vram_mode_change)
    window.set_current_vram_mode(settings.vram_mode)

    def _on_preset_change(preset: str) -> None:
        settings.set_model_preset(preset)
        from .constants import MODEL_PRESETS
        label = MODEL_PRESETS.get(preset, {}).get("label", preset)
        QMessageBox.information(
            window,
            "モデルプリセットを変更しました",
            f"{label} に設定しました。\n次回の生成から反映されます。",
        )

    window.set_preset_change_callback(_on_preset_change)
    window.set_preset_add_callback(lambda: _trigger_preset_install(ctrl, window, paths, settings))

    def _on_sage_attention_toggle(checked: bool) -> None:
        settings.set_sage_attention_enabled(checked)

    window.set_sage_attention_callback(_on_sage_attention_toggle)

    def _on_always_on_top_toggle(checked: bool) -> None:
        settings.set_always_on_top(checked)

    window.set_always_on_top_callback(_on_always_on_top_toggle)
    window.set_always_on_top(settings.always_on_top)

    signals = _AsyncSignals()

    # Discord Bot コントローラ（起動後に設定）
    _discord: dict = {"ctrl": None, "generating": False}

    # バッチキャンセル用フラグ（スレッドセーフ）
    _batch_cancel = threading.Event()
    # 「現在の生成で終了」フラグ — キャンセルと区別するためだけに使う
    _batch_finish_after_current = threading.Event()
    # 単体生成キャンセル用フラグ（スレッドセーフ）
    _single_cancel = threading.Event()

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

    @Slot(str, list, str, bool)
    def _on_setup_ready(system_info: str, installed_presets: list, model_preset: str, sage_attention_available: bool) -> None:
        window.set_system_info(system_info)
        window.update_preset_menu(installed_presets, model_preset)
        window.set_sage_attention_available(sage_attention_available)
        window.set_sage_attention_checked(sage_attention_available and settings.sage_attention_enabled)
        write_bot_state(paths.runtime_root, "idle", ctrl.comfy_port)
        if settings.discord_bot_enabled and settings.discord_token:
            _start_discord_bot()

    @Slot(JobProgress)
    def _on_job_progress(p: JobProgress) -> None:
        window.update_single_progress(
            p.message,
            _job_overall_pct(p),
            _task_pct(p),
            p.message if p.state == JobState.GENERATING else "",
        )

    @Slot(Path, str, float, float, float)
    def _on_job_done(output: Path, source_stem: str, elapsed_sec: float, vram_peak: float, vram_avg: float) -> None:
        write_bot_state(paths.runtime_root, "idle")
        window.show_result(output, source_stem, elapsed_sec, vram_peak, vram_avg)
        _play_complete_se()

    @Slot()
    def _on_job_cancelled() -> None:
        write_bot_state(paths.runtime_root, "idle")
        window.stop_elapsed_timer()
        window.hide_cancel_btn()
        window.show_drop_page()
        QMessageBox.information(window, "中断しました", "生成を中断しました")

    @Slot(str, float, float, float, str)
    def _on_batch_progress(message: str, all_pct: float, image_pct: float, task_pct: float, detail: str) -> None:
        window.update_batch_progress(message, all_pct, image_pct, task_pct, detail)

    @Slot(Path)
    def _on_batch_current_image(image_path: Path) -> None:
        window.set_current_image(image_path)

    @Slot(int, int, float)
    def _on_batch_done(completed: int, total: int, elapsed_sec: float) -> None:
        write_bot_state(paths.runtime_root, "idle")
        window.stop_elapsed_timer()
        window.hide_cancel_btn()
        window.hide_finish_current_btn()
        mins = int(elapsed_sec // 60)
        secs = int(elapsed_sec % 60)
        time_str = f"{mins}分{secs}秒" if mins > 0 else f"{secs}秒"
        cancelled = _batch_cancel.is_set()
        finish_after = _batch_finish_after_current.is_set()
        if cancelled and not finish_after:
            title = "バッチ処理を中断しました"
            msg = f"{completed}/{total}枚 を処理して中断しました\n経過時間: {time_str}"
        elif cancelled and finish_after:
            title = "バッチ処理を停止しました"
            msg = f"{completed}/{total}枚 を処理して停止しました\n経過時間: {time_str}"
        else:
            title = "バッチ処理が完了しました"
            msg = f"{completed}枚 の動画を生成しました\n経過時間: {time_str}"
            _play_complete_se(is_batch=True)
        QMessageBox.information(window, title, msg)
        window.show_drop_page()

    @Slot(str, str, str, bool)
    def _on_error(title: str, msg: str, detail: str, show_repair: bool) -> None:
        write_bot_state(paths.runtime_root, "idle")
        window.stop_elapsed_timer()
        window.hide_cancel_btn()
        window.show_drop_page()
        window.show_error(title, msg, detail, show_repair)

    # ── Discord Bot ───────────────────────────────────────────────────────

    def _start_discord_bot() -> None:
        if _discord["ctrl"]:
            _discord["ctrl"].stop()
        bot = DiscordBotController(settings, paths)
        _discord["ctrl"] = bot
        sig = bot.signals
        sig.job_started.connect(_on_discord_job_started,   Qt.ConnectionType.QueuedConnection)
        sig.job_progress.connect(_on_discord_job_progress, Qt.ConnectionType.QueuedConnection)
        sig.job_done.connect(_on_discord_job_done,         Qt.ConnectionType.QueuedConnection)
        sig.job_cancelled.connect(_on_discord_job_cancelled, Qt.ConnectionType.QueuedConnection)
        sig.job_error.connect(_on_discord_job_error,       Qt.ConnectionType.QueuedConnection)
        sig.status_changed.connect(window.update_discord_status, Qt.ConnectionType.QueuedConnection)
        bot.start()

    @Slot(str, str)
    def _on_discord_job_started(image_path: str, username: str) -> None:
        _discord["generating"] = True
        write_bot_state(paths.runtime_root, "single")
        window.enter_single_mode(Path(image_path))
        window.update_single_progress(f"Discord: @{username}", 0.0, -1.0)

        def _do_discord_cancel() -> None:
            if _discord["ctrl"]:
                _discord["ctrl"].cancel_current_job()
            window.update_status("Discord 生成をキャンセル中...")

        window.show_cancel_btn(_do_discord_cancel)
        logger.info("Discord ジョブ開始: @%s %s", username, image_path)

    @Slot(float, str)
    def _on_discord_job_progress(pct: float, msg: str) -> None:
        if _discord["generating"]:
            window.update_single_progress(msg, pct, pct)

    @Slot(str)
    def _on_discord_job_done(output_path: str) -> None:
        _discord["generating"] = False
        write_bot_state(paths.runtime_root, "idle")
        window.show_result(Path(output_path))
        _play_complete_se()
        logger.info("Discord ジョブ完了: %s", output_path)

    @Slot()
    def _on_discord_job_cancelled() -> None:
        _discord["generating"] = False
        write_bot_state(paths.runtime_root, "idle")
        window.stop_elapsed_timer()
        window.hide_cancel_btn()
        window.show_drop_page()
        window.update_status("Discord からの生成をキャンセルしました")

    @Slot(str)
    def _on_discord_job_error(msg: str) -> None:
        _discord["generating"] = False
        write_bot_state(paths.runtime_root, "idle")
        window.stop_elapsed_timer()
        window.hide_cancel_btn()
        window.show_drop_page()
        window.update_status(f"Discord 生成エラー: {msg}")
        logger.error("Discord ジョブエラー: %s", msg)

    @Slot()
    def _on_discord_settings_requested() -> None:
        dlg = DiscordSettingsDialog(settings, window)
        if _discord["ctrl"]:
            _discord["ctrl"].signals.status_changed.connect(
                dlg.update_bot_status, Qt.ConnectionType.QueuedConnection
            )

        def _handle_save(enabled: bool, token: str, channel_ids) -> None:
            settings.set_discord_bot_enabled(enabled)
            settings.set_discord_token(token)
            settings.set_discord_channel_ids(list(channel_ids))
            if enabled and token:
                _start_discord_bot()
                if _discord["ctrl"]:
                    _discord["ctrl"].signals.status_changed.connect(
                        dlg.update_bot_status, Qt.ConnectionType.QueuedConnection
                    )
                dlg.update_bot_status("接続中...")
                window.update_discord_status("接続中...")
            else:
                if _discord["ctrl"]:
                    _discord["ctrl"].stop()
                    _discord["ctrl"] = None
                window.update_discord_status("停止")
                dlg.update_bot_status("停止")

        dlg.save_requested.connect(_handle_save)
        dlg.exec()

    signals.setup_progress.connect(_on_setup_progress,          Qt.ConnectionType.QueuedConnection)
    signals.setup_ready.connect(_on_setup_ready,                Qt.ConnectionType.QueuedConnection)
    signals.job_progress.connect(_on_job_progress,              Qt.ConnectionType.QueuedConnection)
    signals.job_done.connect(_on_job_done,                      Qt.ConnectionType.QueuedConnection)
    signals.job_cancelled.connect(_on_job_cancelled,            Qt.ConnectionType.QueuedConnection)
    signals.batch_progress.connect(_on_batch_progress,          Qt.ConnectionType.QueuedConnection)
    signals.batch_current_image.connect(_on_batch_current_image, Qt.ConnectionType.QueuedConnection)
    signals.batch_done.connect(_on_batch_done,                  Qt.ConnectionType.QueuedConnection)
    signals.error.connect(_on_error,                            Qt.ConnectionType.QueuedConnection)
    signals.app_quit.connect(app.quit,                          Qt.ConnectionType.QueuedConnection)

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
            # frozen モードでは apply_update_and_restart が os._exit(0) を呼ぶため
            # ここには到達しない。dev モードのみ到達するが更新は適用されない。

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
            signals.setup_ready.emit(
                ctrl.get_system_info_text(), settings.installed_presets, settings.model_preset,
                ctrl.sage_attention_available,
            )
            signals.setup_progress.emit(SetupProgress(state=SetupState.READY, message="準備完了"))
        except SystemUnsupportedError as e:
            signals.error.emit("対応環境外", str(e), "", False)
        except MakeAiFactoryError as e:
            signals.error.emit("セットアップ失敗", str(e), "", True)
        except Exception as e:
            logger.exception("セットアップ中に予期しないエラー")
            signals.error.emit("セットアップ失敗", str(e), "", True)

    async def _run_job(image_path: Path) -> None:
        def _cb(p: JobProgress):
            signals.job_progress.emit(p)
        try:
            job_ctrl = ctrl.get_job_controller()
            output, bench = await job_ctrl.run_job(image_path, on_progress=_cb)
            auto_folder = settings.auto_save_folder
            if settings.auto_save_enabled and auto_folder:
                try:
                    dest_dir = Path(auto_folder)
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(output), str(dest_dir / f"{image_path.stem}.mp4"))
                    logger.info("自動保存しました: %s", dest_dir / f"{image_path.stem}.mp4")
                except Exception as e:
                    logger.warning("自動保存に失敗しました: %s", e)
            signals.job_done.emit(output, image_path.stem, bench.elapsed_sec, bench.vram_peak_gb, bench.vram_avg_gb)
        except MakeAiFactoryError as e:
            if _single_cancel.is_set():
                signals.job_cancelled.emit()
            else:
                signals.error.emit("生成失敗", str(e), "", True)
        except Exception as e:
            if _single_cancel.is_set():
                signals.job_cancelled.emit()
            else:
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

            signals.batch_current_image.emit(image_path)

            def _cb(p: JobProgress, idx: int = i, name: str = image_path.name) -> None:
                img_pct = _job_overall_pct(p)
                all_pct = (idx + img_pct / 100) / total * 100
                signals.batch_progress.emit(
                    f"フォルダ生成 ({idx + 1}/{total}): {name}",
                    all_pct,
                    img_pct,
                    _task_pct(p),
                    p.message,
                )

            try:
                job_ctrl = ctrl.get_job_controller()
                output, _bench = await job_ctrl.run_job(image_path, on_progress=_cb)
                shutil.move(str(image_path), str(end_dir / image_path.name))
                shutil.copy2(str(output), str(output_folder / f"{image_path.stem}.mp4"))
                completed += 1
            except Exception as e:
                if _batch_cancel.is_set():
                    logger.info("バッチ生成中断 (%s)", image_path.name)
                else:
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
        write_bot_state(paths.runtime_root, "single")
        window.enter_single_mode(path)
        window.update_single_progress("生成を準備しています...", 0.0, -1.0)

        _single_cancel.clear()

        def _do_cancel_single() -> None:
            _single_cancel.set()
            window.update_status("中断中...")
            pool = QThreadPool.globalInstance()
            pool.start(_Worker(_cancel_current_job(), signals))

        window.show_cancel_btn(_do_cancel_single)

        pool = QThreadPool.globalInstance()
        pool.start(_Worker(_run_job(path), signals))

    @Slot()
    def _on_batch_requested() -> None:
        dlg = BatchDialog(parent=window)
        if dlg.exec() != BatchDialog.DialogCode.Accepted:
            return
        input_folder = dlg.input_folder()
        output_folder = dlg.output_folder()

        write_bot_state(paths.runtime_root, "batch")
        _batch_cancel.clear()
        _batch_finish_after_current.clear()

        def _do_cancel() -> None:
            _batch_cancel.set()
            window.update_status("中断中...")
            window.hide_finish_current_btn()
            pool = QThreadPool.globalInstance()
            pool.start(_Worker(_cancel_current_job(), signals))

        def _do_finish_after_current() -> None:
            _batch_cancel.set()
            _batch_finish_after_current.set()
            window.update_status("現在の生成が完了したら停止します...")
            window.hide_finish_current_btn()

        window.enter_batch_mode()
        window.update_batch_progress("フォルダ生成を開始しています...", 0.0, 0.0, -1.0)
        window.show_cancel_btn(_do_cancel)
        window.show_finish_current_btn(_do_finish_after_current)

        pool = QThreadPool.globalInstance()
        pool.start(_Worker(_run_batch(input_folder, output_folder), signals))

    window.image_dropped.connect(_on_image_dropped,              Qt.ConnectionType.QueuedConnection)
    window.batch_requested.connect(_on_batch_requested,          Qt.ConnectionType.QueuedConnection)
    window.discord_settings_requested.connect(_on_discord_settings_requested, Qt.ConnectionType.QueuedConnection)

    window.show_progress_indeterminate("セットアップを確認しています...")
    window.show()

    pool = QThreadPool.globalInstance()
    pool.start(_Worker(_run_setup(), signals))

    result = app.exec()
    ctrl.stop_server()
    if _discord["ctrl"]:
        _discord["ctrl"].stop()
    return result


def _trigger_preset_install(ctrl: AppController, window: MainWindow, paths: AppPaths, settings: SettingsStore) -> None:
    import json
    from .gui.model_preset_dialog import ModelPresetDialog
    from .domain.manifest import ModelManifest

    with paths.model_manifest_json().open("r", encoding="utf-8") as f:
        manifest = ModelManifest.from_dict(json.load(f))

    dlg = ModelPresetDialog(
        runtime_root=paths.runtime_root,
        manifest=manifest,
        installed_presets=settings.installed_presets,
        parent=window,
    )

    # install_requested シグナルが来たらワーカーを起動する。
    # dlg.exec() はダイアログが閉じるまでブロックするが、その間も QueuedConnection で
    # ワーカースレッドからのシグナルを処理できるため、進捗表示が機能する。
    def _start_install(presets: list[str]) -> None:
        inst_signals = _AsyncSignals()

        def _on_progress(p: SetupProgress) -> None:
            dlg.show_progress(p.message, p.percent, p.overall_percent)

        def _on_done(p: SetupProgress) -> None:
            if p.state == SetupState.READY:
                dlg.mark_done()
                window.update_preset_menu(settings.installed_presets, settings.model_preset)

        def _on_error(title: str, msg: str, detail: str, show_repair: bool) -> None:
            dlg.reject()
            window.show_error(title, msg, detail, False)

        inst_signals.setup_progress.connect(_on_progress, Qt.ConnectionType.QueuedConnection)
        inst_signals.setup_progress.connect(_on_done,     Qt.ConnectionType.QueuedConnection)
        inst_signals.error.connect(_on_error,             Qt.ConnectionType.QueuedConnection)

        async def _do_install() -> None:
            try:
                def _cb(p: SetupProgress, s=inst_signals) -> None:
                    s.setup_progress.emit(p)
                await ctrl.install_presets(presets, on_progress=_cb)
            except Exception as e:
                logger.exception("プリセットインストールエラー: %s", presets)
                inst_signals.error.emit("インストール失敗", str(e), "", False)
                return
            inst_signals.setup_progress.emit(SetupProgress(
                state=SetupState.READY, message="インストール完了", percent=100, overall_percent=100
            ))

        pool = QThreadPool.globalInstance()
        pool.start(_Worker(_do_install(), inst_signals))

    dlg.install_requested.connect(_start_install)
    dlg.exec()


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

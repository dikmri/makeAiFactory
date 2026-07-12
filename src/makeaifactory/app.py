from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QUrl, Signal, Slot
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from .gui.icon_data import app_icon

from .constants import APP_NAME, APP_VERSION, COMFY_HOST, SUPPORTED_IMAGE_EXTENSIONS
from .i18n import tr, tr_elapsed
from .core.app_controller import AppController
from .core.batch_output import finalize_batch_item
from .core.bot_state import write_bot_state
from .core.diagnostics import build_diagnostic_payload
from .core.error_reporter import send_error_report
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
    """現在のステップバーに表示する進捗。生成中以外・KSampler未開始は不定 (-1)。"""
    if p.state != JobState.GENERATING:
        return -1.0
    if p.total_steps <= 0:
        return -1.0
    return p.percent
from .core.discord_bot_controller import DiscordBotController
from .gui.batch_dialog import BatchDialog
from .gui.dev_mode_dialog import DevModeDialog
from .gui.discord_settings_dialog import DiscordSettingsDialog
from .gui.error_report_dialog import ErrorReportDialog
from .gui.first_run_dialog import CURRENT_TERMS_VERSION, FirstRunDialog
from .gui.install_location_dialog import InstallLocationDialog
from .gui.main_window import MainWindow
from .gui.remote_room_dialog import RemoteRoomDialog, make_qr_pixmap
from .gui.local_bridge_dialog import LocalBridgeDialog
from .remote_room.controller import RemoteRoomController
from .remote_room.room_config import RemoteRoomConfig

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
    report_sent         = Signal(bool, str)  # success, message
    app_quit            = Signal()


class _UpdateCheckSignals(QObject):
    checked      = Signal(object)  # ReleaseInfo | None
    failed       = Signal(str)
    progress     = Signal(float)
    apply_skipped = Signal()       # devモード (非frozen) のため適用をスキップした。frozenではプロセスが終了するため到達しない


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

    from .i18n import detect_system_language, get_language, set_language
    set_language(detect_system_language())

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

    if settings.language:
        set_language(settings.language)
    else:
        settings.set_language(get_language())

    if settings.accepted_terms_version < CURRENT_TERMS_VERSION:
        dlg = FirstRunDialog()
        if dlg.exec() != FirstRunDialog.DialogCode.Accepted:
            return 0
        settings.agree_to_terms()
        settings.set_accepted_terms_version(CURRENT_TERMS_VERSION)

    ctrl = AppController(paths, settings)
    window = MainWindow()
    window.set_paths(paths.logs_dir, paths.outputs_dir)
    window.set_repair_callback(lambda: _trigger_repair(ctrl, window, paths, settings))
    window.set_report_callback(lambda title, message, detail: _on_report_requested(title, message, detail))

    def _global_excepthook(exc_type, exc_value, exc_tb) -> None:
        import traceback
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.error("未処理の例外:\n%s", tb_text)
        try:
            window.show_error(tr("予期しないエラー"), str(exc_value), tb_text[-2000:], False)
        except Exception:
            pass

    sys.excepthook = _global_excepthook

    def _on_report_requested(title: str, message: str, detail: str) -> None:
        from .core.diagnostics import sanitize_text

        payload = build_diagnostic_payload(
            title=title,
            message=message,
            detail=detail,
            system_info=ctrl.system_info,
            vram_mode=settings.vram_mode,
            model_preset=settings.model_preset,
            sage_attention_enabled=settings.sage_attention_enabled,
            runtime_state=ctrl.runtime_state_text,
            app_log_path=paths.app_log,
        )
        import json
        preview_text = json.dumps(payload.to_dict(), ensure_ascii=False, indent=2)
        dlg = ErrorReportDialog(preview_text, window)

        def _on_send(comment: str) -> None:
            payload.user_comment = sanitize_text(comment)

            async def _do_send() -> None:
                success, msg = await send_error_report(payload)
                signals.report_sent.emit(success, msg)

            pool = QThreadPool.globalInstance()
            pool.start(_Worker(_do_send(), signals))

        dlg.send_requested.connect(_on_send)
        dlg.exec()

    def _on_report_sent(success: bool, msg: str) -> None:
        if success:
            QMessageBox.information(window, tr("送信完了"), msg)
        else:
            QMessageBox.warning(window, tr("送信失敗"), msg)

    def _change_install_location() -> None:
        dlg = InstallLocationDialog(runtime_root, window)
        if dlg.exec() == InstallLocationDialog.DialogCode.Accepted:
            new_path = dlg.chosen_path()
            save_runtime_config(exe_dir, new_path)
            QMessageBox.information(
                window,
                tr("インストール場所を変更しました"),
                tr("次回起動時に以下の場所を使用します:\n{new_path}\n\nアプリを再起動してください。").format(new_path=new_path),
            )

    window.set_change_location_callback(_change_install_location)

    def _change_auto_save_folder() -> None:
        current = settings.auto_save_folder
        folder = QFileDialog.getExistingDirectory(
            window, tr("自動保存先フォルダを選択"), current or str(paths.outputs_dir),
        )
        if not folder:
            return
        settings.set_auto_save_folder(folder)
        QMessageBox.information(
            window,
            tr("自動保存先を設定しました"),
            tr("動画完成時に以下のフォルダへ自動保存します:\n{folder}").format(folder=folder),
        )

    window.set_auto_save_folder_callback(_change_auto_save_folder)

    def _on_auto_save_toggled(checked: bool) -> None:
        if checked and not settings.auto_save_folder:
            folder = QFileDialog.getExistingDirectory(
                window, tr("自動保存先フォルダを選択"), str(paths.outputs_dir),
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
        label = tr(VRAM_MODE_LABELS.get(mode, mode))
        QMessageBox.information(
            window,
            tr("VRAMモードを変更しました"),
            tr("{label} に設定しました。\n\n次回起動時に反映されます。\n"
               "（今すぐ反映するにはアプリを再起動してください）").format(label=label),
        )

    window.set_vram_mode_callback(_on_vram_mode_change)
    window.set_current_vram_mode(settings.vram_mode)

    def _on_language_change(lang: str) -> None:
        settings.set_language(lang)
        set_language(lang)
        from .i18n import LANGUAGE_LABELS
        QMessageBox.information(
            window,
            "言語を変更しました / Language changed",
            tr("{label} に設定しました。\n\n"
               "次回起動時に反映されます。\n（今すぐ反映するにはアプリを再起動してください）").format(
                label=LANGUAGE_LABELS.get(lang, lang)),
        )

    window.set_language_callback(_on_language_change)
    window.set_current_language(settings.language or get_language())

    def _on_preset_change(preset: str) -> None:
        settings.set_model_preset(preset)
        from .constants import MODEL_PRESETS
        label = tr(MODEL_PRESETS.get(preset, {}).get("label", preset))
        QMessageBox.information(
            window,
            tr("モデルプリセットを変更しました"),
            tr("{label} に設定しました。\n次回の生成から反映されます。").format(label=label),
        )

    window.set_preset_change_callback(_on_preset_change)
    window.set_preset_add_callback(lambda: _trigger_preset_install(ctrl, window, paths, settings))

    def _finalize_workflow_switch(workflow_id: str, label: str) -> bool:
        """選択ワークフローをアクティブ化し、設定とメニューに反映する。成功で True。"""
        try:
            ctrl.apply_workflow_preset(workflow_id)
        except Exception as e:
            logger.exception("ワークフロー切替に失敗: %s", workflow_id)
            window.set_active_workflow(settings.workflow)
            QMessageBox.warning(
                window,
                tr("ワークフローの切替に失敗しました"),
                tr("{label} への切替に失敗しました: {e}").format(label=label, e=e),
            )
            return False
        settings.set_workflow(workflow_id)
        window.set_active_workflow(workflow_id)
        return True

    def _start_workflow_download(workflow_id: str, label: str) -> None:
        """ワークフロー専用モデルをバックグラウンドDLし、完了後に切替を確定する。"""
        dl_signals = _AsyncSignals()
        window.show_progress_indeterminate(
            tr("「{label}」用の追加モデルをダウンロードしています...").format(label=label)
        )

        def _on_progress(p: SetupProgress) -> None:
            if p.state == SetupState.READY:
                window.show_drop_page()
                if _finalize_workflow_switch(workflow_id, label):
                    window.update_status(
                        tr("✓ ワークフローを「{label}」に切り替えました").format(label=label)
                    )
            else:
                window.show_progress(p.message, p.overall_percent, p.message)

        def _on_error(title: str, msg: str, detail: str, show_repair: bool) -> None:
            window.show_drop_page()
            window.set_active_workflow(settings.workflow)  # 選択を元に戻す
            window.show_error(title, msg, detail, False)

        dl_signals.setup_progress.connect(_on_progress, Qt.ConnectionType.QueuedConnection)
        dl_signals.error.connect(_on_error, Qt.ConnectionType.QueuedConnection)

        async def _do_download() -> None:
            try:
                def _cb(p: SetupProgress, s=dl_signals) -> None:
                    s.setup_progress.emit(p)
                await ctrl.install_workflow_models(workflow_id, on_progress=_cb)
            except Exception as e:
                logger.exception("追加モデルDL失敗: %s", workflow_id)
                dl_signals.error.emit(tr("追加ダウンロード失敗"), str(e), "", False)
                return
            dl_signals.setup_progress.emit(SetupProgress(
                state=SetupState.READY, message=tr("ダウンロード完了"), percent=100, overall_percent=100,
            ))

        pool = QThreadPool.globalInstance()
        pool.start(_Worker(_do_download(), dl_signals))

    def _on_workflow_change(workflow_id: str) -> None:
        from .constants import WORKFLOW_PRESETS
        info = WORKFLOW_PRESETS.get(workflow_id, {})
        label = tr(info.get("label", workflow_id))
        if workflow_id == settings.workflow:
            return

        # 追加DLが必要か (ワークフロー専用LoRA等の未配置分) を確認する
        try:
            missing_count, missing_bytes = ctrl.workflow_download_requirement(workflow_id)
        except Exception:
            logger.exception("追加DL要否の判定に失敗: %s", workflow_id)
            missing_count, missing_bytes = 0, 0

        if missing_count > 0:
            mb = missing_bytes / (1024 * 1024)
            ret = QMessageBox.question(
                window,
                tr("追加ダウンロードが必要です"),
                tr("「{label}」を使うには追加で {count} 個・約 {mb:.0f} MB のモデルを"
                   "ダウンロードします。\n今すぐダウンロードしますか？").format(
                    label=label, count=missing_count, mb=mb),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                window.set_active_workflow(settings.workflow)  # 選択を元に戻す
                return
            _start_workflow_download(workflow_id, label)
            return

        # 追加DL不要 → 即時切替
        if _finalize_workflow_switch(workflow_id, label):
            QMessageBox.information(
                window,
                tr("ワークフローを変更しました"),
                tr("{label} に設定しました。\n次回の生成から反映されます。").format(label=label),
            )

    window.set_workflow_change_callback(_on_workflow_change)
    window.set_active_workflow(settings.workflow)

    def _on_sage_attention_toggle(checked: bool) -> None:
        settings.set_sage_attention_enabled(checked)

    window.set_sage_attention_callback(_on_sage_attention_toggle)

    def _on_always_on_top_toggle(checked: bool) -> None:
        settings.set_always_on_top(checked)

    window.set_always_on_top_callback(_on_always_on_top_toggle)
    window.set_always_on_top(settings.always_on_top)

    def _on_check_update_requested(dlg) -> None:
        from .core.updater import check_for_update
        dlg.show_checking()
        upd_signals = _UpdateCheckSignals()

        def _on_checked(release) -> None:
            if release is None:
                dlg.show_up_to_date()
            else:
                dlg.show_update_available(release.version)

        def _on_failed(message: str) -> None:
            dlg.show_check_failed(message)

        upd_signals.checked.connect(_on_checked, Qt.ConnectionType.QueuedConnection)
        upd_signals.failed.connect(_on_failed,   Qt.ConnectionType.QueuedConnection)

        async def _do_check() -> None:
            try:
                release = await asyncio.wait_for(check_for_update(), timeout=15.0)
                upd_signals.checked.emit(release)
            except asyncio.TimeoutError:
                upd_signals.failed.emit(tr("タイムアウトしました"))
            except Exception as e:
                upd_signals.failed.emit(str(e))

        pool = QThreadPool.globalInstance()
        pool.start(_Worker(_do_check(), upd_signals))

    def _on_update_now_requested(dlg) -> None:
        from .core.updater import apply_update_and_restart, check_for_update, download_update
        upd_signals = _UpdateCheckSignals()

        def _on_progress(pct: float) -> None:
            dlg.show_downloading(pct * 100)

        def _on_failed(message: str) -> None:
            dlg.show_check_failed(message)

        def _on_apply_skipped() -> None:
            dlg.show_apply_skipped_dev_mode()
            QMessageBox.information(
                window, tr("アップデート"),
                tr("開発モードで実行中のため、アップデートの適用はスキップされました。"),
            )

        upd_signals.progress.connect(_on_progress,      Qt.ConnectionType.QueuedConnection)
        upd_signals.failed.connect(_on_failed,           Qt.ConnectionType.QueuedConnection)
        upd_signals.apply_skipped.connect(_on_apply_skipped, Qt.ConnectionType.QueuedConnection)

        async def _do_update() -> None:
            try:
                release = await asyncio.wait_for(check_for_update(), timeout=15.0)
                if release is None:
                    upd_signals.failed.emit(tr("最新バージョンが見つかりませんでした"))
                    return

                def _cb(pct: float) -> None:
                    upd_signals.progress.emit(pct)
                zip_path = await download_update(release, progress_cb=_cb)
                apply_update_and_restart(zip_path)
                # frozen モードでは os._exit(0) が呼ばれここには到達しない。
                # 到達した場合は dev モードでスキップされたことを意味する。
                upd_signals.apply_skipped.emit()
            except asyncio.TimeoutError:
                upd_signals.failed.emit(tr("タイムアウトしました"))
            except Exception as e:
                upd_signals.failed.emit(str(e))

        pool = QThreadPool.globalInstance()
        pool.start(_Worker(_do_update(), upd_signals))

    window.set_check_update_callback(_on_check_update_requested)
    window.set_update_now_callback(_on_update_now_requested)

    signals = _AsyncSignals()

    # Discord Bot コントローラ（起動後に設定）
    _discord: dict = {"ctrl": None, "generating": False, "batch_running": False, "batch_all_pct": 0.0}

    # Remote Room コントローラ
    _remote_room: dict = {"ctrl": None, "dlg": None, "generating": False}
    _local_bridge: dict = {"dlg": None}

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
        # 保存済みワークフロー選択をメニュー表示と runtime_template に反映する。
        # default は出荷テンプレート(および開発モードでの編集)をそのまま使うため
        # 再適用しない。pai/fe はアップデートで出荷テンプレートに戻る可能性があるため
        # 起動時に再適用して選択を確実に反映する。
        active_wf = settings.workflow
        window.set_active_workflow(active_wf)
        if active_wf != "default":
            try:
                ctrl.apply_workflow_preset(active_wf)
            except Exception:
                logger.exception("起動時のワークフロー再適用に失敗: %s", active_wf)
                settings.set_workflow("default")
                window.set_active_workflow("default")
        window.set_sage_attention_available(sage_attention_available)
        window.set_sage_attention_checked(sage_attention_available and settings.sage_attention_enabled)
        write_bot_state(paths.runtime_root, "idle", ctrl.comfy_port)
        if os.environ.get("MAF_UPDATE_APPLIED"):
            window.update_status(tr("✓ v{version} にアップデートされました").format(version=APP_VERSION))
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
        QMessageBox.information(window, tr("中断しました"), tr("生成を中断しました"))

    @Slot(str, float, float, float, str)
    def _on_batch_progress(message: str, all_pct: float, image_pct: float, task_pct: float, detail: str) -> None:
        _discord["batch_all_pct"] = all_pct
        window.update_batch_progress(message, all_pct, image_pct, task_pct, detail)

    @Slot(Path)
    def _on_batch_current_image(image_path: Path) -> None:
        window.set_current_image(image_path)

    @Slot(int, int, float)
    def _on_batch_done(completed: int, total: int, elapsed_sec: float) -> None:
        _discord["batch_running"] = False
        if _discord["ctrl"]:
            _discord["ctrl"].set_batch_mode(False)
        write_bot_state(paths.runtime_root, "idle")
        window.stop_elapsed_timer()
        window.hide_cancel_btn()
        window.hide_finish_current_btn()
        time_str = tr_elapsed(elapsed_sec)
        cancelled = _batch_cancel.is_set()
        finish_after = _batch_finish_after_current.is_set()
        if cancelled and not finish_after:
            title = tr("バッチ処理を中断しました")
            msg = tr("{completed}/{total}枚 を処理して中断しました\n経過時間: {time_str}").format(
                completed=completed, total=total, time_str=time_str)
        elif cancelled and finish_after:
            title = tr("バッチ処理を停止しました")
            msg = tr("{completed}/{total}枚 を処理して停止しました\n経過時間: {time_str}").format(
                completed=completed, total=total, time_str=time_str)
        else:
            title = tr("バッチ処理が完了しました")
            msg = tr("{completed}枚 の動画を生成しました\n経過時間: {time_str}").format(
                completed=completed, time_str=time_str)
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
        logger.info("_start_discord_bot 開始 (既存ctrl=%s)", _discord["ctrl"] is not None)
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
        try:
            ctrl.build_workflow_templates()
        except Exception:
            logger.warning("Discord Bot: ワークフローテンプレート生成に失敗", exc_info=True)
        bot.start()

    @Slot(str, str)
    def _on_discord_job_started(image_path: str, username: str) -> None:
        _discord["generating"] = True
        write_bot_state(paths.runtime_root, "single")

        if _discord["batch_running"]:
            window.set_current_image(Path(image_path))
            window.update_batch_progress(
                tr("⚡ Discord 割り込み生成中: @{username}").format(username=username),
                _discord["batch_all_pct"], 0.0, -1.0, tr("Discord からの依頼を処理中"),
            )
            logger.info("Discord 割り込みジョブ開始: @%s %s", username, image_path)
            return

        window.enter_single_mode(Path(image_path))
        window.update_single_progress(f"Discord: @{username}", 0.0, -1.0)

        def _do_discord_cancel() -> None:
            if _discord["ctrl"]:
                _discord["ctrl"].cancel_current_job()
            window.update_status(tr("Discord 生成をキャンセル中..."))

        window.show_cancel_btn(_do_discord_cancel)
        logger.info("Discord ジョブ開始: @%s %s", username, image_path)

    @Slot(float, str)
    def _on_discord_job_progress(pct: float, msg: str) -> None:
        if not _discord["generating"]:
            return
        if _discord["batch_running"]:
            window.update_batch_progress(
                tr("⚡ Discord 割り込み生成中 — {msg}").format(msg=msg),
                _discord["batch_all_pct"], pct, pct, tr("Discord からの依頼を処理中"),
            )
        else:
            window.update_single_progress(msg, pct, pct)

    @Slot(str)
    def _on_discord_job_done(output_path: str) -> None:
        _discord["generating"] = False
        if _discord["batch_running"]:
            write_bot_state(paths.runtime_root, "batch")
            window.update_batch_progress(
                tr("⚡ Discord 割り込み完了 — バッチ再開中..."),
                _discord["batch_all_pct"], 100.0, -1.0, "",
            )
            logger.info("Discord 割り込みジョブ完了: %s", output_path)
            return
        write_bot_state(paths.runtime_root, "idle")
        window.show_result(Path(output_path))
        _play_complete_se()
        logger.info("Discord ジョブ完了: %s", output_path)

    @Slot()
    def _on_discord_job_cancelled() -> None:
        _discord["generating"] = False
        if _discord["batch_running"]:
            write_bot_state(paths.runtime_root, "batch")
            return
        write_bot_state(paths.runtime_root, "idle")
        window.stop_elapsed_timer()
        window.hide_cancel_btn()
        window.show_drop_page()
        window.update_status(tr("Discord からの生成をキャンセルしました"))

    @Slot(str)
    def _on_discord_job_error(msg: str) -> None:
        _discord["generating"] = False
        if _discord["batch_running"]:
            write_bot_state(paths.runtime_root, "batch")
            logger.error("Discord 割り込みジョブエラー: %s", msg)
            return
        write_bot_state(paths.runtime_root, "idle")
        window.stop_elapsed_timer()
        window.hide_cancel_btn()
        window.show_drop_page()
        window.update_status(tr("Discord 生成エラー: {msg}").format(msg=msg))
        logger.error("Discord ジョブエラー: %s", msg)

    @Slot()
    def _on_discord_settings_requested() -> None:
        dlg = DiscordSettingsDialog(settings, window)
        if _discord["ctrl"]:
            _discord["ctrl"].signals.status_changed.connect(
                dlg.update_bot_status, Qt.ConnectionType.QueuedConnection
            )

        def _handle_save(enabled: bool, token: str, channel_ids: list, interrupt: bool = False) -> None:
            logger.info("Discord 設定保存: enabled=%s, has_token=%s, interrupt=%s", enabled, bool(token), interrupt)
            try:
                settings.set_discord_bot_enabled(enabled)
                settings.set_discord_token(token)
                settings.set_discord_channel_ids(list(channel_ids))
                settings.set_discord_bot_interrupt(interrupt)
                if enabled and token:
                    _start_discord_bot()
                    if _discord["ctrl"]:
                        _discord["ctrl"].signals.status_changed.connect(
                            dlg.update_bot_status, Qt.ConnectionType.QueuedConnection
                        )
                    dlg.update_bot_status("connecting", tr("接続中..."))
                    window.update_discord_status("connecting", tr("接続中..."))
                else:
                    if _discord["ctrl"]:
                        _discord["ctrl"].stop()
                        _discord["ctrl"] = None
                    window.update_discord_status("stopped", tr("停止"))
                    dlg.update_bot_status("stopped", tr("停止"))
            except Exception as e:
                logger.exception("Discord 設定保存中にエラー")
                dlg.update_bot_status("error", tr("エラー: {e}").format(e=e))

        dlg.set_save_callback(_handle_save)
        dlg.exec()

    # ── Remote Room ───────────────────────────────────────────────────────

    @Slot(str, str)
    def _on_remote_url_ready(url: str, pin: str) -> None:
        """URL 公開時にメインウィンドウの drop ページへ QR を表示する。"""
        qr_url = f"{url}?pin={pin}" if pin else url
        hint = tr("📱 スキャンで入室 (PIN自動入力)") if pin else tr("📱 スキャンして入室")
        pixmap = make_qr_pixmap(qr_url, 160)
        if pixmap:
            window.show_remote_room_qr(pixmap, hint)

    @Slot(str, str)
    def _on_remote_status_for_qr(status_code: str, _message: str) -> None:
        """投入口が停止したときメインウィンドウの QR を消す。"""
        if status_code in ("stopped", "error"):
            window.clear_remote_room_qr()

    @Slot()
    def _on_remote_room_requested() -> None:
        dlg = _remote_room.get("dlg")
        if dlg is None or not dlg.isVisible():
            dlg = RemoteRoomDialog(window)
            _remote_room["dlg"] = dlg

            def _handle_start(config_dict: dict) -> None:
                room_ctrl = _remote_room.get("ctrl")
                if room_ctrl and room_ctrl.is_running:
                    return
                cfg = RemoteRoomConfig.from_dict(config_dict)
                try:
                    ctrl.build_workflow_templates()
                except Exception:
                    logger.warning("インターネット投入口: ワークフローテンプレート生成に失敗", exc_info=True)
                room_ctrl = RemoteRoomController(settings, paths)
                _remote_room["ctrl"] = room_ctrl
                sig = room_ctrl.signals
                sig.status_changed.connect(dlg.update_status,          Qt.ConnectionType.QueuedConnection)
                sig.status_changed.connect(_on_remote_status_for_qr,   Qt.ConnectionType.QueuedConnection)
                sig.public_url_ready.connect(dlg.set_public_url,       Qt.ConnectionType.QueuedConnection)
                sig.public_url_ready.connect(_on_remote_url_ready,     Qt.ConnectionType.QueuedConnection)
                sig.stats_changed.connect(dlg.update_stats,            Qt.ConnectionType.QueuedConnection)
                sig.error.connect(dlg.show_error_msg,                  Qt.ConnectionType.QueuedConnection)
                sig.job_started.connect(_on_remote_job_started,        Qt.ConnectionType.QueuedConnection)
                sig.job_progress.connect(_on_remote_job_progress,      Qt.ConnectionType.QueuedConnection)
                sig.job_done.connect(_on_remote_job_done,              Qt.ConnectionType.QueuedConnection)
                sig.job_error.connect(_on_remote_job_error,            Qt.ConnectionType.QueuedConnection)
                settings.set_remote_room_config(config_dict)
                room_ctrl.start(cfg)
                dlg.update_status("starting", tr("起動中..."))

            def _handle_stop() -> None:
                ctrl = _remote_room.get("ctrl")
                if ctrl:
                    ctrl.stop()
                    _remote_room["ctrl"] = None

            dlg.set_start_callback(_handle_start)
            dlg.set_stop_callback(_handle_stop)
            dlg.stop_accepting.connect(lambda: _remote_room["ctrl"].stop_accepting() if _remote_room["ctrl"] else None)
            dlg.cancel_job.connect(lambda: _remote_room["ctrl"].cancel_current_job() if _remote_room["ctrl"] else None)
            dlg.clear_queue.connect(lambda: _remote_room["ctrl"].clear_queue() if _remote_room["ctrl"] else None)

        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_local_bridge_requested() -> None:
        dlg = _local_bridge.get("dlg")
        if dlg is None:
            dlg = LocalBridgeDialog(window)
            _local_bridge["dlg"] = dlg

            def _handle_toggle(enabled: bool) -> None:
                try:
                    if enabled:
                        token = ctrl.start_local_bridge()
                        if not _local_bridge.get("connected"):
                            sig = ctrl.local_bridge_signals
                            if sig is not None:
                                sig.job_started.connect(_on_bridge_job_started,   Qt.ConnectionType.QueuedConnection)
                                sig.job_progress.connect(_on_bridge_job_progress, Qt.ConnectionType.QueuedConnection)
                                sig.job_done.connect(_on_bridge_job_done,         Qt.ConnectionType.QueuedConnection)
                                sig.job_error.connect(_on_bridge_job_error,       Qt.ConnectionType.QueuedConnection)
                                _local_bridge["connected"] = True
                        dlg.set_active(True, ctrl.local_bridge_port, token)
                    else:
                        ctrl.stop_local_bridge()
                        dlg.set_active(False, ctrl.local_bridge_port, "")
                except Exception as e:  # noqa: BLE001
                    logger.exception("ブラウザ連携の切替に失敗")
                    dlg.set_active(
                        ctrl.is_local_bridge_running, ctrl.local_bridge_port,
                        settings.local_bridge_token if ctrl.is_local_bridge_running else "",
                    )
                    QMessageBox.warning(window, tr("エラー"), tr("エラー: {e}").format(e=e))

            dlg.set_toggle_callback(_handle_toggle)

        dlg.set_active(
            ctrl.is_local_bridge_running, ctrl.local_bridge_port,
            settings.local_bridge_token if ctrl.is_local_bridge_running else "",
        )
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    @Slot(str, str)
    def _on_remote_job_started(job_id: str, image_path: str) -> None:
        _remote_room["generating"] = True
        write_bot_state(paths.runtime_root, "single")
        window.enter_single_mode(Path(image_path))
        window.update_single_progress(tr("🌐 投入口 生成中: #{job_id}").format(job_id=job_id[:6]), 0.0, -1.0)

        def _do_remote_cancel() -> None:
            ctrl = _remote_room.get("ctrl")
            if ctrl:
                ctrl.cancel_current_job()
            window.update_status(tr("🌐 投入口 生成をキャンセル中..."))

        window.show_cancel_btn(_do_remote_cancel)
        logger.info("Remote Room ジョブ開始: %s", job_id)

    @Slot(str, float, str)
    def _on_remote_job_progress(job_id: str, pct: float, label: str) -> None:
        if _remote_room["generating"]:
            window.update_single_progress(label, pct, pct)

    @Slot(str, str)
    def _on_remote_job_done(job_id: str, output_path: str) -> None:
        _remote_room["generating"] = False
        write_bot_state(paths.runtime_root, "idle")
        window.show_result(Path(output_path))
        _play_complete_se()
        logger.info("Remote Room ジョブ完了: %s → %s", job_id, output_path)

    @Slot(str, str)
    def _on_remote_job_error(job_id: str, msg: str) -> None:
        _remote_room["generating"] = False
        write_bot_state(paths.runtime_root, "idle")
        window.stop_elapsed_timer()
        window.hide_cancel_btn()
        window.show_drop_page()
        window.update_status(tr("🌐 投入口 生成エラー: {msg}").format(msg=msg))
        logger.error("Remote Room ジョブエラー: %s — %s", job_id, msg)

    # ── ブラウザ連携 (ローカルブリッジ) のジョブ反映。完成動画はアプリ側で自動保存 ──

    @Slot(str, str)
    def _on_bridge_job_started(job_id: str, image_path: str) -> None:
        _local_bridge["generating"] = True
        write_bot_state(paths.runtime_root, "single")
        window.enter_single_mode(Path(image_path))
        window.update_single_progress(
            tr("🖥 ブラウザ連携 生成中: #{job_id}").format(job_id=job_id[:6]), 0.0, -1.0)
        logger.info("ブラウザ連携 ジョブ開始: %s", job_id)

    @Slot(str, float, str)
    def _on_bridge_job_progress(job_id: str, pct: float, label: str) -> None:
        if _local_bridge.get("generating"):
            window.update_single_progress(label, pct, pct)

    @Slot(str, str)
    def _on_bridge_job_done(job_id: str, output_path: str) -> None:
        _local_bridge["generating"] = False
        write_bot_state(paths.runtime_root, "idle")
        auto_folder = settings.auto_save_folder
        if settings.auto_save_enabled and auto_folder:
            try:
                dest_dir = Path(auto_folder)
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(output_path), str(dest_dir / f"bridge_{job_id}.mp4"))
                logger.info("ブラウザ連携 自動保存: %s", dest_dir / f"bridge_{job_id}.mp4")
            except Exception as e:  # noqa: BLE001
                logger.warning("ブラウザ連携 自動保存に失敗: %s", e)
        window.show_result(Path(output_path))
        _play_complete_se()
        logger.info("ブラウザ連携 ジョブ完了: %s → %s", job_id, output_path)

    @Slot(str, str)
    def _on_bridge_job_error(job_id: str, msg: str) -> None:
        _local_bridge["generating"] = False
        write_bot_state(paths.runtime_root, "idle")
        window.stop_elapsed_timer()
        window.hide_cancel_btn()
        window.show_drop_page()
        window.update_status(tr("🖥 ブラウザ連携 生成エラー: {msg}").format(msg=msg))
        logger.error("ブラウザ連携 ジョブエラー: %s — %s", job_id, msg)

    @Slot()
    def _on_dev_mode_requested() -> None:
        async def _run_job_fn(image_path: Path, overrides, on_progress):
            job_ctrl = ctrl.get_job_controller()
            return await job_ctrl.run_job(image_path, on_progress=on_progress, dev_overrides=overrides)

        template_defaults = None
        try:
            import json
            from .comfy.workflow_patcher import extract_dev_defaults
            with paths.runtime_template_json().open("r", encoding="utf-8") as f:
                raw_template = json.load(f)
            template_defaults = extract_dev_defaults(raw_template)
        except Exception as e:
            logger.warning("開発モードのテンプレート初期値読み込み失敗: %s", e)

        workflow_json_text = ""
        try:
            workflow_json_text = paths.api_source_json().read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("api_source.json 読み込み失敗: %s", e)

        def _apply_workflow_json(text: str) -> str:
            import json
            from .comfy.workflow_sanitizer import generate_analysis_report, sanitize_workflow
            from .constants import LOADIMAGE_NODE_ID, OUTPUT_VIDEO_NODE_ID
            try:
                source = json.loads(text)
            except json.JSONDecodeError as e:
                return tr("JSON構文エラー: {e}").format(e=e)
            if not isinstance(source, dict) or not source:
                return tr("ワークフローが空です")

            sanitized = sanitize_workflow(source)
            if not sanitized or OUTPUT_VIDEO_NODE_ID not in sanitized or LOADIMAGE_NODE_ID not in sanitized:
                return tr("必須ノード (画像入力/動画出力) が含まれていないため適用できません")

            try:
                with paths.api_source_json().open("w", encoding="utf-8") as f:
                    json.dump(source, f, ensure_ascii=False, indent=2)
                with paths.runtime_template_json().open("w", encoding="utf-8") as f:
                    json.dump(sanitized, f, ensure_ascii=False, indent=2)
                report = generate_analysis_report(source, sanitized)
                with (paths.workflow_dir / "workflow_analysis_report.md").open("w", encoding="utf-8") as f:
                    f.write(report)
            except Exception as e:
                return tr("保存に失敗しました: {e}").format(e=e)

            ctrl.reload_workflow_template()
            logger.info("開発モード: ワークフローJSONを保存・適用しました")
            return ""

        dlg = DevModeDialog(
            run_job_fn=_run_job_fn,
            save_params_fn=settings.set_dev_mode_params,
            load_params=settings.dev_mode_params,
            template_defaults=template_defaults,
            workflow_json_text=workflow_json_text,
            apply_workflow_json_fn=_apply_workflow_json,
            parent=window,
        )
        dlg.show()

        async def _fetch_lora_choices() -> list[str]:
            from .comfy.api_client import ComfyApiClient
            client = ComfyApiClient(f"http://{COMFY_HOST}:{ctrl.comfy_port}")
            info = await client.get_object_info()
            node_info = info.get("LoraLoaderModelOnly", {})
            spec = node_info.get("input", {}).get("required", {}).get("lora_name")
            if spec and isinstance(spec[0], list):
                return sorted(spec[0])
            return []

        dlg.fetch_lora_choices(_fetch_lora_choices)

    signals.setup_progress.connect(_on_setup_progress,          Qt.ConnectionType.QueuedConnection)
    signals.setup_ready.connect(_on_setup_ready,                Qt.ConnectionType.QueuedConnection)
    signals.job_progress.connect(_on_job_progress,              Qt.ConnectionType.QueuedConnection)
    signals.job_done.connect(_on_job_done,                      Qt.ConnectionType.QueuedConnection)
    signals.job_cancelled.connect(_on_job_cancelled,            Qt.ConnectionType.QueuedConnection)
    signals.batch_progress.connect(_on_batch_progress,          Qt.ConnectionType.QueuedConnection)
    signals.batch_current_image.connect(_on_batch_current_image, Qt.ConnectionType.QueuedConnection)
    signals.batch_done.connect(_on_batch_done,                  Qt.ConnectionType.QueuedConnection)
    signals.error.connect(_on_error,                            Qt.ConnectionType.QueuedConnection)
    signals.report_sent.connect(_on_report_sent,                Qt.ConnectionType.QueuedConnection)
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
                    message=tr("v{version} をダウンロード中... {pct:.0f}%").format(
                        version=release.version, pct=pct * 100),
                    percent=pct * 100,
                ))

            signals.setup_progress.emit(SetupProgress(
                state=SetupState.DOWNLOADING_MODELS,
                message=tr("新しいバージョン v{version} があります。ダウンロード中...").format(version=release.version),
                percent=0,
            ))
            zip_path = await download_update(release, progress_cb=_upd_pct)
            signals.setup_progress.emit(SetupProgress(
                state=SetupState.DOWNLOADING_MODELS,
                message=tr("アップデートを適用して再起動します..."),
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
            signals.setup_progress.emit(SetupProgress(state=SetupState.READY, message=tr("準備完了")))
        except SystemUnsupportedError as e:
            signals.error.emit(tr("対応環境外"), str(e), "", False)
        except MakeAiFactoryError as e:
            signals.error.emit(tr("セットアップ失敗"), str(e), "", True)
        except Exception as e:
            logger.exception("セットアップ中に予期しないエラー")
            signals.error.emit(tr("セットアップ失敗"), str(e), "", True)

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
                signals.error.emit(tr("生成失敗"), str(e), "", True)
        except Exception as e:
            if _single_cancel.is_set():
                signals.job_cancelled.emit()
            else:
                logger.exception("生成中に予期しないエラー")
                signals.error.emit(tr("生成失敗"), str(e), "", False)

    async def _run_batch(input_folder: Path, output_folder: Path) -> None:
        images = sorted(
            p for p in input_folder.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
        )
        total = len(images)
        if total == 0:
            signals.error.emit(
                tr("画像が見つかりません"),
                tr("{folder} に対応画像がありません").format(folder=input_folder),
                "", False,
            )
            return

        end_dir = input_folder / "end"
        end_dir.mkdir(exist_ok=True)
        output_folder.mkdir(parents=True, exist_ok=True)

        start = time.monotonic()
        completed = 0
        failed: list[str] = []
        manifest_path = output_folder / "batch_manifest.json"

        def _append_manifest(entry: dict) -> None:
            # manifestへの記録はbest-effort。失敗してもバッチ処理自体は継続する。
            # 原子的書き込みは後続PRで共通化予定のため、現時点では
            # 「既存全体を読んで1件追記し、全体を書き戻す」素朴な実装でよい。
            import json
            try:
                try:
                    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if not isinstance(existing, list):
                        existing = []
                except (FileNotFoundError, json.JSONDecodeError):
                    existing = []
                existing.append(entry)
                with manifest_path.open("w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.debug("batch_manifest.json 書き込み失敗: %s", e)

        for i, image_path in enumerate(images):
            if _batch_cancel.is_set():
                break

            signals.batch_current_image.emit(image_path)

            def _cb(p: JobProgress, idx: int = i, name: str = image_path.name) -> None:
                img_pct = _job_overall_pct(p)
                all_pct = (idx + img_pct / 100) / total * 100
                signals.batch_progress.emit(
                    tr("フォルダ生成 ({idx}/{total}): {name}").format(idx=idx + 1, total=total, name=name),
                    all_pct,
                    img_pct,
                    _task_pct(p),
                    p.message,
                )

            try:
                job_ctrl = ctrl.get_job_controller()
                output, _bench = await job_ctrl.run_job(image_path, on_progress=_cb)
                final = finalize_batch_item(image_path, output, output_folder, end_dir)
                completed += 1
                _append_manifest({
                    "input": image_path.name,
                    "stem": image_path.stem,
                    "state": "done",
                    "output": final.name,
                    "error": None,
                    "timestamp": time.time(),
                })
            except Exception as e:
                if _batch_cancel.is_set():
                    logger.info("バッチ生成中断 (%s)", image_path.name)
                else:
                    failed.append(image_path.name)
                    logger.error("バッチ処理エラー (%s): %s", image_path.name, e)
                    _append_manifest({
                        "input": image_path.name,
                        "stem": image_path.stem,
                        "state": "failed",
                        "output": None,
                        "error": str(e),
                        "timestamp": time.time(),
                    })

            # Discord 割り込み生成: 1件終了後、割り込みキューが空になるまで待機
            disc_ctrl = _discord["ctrl"]
            if settings.discord_bot_interrupt and disc_ctrl and disc_ctrl.interrupt_active.is_set():
                logger.info("Discord 割り込み待機 (バッチ %d/%d 完了後)", i + 1, total)
                all_pct_so_far = (i + 1) / total * 100
                signals.batch_progress.emit(
                    tr("フォルダ生成 ({idx}/{total}) — ⚡ Discord 割り込み生成中...").format(idx=i + 1, total=total),
                    all_pct_so_far, 100.0, -1.0, tr("Discord からのリクエストを優先処理中"),
                )
                while disc_ctrl.interrupt_active.is_set():
                    # 「次の動画で終了」はソフトキャンセルなので割り込み完了まで待つ。
                    # ハードキャンセル（中断ボタン）のみ即時脱出する。
                    if _batch_cancel.is_set() and not _batch_finish_after_current.is_set():
                        break
                    await asyncio.sleep(0.5)
                logger.info("Discord 割り込み完了 → バッチ再開")

        if failed:
            logger.warning("バッチ失敗 %d件: %s", len(failed), failed)

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
        window.update_single_progress(tr("生成を準備しています..."), 0.0, -1.0)

        _single_cancel.clear()

        def _do_cancel_single() -> None:
            _single_cancel.set()
            window.update_status(tr("中断中..."))
            pool = QThreadPool.globalInstance()
            pool.start(_Worker(_cancel_current_job(), signals))

        window.show_cancel_btn(_do_cancel_single)

        pool = QThreadPool.globalInstance()
        pool.start(_Worker(_run_job(path), signals))

    @Slot()
    def _on_batch_requested() -> None:
        dlg = BatchDialog(
            parent=window,
            default_input=settings.batch_input_folder,
            default_output=settings.batch_output_folder,
        )
        if dlg.exec() != BatchDialog.DialogCode.Accepted:
            return
        input_folder = dlg.input_folder()
        output_folder = dlg.output_folder()
        settings.set_batch_input_folder(str(input_folder))
        settings.set_batch_output_folder(str(output_folder))

        write_bot_state(paths.runtime_root, "batch")
        _batch_cancel.clear()
        _batch_finish_after_current.clear()
        _discord["batch_running"] = True
        if _discord["ctrl"] and settings.discord_bot_interrupt:
            _discord["ctrl"].set_batch_mode(True)

        def _do_cancel() -> None:
            _batch_cancel.set()
            window.update_status(tr("中断中..."))
            window.hide_finish_current_btn()
            pool = QThreadPool.globalInstance()
            pool.start(_Worker(_cancel_current_job(), signals))

        def _do_finish_after_current() -> None:
            if _batch_finish_after_current.is_set():
                # 既に予約済み → 生成完了前ならここで取り消せる
                _batch_finish_after_current.clear()
                _batch_cancel.clear()
                window.update_status(tr("フォルダ生成を続行します"))
                window.set_finish_current_btn_text(tr("現在の生成で終了"))
            else:
                _batch_cancel.set()
                _batch_finish_after_current.set()
                window.update_status(tr("現在の生成が完了したら停止します..."))
                window.set_finish_current_btn_text(tr("終了予約を取り消す"))

        window.enter_batch_mode()
        window.update_batch_progress(tr("フォルダ生成を開始しています..."), 0.0, 0.0, -1.0)
        window.show_cancel_btn(_do_cancel)
        window.show_finish_current_btn(_do_finish_after_current)

        pool = QThreadPool.globalInstance()
        pool.start(_Worker(_run_batch(input_folder, output_folder), signals))

    window.image_dropped.connect(_on_image_dropped,              Qt.ConnectionType.QueuedConnection)
    window.batch_requested.connect(_on_batch_requested,          Qt.ConnectionType.QueuedConnection)
    window.discord_settings_requested.connect(_on_discord_settings_requested, Qt.ConnectionType.QueuedConnection)
    window.dev_mode_requested.connect(_on_dev_mode_requested,    Qt.ConnectionType.QueuedConnection)
    window.remote_room_requested.connect(_on_remote_room_requested, Qt.ConnectionType.QueuedConnection)
    window.local_bridge_requested.connect(_on_local_bridge_requested, Qt.ConnectionType.QueuedConnection)

    window.show_progress_indeterminate(tr("セットアップを確認しています..."))
    window.show()

    pool = QThreadPool.globalInstance()
    pool.start(_Worker(_run_setup(), signals))

    result = app.exec()
    ctrl.stop_server()
    if _discord["ctrl"]:
        _discord["ctrl"].stop()
    if _remote_room["ctrl"]:
        _remote_room["ctrl"].stop()
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
                inst_signals.error.emit(tr("インストール失敗"), str(e), "", False)
                return
            inst_signals.setup_progress.emit(SetupProgress(
                state=SetupState.READY, message=tr("インストール完了"), percent=100, overall_percent=100
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
    window.show_progress_indeterminate(tr("修復中..."))
    signals = _AsyncSignals()

    async def _repair():
        try:
            ctrl2 = AppController(paths, settings)
            await ctrl2.setup()
            signals.setup_progress.emit(SetupProgress(state=SetupState.READY, message=tr("修復完了")))
        except Exception as e:
            signals.error.emit(tr("修復失敗"), str(e), "", False)

    pool = QThreadPool.globalInstance()
    pool.start(_Worker(_repair(), signals))

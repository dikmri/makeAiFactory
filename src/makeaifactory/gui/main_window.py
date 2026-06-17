from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..constants import APP_NAME, APP_VERSION
from ..domain.progress import JobProgress, SetupProgress, SetupState
from .drop_area import DropArea
from .error_dialog import ErrorDialog
from .progress_view import ProgressView
from .result_view import ResultView

logger = logging.getLogger(__name__)

_PAGE_DROP = 0
_PAGE_PROGRESS = 1
_PAGE_RESULT = 2


class MainWindow(QMainWindow):
    image_dropped = Signal(Path)
    batch_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(540, 580)
        self._setup_style()
        self._build_ui()
        self._build_menu()

    def _setup_style(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #0f0f1a;
                color: #eee;
                font-family: "Yu Gothic UI", "Meiryo", sans-serif;
            }
            QMenuBar {
                background: #111;
                color: #ccc;
            }
            QMenuBar::item:selected { background: #1a1a2e; }
            QMenu {
                background: #1a1a2e;
                color: #ccc;
                border: 1px solid #333;
            }
            QMenu::item:selected { background: #253858; }
            QStatusBar { background: #111; color: #999; font-size: 12px; }
            QLineEdit {
                background: #1a1a2e;
                color: #eee;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QPushButton {
                background: #1a1a2e;
                color: #ccc;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QPushButton:hover { background: #253858; }
            QDialogButtonBox QPushButton {
                padding: 6px 20px;
            }
        """)

    def _build_ui(self) -> None:
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        # ── Drop page: DropArea + 一括生成ボタン ──────────────────────
        drop_page = QWidget()
        dp_layout = QVBoxLayout(drop_page)
        dp_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dp_layout.setContentsMargins(24, 24, 24, 24)
        dp_layout.setSpacing(14)

        self._drop_area = DropArea()
        self._drop_area.image_dropped.connect(self.image_dropped)
        dp_layout.addWidget(self._drop_area, alignment=Qt.AlignmentFlag.AlignCenter)

        self._batch_btn = QPushButton("フォルダ選択")
        self._batch_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #777;
                border: 1px solid #444;
                border-radius: 6px;
                padding: 7px 28px;
                font-size: 13px;
            }
            QPushButton:hover { background: #1a1a2e; color: #bbb; border-color: #4fc3f7; }
        """)
        self._batch_btn.clicked.connect(self.batch_requested)
        dp_layout.addWidget(self._batch_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._stack.addWidget(drop_page)  # _PAGE_DROP

        self._progress_view = ProgressView()
        self._stack.addWidget(self._progress_view)  # _PAGE_PROGRESS

        self._result_view = ResultView()
        self._result_view.request_again.connect(self._on_request_again)
        self._result_view.image_dropped.connect(self.image_dropped)
        self._stack.addWidget(self._result_view)  # _PAGE_RESULT

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("ファイル")
        open_output_action = QAction("保存フォルダを開く", self)
        open_output_action.triggered.connect(self._open_output_dir)
        file_menu.addAction(open_output_action)
        file_menu.addSeparator()
        quit_action = QAction("終了", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        settings_menu = menu_bar.addMenu("設定")
        change_loc_action = QAction("インストール場所を変更...", self)
        change_loc_action.triggered.connect(self._change_install_location)
        settings_menu.addAction(change_loc_action)

        auto_save_action = QAction("自動保存先を設定...", self)
        auto_save_action.triggered.connect(self._change_auto_save_folder)
        settings_menu.addAction(auto_save_action)

        self._auto_save_enabled_action = QAction("完成時に自動保存する", self)
        self._auto_save_enabled_action.setCheckable(True)
        self._auto_save_enabled_action.setChecked(False)
        self._auto_save_enabled_action.triggered.connect(self._on_auto_save_toggled)
        settings_menu.addAction(self._auto_save_enabled_action)
        self._auto_save_toggle_cb = None

        self._always_on_top_action = QAction("常に最前面に表示", self)
        self._always_on_top_action.setCheckable(True)
        self._always_on_top_action.setChecked(False)
        self._always_on_top_action.triggered.connect(self._on_always_on_top_toggled)
        settings_menu.addAction(self._always_on_top_action)
        self._always_on_top_callback = None

        settings_menu.addSeparator()
        self._preset_menu = settings_menu.addMenu("モデルプリセット")
        self._preset_group = QActionGroup(self)
        self._preset_group.setExclusive(True)
        self._preset_actions: dict[str, QAction] = {}
        self._preset_change_callback = None
        self._preset_add_callback = None
        self._rebuild_preset_menu(installed_presets=["normal"], active_preset="normal")

        settings_menu.addSeparator()
        vram_menu = settings_menu.addMenu("VRAMモード")
        self._vram_group = QActionGroup(self)
        self._vram_group.setExclusive(True)
        self._vram_actions: dict[str, QAction] = {}
        for mode, label in [
            ("normal", "通常モード (推奨: 16GB+)"),
            ("novram", "超省VRAMモード --novram (～16GB未満 / 低速・RAM大量消費)"),
        ]:
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(mode == "normal")
            act.triggered.connect(lambda checked, m=mode: self._on_vram_mode_selected(m))
            self._vram_group.addAction(act)
            vram_menu.addAction(act)
            self._vram_actions[mode] = act

        self._vram_mode_callback = None

        settings_menu.addSeparator()
        self._sage_attention_action = QAction("高速化 (SageAttention) を使う", self)
        self._sage_attention_action.setCheckable(True)
        self._sage_attention_action.setChecked(False)
        self._sage_attention_action.setEnabled(False)
        self._sage_attention_action.triggered.connect(self._on_sage_attention_toggled)
        settings_menu.addAction(self._sage_attention_action)
        self._sage_attention_callback = None

        settings_menu.addSeparator()
        se_menu = settings_menu.addMenu("完成通知音")

        self._se_enabled_action = QAction("通知音を鳴らす", self)
        self._se_enabled_action.setCheckable(True)
        self._se_enabled_action.setChecked(True)
        self._se_enabled_action.triggered.connect(self._on_se_enabled_toggled)
        se_menu.addAction(self._se_enabled_action)
        self._se_enabled_callback = None

        self._se_batch_action = QAction("フォルダ生成完了時も鳴らす", self)
        self._se_batch_action.setCheckable(True)
        self._se_batch_action.setChecked(True)
        self._se_batch_action.triggered.connect(self._on_se_batch_toggled)
        se_menu.addAction(self._se_batch_action)
        self._se_batch_callback = None

        se_menu.addSeparator()
        self._se_volume_group = QActionGroup(self)
        self._se_volume_group.setExclusive(True)
        self._se_volume_actions: dict[int, QAction] = {}
        for vol in (25, 50, 75, 100):
            act = QAction(f"音量 {vol}%", self)
            act.setCheckable(True)
            act.triggered.connect(lambda checked, v=vol: self._on_se_volume_selected(v))
            self._se_volume_group.addAction(act)
            se_menu.addAction(act)
            self._se_volume_actions[vol] = act
        self._se_volume_callback = None

        help_menu = menu_bar.addMenu("ヘルプ")
        log_action = QAction("ログを開く", self)
        log_action.triggered.connect(self._open_logs)
        help_menu.addAction(log_action)

        diag_action = QAction("runtime診断", self)
        diag_action.triggered.connect(self._show_diagnostics)
        help_menu.addAction(diag_action)

        repair_action = QAction("runtimeを修復", self)
        repair_action.triggered.connect(self._request_repair)
        help_menu.addAction(repair_action)

        help_menu.addSeparator()
        about_action = QAction("バージョン情報", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

        # Ctrl+V でクリップボードの画像をペースト
        paste_sc = QShortcut(QKeySequence.StandardKey.Paste, self)
        paste_sc.activated.connect(self._try_paste_image)

        self._logs_dir: Path | None = None
        self._output_dir: Path | None = None
        self._system_info_text: str = ""
        self._repair_callback = None
        self._change_location_cb = None
        self._auto_save_folder_cb = None
        self._vram_mode_callback = None
        self._preset_change_callback = None
        self._preset_add_callback = None

    def set_paths(self, logs_dir: Path, output_dir: Path) -> None:
        self._logs_dir = logs_dir
        self._output_dir = output_dir

    def set_system_info(self, text: str) -> None:
        self._system_info_text = text

    def set_repair_callback(self, cb) -> None:
        self._repair_callback = cb

    def set_change_location_callback(self, cb) -> None:
        self._change_location_cb = cb

    def set_auto_save_folder_callback(self, cb) -> None:
        self._auto_save_folder_cb = cb

    def set_auto_save_toggle_callback(self, cb) -> None:
        self._auto_save_toggle_cb = cb

    def set_auto_save_checked(self, checked: bool) -> None:
        self._auto_save_enabled_action.setChecked(checked)

    def _on_auto_save_toggled(self, checked: bool) -> None:
        if self._auto_save_toggle_cb:
            self._auto_save_toggle_cb(checked)

    def set_always_on_top_callback(self, cb) -> None:
        self._always_on_top_callback = cb

    def set_always_on_top(self, enabled: bool) -> None:
        """設定値をメニューとウィンドウの両方に反映する (起動時の初期反映用)。"""
        self._always_on_top_action.setChecked(enabled)
        self._apply_always_on_top(enabled)

    def _apply_always_on_top(self, enabled: bool) -> None:
        # setWindowFlag は呼んだ時点で isVisible() を即座に False にするため、
        # 判定は呼ぶ前の表示状態でキャプチャしておく必要がある。
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        # ネイティブウィンドウが再生成されるため、表示中だった場合のみ show() し直す。
        # 起動シーケンス中 (まだ未表示) に呼ばれた場合はここで先に表示してしまわないようにする。
        if was_visible:
            self.show()

    def _on_always_on_top_toggled(self, checked: bool) -> None:
        self._apply_always_on_top(checked)
        if self._always_on_top_callback:
            self._always_on_top_callback(checked)

    def set_se_enabled_callback(self, cb) -> None:
        self._se_enabled_callback = cb

    def set_se_enabled_checked(self, checked: bool) -> None:
        self._se_enabled_action.setChecked(checked)

    def _on_se_enabled_toggled(self, checked: bool) -> None:
        if self._se_enabled_callback:
            self._se_enabled_callback(checked)

    def set_se_batch_callback(self, cb) -> None:
        self._se_batch_callback = cb

    def set_se_batch_checked(self, checked: bool) -> None:
        self._se_batch_action.setChecked(checked)

    def _on_se_batch_toggled(self, checked: bool) -> None:
        if self._se_batch_callback:
            self._se_batch_callback(checked)

    def set_se_volume_callback(self, cb) -> None:
        self._se_volume_callback = cb

    def set_se_volume_checked(self, volume: int) -> None:
        if volume in self._se_volume_actions:
            self._se_volume_actions[volume].setChecked(True)

    def _on_se_volume_selected(self, volume: int) -> None:
        if self._se_volume_callback:
            self._se_volume_callback(volume)

    def set_vram_mode_callback(self, cb) -> None:
        self._vram_mode_callback = cb

    def set_preset_change_callback(self, cb) -> None:
        self._preset_change_callback = cb

    def set_preset_add_callback(self, cb) -> None:
        self._preset_add_callback = cb

    def _rebuild_preset_menu(self, installed_presets: list[str], active_preset: str) -> None:
        from ..constants import MODEL_PRESETS
        self._preset_menu.clear()
        for act in list(self._preset_actions.values()):
            self._preset_group.removeAction(act)
        self._preset_actions.clear()

        for key in MODEL_PRESETS:
            if key not in installed_presets:
                continue
            info = MODEL_PRESETS[key]
            act = QAction(info["label"], self)
            act.setCheckable(True)
            act.setChecked(key == active_preset)
            act.triggered.connect(lambda checked, k=key: self._on_preset_selected(k))
            self._preset_group.addAction(act)
            self._preset_menu.addAction(act)
            self._preset_actions[key] = act

        self._preset_menu.addSeparator()
        add_act = QAction("プリセットを追加...", self)
        add_act.triggered.connect(self._on_add_preset)
        self._preset_menu.addAction(add_act)

    def update_preset_menu(self, installed_presets: list[str], active_preset: str) -> None:
        self._rebuild_preset_menu(installed_presets, active_preset)

    def set_active_preset(self, preset: str) -> None:
        if preset in self._preset_actions:
            self._preset_actions[preset].setChecked(True)

    def _on_preset_selected(self, preset: str) -> None:
        if self._preset_change_callback:
            self._preset_change_callback(preset)

    def _on_add_preset(self) -> None:
        if self._preset_add_callback:
            self._preset_add_callback()

    def set_current_vram_mode(self, mode: str) -> None:
        if mode in self._vram_actions:
            self._vram_actions[mode].setChecked(True)

    def _on_vram_mode_selected(self, mode: str) -> None:
        if self._vram_mode_callback:
            self._vram_mode_callback(mode)

    def set_sage_attention_callback(self, cb) -> None:
        self._sage_attention_callback = cb

    def set_sage_attention_checked(self, checked: bool) -> None:
        self._sage_attention_action.setChecked(checked)

    def set_sage_attention_available(self, available: bool) -> None:
        """セットアップ時のインストール結果に応じてメニュー項目の有効/無効を切り替える。"""
        self._sage_attention_action.setEnabled(available)
        if not available:
            self._sage_attention_action.setChecked(False)
            self._sage_attention_action.setText("高速化 (SageAttention) を使う (この環境では未対応)")
        else:
            self._sage_attention_action.setText("高速化 (SageAttention) を使う")

    def _on_sage_attention_toggled(self, checked: bool) -> None:
        if self._sage_attention_callback:
            self._sage_attention_callback(checked)

    @Slot()
    def _try_paste_image(self) -> None:
        """クリップボードの画像をtempファイルに保存してD&Dと同じフローで処理する。"""
        if self._stack.currentIndex() not in (_PAGE_DROP, _PAGE_RESULT):
            return
        img: QImage = QApplication.clipboard().image()
        if img.isNull():
            return
        tmp = Path(tempfile.mktemp(suffix=".png", prefix="maf_clip_"))
        if not img.save(str(tmp), "PNG"):
            logger.warning("クリップボード画像の保存に失敗しました")
            return
        logger.info("クリップボードから画像を貼り付け: %s (%dx%d)", tmp, img.width(), img.height())
        self.image_dropped.emit(tmp)

    def show_finish_current_btn(self, callback) -> None:
        self._progress_view.show_finish_current(callback)

    def hide_finish_current_btn(self) -> None:
        self._progress_view.hide_finish_current()

    @Slot()
    def show_drop_page(self) -> None:
        self._result_view.stop_playback()
        self._progress_view.stop_elapsed()
        self._progress_view.hide_cancel()
        self._progress_view.hide_finish_current()
        self._drop_area.set_ready()
        self._stack.setCurrentIndex(_PAGE_DROP)
        self._status_bar.clearMessage()

    @Slot(str, float, str)
    def show_progress(self, message: str, percent: float = 0.0, detail: str = "") -> None:
        """セットアップ用 1バー進捗。"""
        self._stack.setCurrentIndex(_PAGE_PROGRESS)
        self._progress_view.update(message, percent, detail)

    @Slot(str)
    def show_progress_indeterminate(self, message: str) -> None:
        """セットアップ用不定プログレス。"""
        self._stack.setCurrentIndex(_PAGE_PROGRESS)
        self._progress_view.set_indeterminate(message)

    def enter_single_mode(self, image_path: Path | None = None) -> None:
        """単体生成モードに切り替え (2バー + 画像プレビュー + ETA)。"""
        self._result_view.stop_playback()
        self._stack.setCurrentIndex(_PAGE_PROGRESS)
        self._progress_view.enter_single(image_path)

    def enter_batch_mode(self) -> None:
        """バッチ生成モードに切り替え (3バー + 画像プレビュー + ETA)。"""
        self._stack.setCurrentIndex(_PAGE_PROGRESS)
        self._progress_view.enter_batch()

    def set_current_image(self, image_path: Path) -> None:
        """バッチ処理中に処理中の画像を更新する。"""
        self._progress_view.set_preview_image(image_path)

    def update_single_progress(
        self,
        message: str,
        overall_pct: float,
        task_pct: float,
        task_detail: str = "",
    ) -> None:
        self._progress_view.update_single(message, overall_pct, task_pct, task_detail)

    def update_batch_progress(
        self,
        message: str,
        all_pct: float,
        image_pct: float,
        task_pct: float,
        task_detail: str = "",
    ) -> None:
        self._progress_view.update_batch(message, all_pct, image_pct, task_pct, task_detail)

    @Slot(Path, str, float, float, float)
    def show_result(
        self,
        output_path: Path,
        source_stem: str = "",
        elapsed_sec: float = 0.0,
        vram_peak_gb: float = 0.0,
        vram_avg_gb: float = 0.0,
    ) -> None:
        self._progress_view.stop_elapsed()
        self._progress_view.hide_cancel()
        self._stack.setCurrentIndex(_PAGE_RESULT)
        self._result_view.show_result(output_path, source_stem, elapsed_sec, vram_peak_gb, vram_avg_gb)
        mins = int(elapsed_sec // 60)
        secs = int(elapsed_sec % 60)
        elapsed_str = f" ({mins}分{secs}秒)" if mins > 0 else (f" ({secs}秒)" if secs > 0 else "")
        vram_str = f" | VRAM peak: {vram_peak_gb:.1f}GB" if vram_peak_gb > 0 else ""
        self._status_bar.showMessage(f"完成: {output_path.name}{elapsed_str}{vram_str}")

    def show_error(self, title: str, message: str, detail: str = "", show_repair: bool = False) -> None:
        repair = ErrorDialog.show_error(title, message, detail, self, show_repair)
        if repair and self._repair_callback:
            self._repair_callback()

    def update_status(self, message: str) -> None:
        self._status_bar.showMessage(message)

    def start_elapsed_timer(self) -> None:
        self._progress_view.start_elapsed()

    def stop_elapsed_timer(self) -> None:
        self._progress_view.stop_elapsed()

    def show_cancel_btn(self, callback) -> None:
        self._progress_view.show_cancel(callback)

    def hide_cancel_btn(self) -> None:
        self._progress_view.hide_cancel()

    @Slot()
    def _on_request_again(self) -> None:
        self.show_drop_page()

    def _open_output_dir(self) -> None:
        if self._output_dir and self._output_dir.exists():
            os.startfile(str(self._output_dir))

    def _open_logs(self) -> None:
        if self._logs_dir and self._logs_dir.exists():
            os.startfile(str(self._logs_dir))

    def _show_diagnostics(self) -> None:
        QMessageBox.information(self, "runtime診断", self._system_info_text or "診断情報がありません。")

    def _change_install_location(self) -> None:
        if self._change_location_cb:
            self._change_location_cb()

    def _change_auto_save_folder(self) -> None:
        if self._auto_save_folder_cb:
            self._auto_save_folder_cb()

    def _request_repair(self) -> None:
        if self._repair_callback:
            result = QMessageBox.question(
                self, "runtimeを修復",
                "runtimeを修復しますか？\n（インターネット接続が必要な場合があります）",
            )
            if result == QMessageBox.StandardButton.Yes:
                self._repair_callback()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            f"{APP_NAME}について",
            f"{APP_NAME} v{APP_VERSION}\n\n"
            "画像をドラッグ＆ドロップするだけでAI動画を生成するアプリです。\n"
            "生成はすべてローカルPCで行われます。\n"
            "入力画像・生成動画が外部送信されることはありません。",
        )

    def closeEvent(self, event) -> None:
        self._result_view.stop_playback()
        super().closeEvent(event)

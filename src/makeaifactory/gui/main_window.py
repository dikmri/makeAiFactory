from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
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
        self.setMinimumSize(540, 460)
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

        settings_menu.addSeparator()
        vram_menu = settings_menu.addMenu("VRAMモード")
        self._vram_group = QActionGroup(self)
        self._vram_group.setExclusive(True)
        self._vram_actions: dict[str, QAction] = {}
        for mode, label in [
            ("normal", "通常モード (推奨: 16GB+)"),
            ("low",    "低VRAMモード --lowvram (8-15GB)"),
            ("novram", "超省VRAMモード --novram (～8GB / 低速)"),
        ]:
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(mode == "normal")
            act.triggered.connect(lambda checked, m=mode: self._on_vram_mode_selected(m))
            self._vram_group.addAction(act)
            vram_menu.addAction(act)
            self._vram_actions[mode] = act

        self._vram_mode_callback = None

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

        self._logs_dir: Path | None = None
        self._output_dir: Path | None = None
        self._system_info_text: str = ""
        self._repair_callback = None
        self._change_location_cb = None
        self._vram_mode_callback = None

    def set_paths(self, logs_dir: Path, output_dir: Path) -> None:
        self._logs_dir = logs_dir
        self._output_dir = output_dir

    def set_system_info(self, text: str) -> None:
        self._system_info_text = text

    def set_repair_callback(self, cb) -> None:
        self._repair_callback = cb

    def set_change_location_callback(self, cb) -> None:
        self._change_location_cb = cb

    def set_vram_mode_callback(self, cb) -> None:
        self._vram_mode_callback = cb

    def set_current_vram_mode(self, mode: str) -> None:
        if mode in self._vram_actions:
            self._vram_actions[mode].setChecked(True)

    def _on_vram_mode_selected(self, mode: str) -> None:
        if self._vram_mode_callback:
            self._vram_mode_callback(mode)

    @Slot()
    def show_drop_page(self) -> None:
        self._result_view.stop_playback()
        self._progress_view.stop_elapsed()
        self._progress_view.hide_cancel()
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

    def enter_single_mode(self) -> None:
        """単体生成モードに切り替え (2バー + ETA)。"""
        self._stack.setCurrentIndex(_PAGE_PROGRESS)
        self._progress_view.enter_single()

    def enter_batch_mode(self) -> None:
        """バッチ生成モードに切り替え (3バー + ETA)。"""
        self._stack.setCurrentIndex(_PAGE_PROGRESS)
        self._progress_view.enter_batch()

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

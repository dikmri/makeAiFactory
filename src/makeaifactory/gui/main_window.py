from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
    QWidget,
)

from ..constants import APP_NAME, APP_VERSION
from ..domain.progress import JobProgress, JobState, SetupProgress, SetupState
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

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(540, 420)
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
        """)

    def _build_ui(self) -> None:
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._drop_area = DropArea()
        self._drop_area.image_dropped.connect(self.image_dropped)
        self._stack.addWidget(self._drop_area)

        self._progress_view = ProgressView()
        self._stack.addWidget(self._progress_view)

        self._result_view = ResultView()
        self._result_view.request_again.connect(self._on_request_again)
        self._stack.addWidget(self._result_view)

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

    def set_paths(self, logs_dir: Path, output_dir: Path) -> None:
        self._logs_dir = logs_dir
        self._output_dir = output_dir

    def set_system_info(self, text: str) -> None:
        self._system_info_text = text

    def set_repair_callback(self, cb) -> None:
        self._repair_callback = cb

    def set_change_location_callback(self, cb) -> None:
        self._change_location_cb = cb

    @Slot()
    def show_drop_page(self) -> None:
        self._result_view.stop_playback()
        self._drop_area.set_ready()
        self._stack.setCurrentIndex(_PAGE_DROP)
        self._status_bar.clearMessage()

    @Slot(str, float, str)
    def show_progress(self, message: str, percent: float = 0.0, detail: str = "") -> None:
        self._stack.setCurrentIndex(_PAGE_PROGRESS)
        self._progress_view.update(message, percent, detail)

    @Slot(str)
    def show_progress_indeterminate(self, message: str) -> None:
        self._stack.setCurrentIndex(_PAGE_PROGRESS)
        self._progress_view.set_indeterminate(message)

    @Slot(Path)
    def show_result(self, output_path: Path) -> None:
        self._stack.setCurrentIndex(_PAGE_RESULT)
        self._result_view.show_result(output_path)
        self._status_bar.showMessage(f"完成: {output_path.name}")

    def show_error(self, title: str, message: str, detail: str = "", show_repair: bool = False) -> None:
        repair = ErrorDialog.show_error(title, message, detail, self, show_repair)
        if repair and self._repair_callback:
            self._repair_callback()

    def update_status(self, message: str) -> None:
        self._status_bar.showMessage(message)

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

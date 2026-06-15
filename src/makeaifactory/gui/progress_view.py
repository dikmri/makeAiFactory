from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget


class ProgressView(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        self._title = QLabel("処理中...")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setStyleSheet("color: #eee; font-size: 18px;")

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setMinimumWidth(300)
        self._bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #444;
                border-radius: 6px;
                background: #222;
                text-align: center;
                color: #eee;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4fc3f7, stop:1 #1565c0);
                border-radius: 5px;
            }
        """)

        self._detail = QLabel("")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail.setStyleSheet("color: #999; font-size: 13px;")
        self._detail.setWordWrap(True)

        self._elapsed_label = QLabel("")
        self._elapsed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._elapsed_label.setStyleSheet("color: #666; font-size: 12px;")
        self._elapsed_label.setVisible(False)

        self._cancel_btn = QPushButton("中断")
        self._cancel_btn.setStyleSheet("""
            QPushButton {
                background: #2a1a1a;
                color: #f88;
                border: 1px solid #a33;
                border-radius: 6px;
                padding: 6px 24px;
                font-size: 13px;
            }
            QPushButton:hover { background: #3a1a1a; border-color: #f44; color: #faa; }
        """)
        self._cancel_btn.setVisible(False)

        layout.addWidget(self._title)
        layout.addWidget(self._bar)
        layout.addWidget(self._detail)
        layout.addWidget(self._elapsed_label)
        layout.addWidget(self._cancel_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._start_mono: float = 0.0

    def update(self, message: str, percent: float = 0.0, detail: str = "") -> None:
        self._title.setText(message)
        self._bar.setRange(0, 100)
        self._bar.setValue(int(percent))
        self._detail.setText(detail)

    def set_indeterminate(self, message: str) -> None:
        self._title.setText(message)
        self._bar.setRange(0, 0)
        self._detail.setText("")

    def set_determinate(self) -> None:
        self._bar.setRange(0, 100)

    def start_elapsed(self) -> None:
        self._start_mono = time.monotonic()
        self._elapsed_label.setText("経過時間: 0秒")
        self._elapsed_label.setVisible(True)
        self._timer.start(1000)

    def stop_elapsed(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        elapsed = time.monotonic() - self._start_mono
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        if mins > 0:
            self._elapsed_label.setText(f"経過時間: {mins}分{secs}秒")
        else:
            self._elapsed_label.setText(f"経過時間: {secs}秒")

    def show_cancel(self, callback) -> None:
        try:
            self._cancel_btn.clicked.disconnect()
        except RuntimeError:
            pass
        self._cancel_btn.clicked.connect(callback)
        self._cancel_btn.setVisible(True)

    def hide_cancel(self) -> None:
        self._cancel_btn.setVisible(False)

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget


class ProgressView(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

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

        layout.addWidget(self._title)
        layout.addWidget(self._bar)
        layout.addWidget(self._detail)

    def update(self, message: str, percent: float = 0.0, detail: str = "") -> None:
        self._title.setText(message)
        self._bar.setValue(int(percent))
        self._detail.setText(detail)

    def set_indeterminate(self, message: str) -> None:
        self._title.setText(message)
        self._bar.setRange(0, 0)
        self._detail.setText("")

    def set_determinate(self) -> None:
        self._bar.setRange(0, 100)

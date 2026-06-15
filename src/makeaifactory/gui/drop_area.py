from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QLabel, QWidget

from ..constants import SUPPORTED_IMAGE_EXTENSIONS

logger = logging.getLogger(__name__)


class DropArea(QLabel):
    image_dropped = Signal(Path)

    _STYLE_IDLE = """
        QLabel {
            border: 2px dashed #555;
            border-radius: 12px;
            background: #1a1a2e;
            color: #aaa;
            font-size: 16px;
        }
    """
    _STYLE_HOVER = """
        QLabel {
            border: 2px dashed #4fc3f7;
            border-radius: 12px;
            background: #162447;
            color: #4fc3f7;
            font-size: 16px;
        }
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)
        self.setMinimumSize(420, 280)
        self._set_idle_state()

    def _set_idle_state(self) -> None:
        self.setText(
            "画像をここにドロップ\n"
            "または Ctrl+V で貼り付け\n\n"
            "PNG / JPG / JPEG / WEBP\n\n"
            "初回のみセットアップが実行されます"
        )
        self.setStyleSheet(self._STYLE_IDLE)

    def set_busy(self) -> None:
        self.setText("生成中...")
        self.setStyleSheet(self._STYLE_IDLE)
        self.setAcceptDrops(False)

    def set_ready(self) -> None:
        self._set_idle_state()
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(Path(u.toLocalFile()).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS for u in urls):
                event.acceptProposedAction()
                self.setStyleSheet(self._STYLE_HOVER)
                return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self.setStyleSheet(self._STYLE_IDLE)

    def dropEvent(self, event: QDropEvent) -> None:
        self.setStyleSheet(self._STYLE_IDLE)
        urls = event.mimeData().urls()
        for url in urls:
            path = Path(url.toLocalFile())
            if path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                logger.info("画像ドロップ: %s", path)
                self.image_dropped.emit(path)
                return
        logger.warning("対応していないファイル形式です")

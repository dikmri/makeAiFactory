from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class ResultView(QWidget):
    request_again = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

        title = QLabel("動画が完成しました")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #eee; font-size: 20px; font-weight: bold;")
        layout.addWidget(title)

        self._video_widget = QVideoWidget()
        self._video_widget.setMinimumSize(480, 270)
        self._video_widget.setStyleSheet("background: #000; border-radius: 8px;")
        layout.addWidget(self._video_widget)

        self._player = QMediaPlayer()
        self._player.setVideoOutput(self._video_widget)
        # 無限ループ再生 (-1 = QMediaPlayer.Loops.Infinite)
        self._player.setLoops(-1)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        self._save_btn = QPushButton("名前を付けて保存")
        self._save_btn.setStyleSheet(self._btn_style())
        self._save_btn.clicked.connect(self._save_as)

        self._open_btn = QPushButton("保存フォルダを開く")
        self._open_btn.setStyleSheet(self._btn_style())
        self._open_btn.clicked.connect(self._open_folder)

        self._again_btn = QPushButton("もう一度作る")
        self._again_btn.setStyleSheet(self._btn_style())
        self._again_btn.clicked.connect(self.request_again)

        btn_layout.addWidget(self._save_btn)
        btn_layout.addWidget(self._open_btn)
        btn_layout.addWidget(self._again_btn)
        layout.addLayout(btn_layout)

        self._output_path: Path | None = None

    def _btn_style(self) -> str:
        return """
            QPushButton {
                background: #1565c0;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 24px;
                font-size: 14px;
            }
            QPushButton:hover { background: #1976d2; }
            QPushButton:pressed { background: #0d47a1; }
        """

    def show_result(self, output_path: Path) -> None:
        self._output_path = output_path
        try:
            self._player.setSource(QUrl.fromLocalFile(str(output_path)))
            self._player.play()
        except Exception as e:
            logger.warning("動画再生失敗: %s", e)

    def _save_as(self) -> None:
        if not self._output_path:
            return
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "動画を保存",
            self._output_path.name,
            "動画ファイル (*.mp4);;すべてのファイル (*)",
        )
        if dest:
            try:
                shutil.copy2(str(self._output_path), dest)
                logger.info("動画を保存しました: %s", dest)
            except Exception as e:
                logger.warning("動画保存失敗: %s", e)

    def _open_folder(self) -> None:
        if self._output_path:
            os.startfile(str(self._output_path.parent))

    def stop_playback(self) -> None:
        self._player.stop()

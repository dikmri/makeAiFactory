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
        layout.setSpacing(12)

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
        self._player.setLoops(-1)

        self._time_label = QLabel("")
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._time_label.setStyleSheet("color: #888; font-size: 13px;")
        self._time_label.setVisible(False)
        layout.addWidget(self._time_label)

        self._bench_label = QLabel("")
        self._bench_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bench_label.setStyleSheet("color: #666; font-size: 12px;")
        self._bench_label.setVisible(False)
        layout.addWidget(self._bench_label)

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
        self._source_stem: str = ""

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

    def show_result(
        self,
        output_path: Path,
        source_stem: str = "",
        elapsed_sec: float = 0.0,
        vram_peak_gb: float = 0.0,
        vram_avg_gb: float = 0.0,
    ) -> None:
        self._output_path = output_path
        self._source_stem = source_stem
        if elapsed_sec > 0:
            mins = int(elapsed_sec // 60)
            secs = int(elapsed_sec % 60)
            time_str = f"生成時間: {mins}分{secs}秒" if mins > 0 else f"生成時間: {secs}秒"
            self._time_label.setText(time_str)
            self._time_label.setVisible(True)
        else:
            self._time_label.setVisible(False)

        if vram_peak_gb > 0:
            bench_str = f"VRAMピーク: {vram_peak_gb:.1f} GB  |  平均: {vram_avg_gb:.1f} GB"
            self._bench_label.setText(bench_str)
            self._bench_label.setVisible(True)
        else:
            self._bench_label.setVisible(False)
        try:
            self._player.setSource(QUrl.fromLocalFile(str(output_path)))
            self._player.play()
        except Exception as e:
            logger.warning("動画再生失敗: %s", e)

    def _save_as(self) -> None:
        if not self._output_path:
            return
        stem = self._source_stem or self._output_path.stem
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "動画を保存",
            f"{stem}.mp4",
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

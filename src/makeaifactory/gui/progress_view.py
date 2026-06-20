from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr, tr_elapsed

_BLUE = """
    QProgressBar {
        border: 1px solid #333; border-radius: 5px;
        background: #1a1a2e; text-align: center;
        color: #ccc; font-size: 12px; min-height: 20px;
    }
    QProgressBar::chunk {
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #4fc3f7, stop:1 #1565c0);
        border-radius: 4px;
    }
"""
_GREEN = """
    QProgressBar {
        border: 1px solid #333; border-radius: 5px;
        background: #1a1a2e; text-align: center;
        color: #ccc; font-size: 12px; min-height: 20px;
    }
    QProgressBar::chunk {
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #66bb6a, stop:1 #2e7d32);
        border-radius: 4px;
    }
"""
_AMBER = """
    QProgressBar {
        border: 1px solid #333; border-radius: 5px;
        background: #1a1a2e; text-align: center;
        color: #ccc; font-size: 12px; min-height: 20px;
    }
    QProgressBar::chunk {
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #ffa726, stop:1 #e65100);
        border-radius: 4px;
    }
"""
_LABEL_BLUE  = "color: #4fc3f7; font-size: 11px;"
_LABEL_GREEN = "color: #66bb6a; font-size: 11px;"
_LABEL_AMBER = "color: #ffa726; font-size: 11px;"

_IMG_MAX_W = 360
_IMG_MAX_H = 220


class ProgressView(QWidget):
    """
    セットアップ : 1バー (青) / 画像プレビューなし
    単体生成     : 2バー — 青=全体進捗, 橙=現在のステップ / 入力画像表示
    バッチ生成   : 3バー — 青=全体進捗, 緑=現在の画像, 橙=現在のステップ / 処理中画像表示
    """

    _SETUP  = "setup"
    _SINGLE = "single"
    _BATCH  = "batch"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)
        layout.setContentsMargins(40, 16, 40, 16)

        # ── 画像プレビュー ────────────────────────────────────────────────
        self._img_preview = QLabel()
        self._img_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_preview.setMaximumWidth(_IMG_MAX_W + 8)
        self._img_preview.setStyleSheet("""
            QLabel {
                background: #070710;
                border: 1px solid #2a2a42;
                border-radius: 8px;
                padding: 4px;
            }
        """)
        self._img_preview.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        self._img_preview.setVisible(False)
        layout.addWidget(self._img_preview, alignment=Qt.AlignmentFlag.AlignCenter)

        # ファイル名ラベル
        self._img_name = QLabel("")
        self._img_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_name.setStyleSheet("color: #666; font-size: 11px;")
        self._img_name.setVisible(False)
        layout.addWidget(self._img_name)

        layout.addSpacing(4)

        # ── タイトル ─────────────────────────────────────────────────────
        self._title = QLabel(tr("処理中..."))
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setStyleSheet("color: #eee; font-size: 15px; font-weight: bold;")
        self._title.setWordWrap(True)
        layout.addWidget(self._title)
        layout.addSpacing(4)

        # ── バー1: 全体進捗 (blue) — 常時表示 ────────────────────────────
        self._lbl1 = QLabel(tr("全体進捗"))
        self._lbl1.setStyleSheet(_LABEL_BLUE)
        self._bar1 = self._mk_bar(_BLUE)
        layout.addWidget(self._lbl1)
        layout.addWidget(self._bar1)

        # ── バー2: 現在の画像 (green) — バッチ時のみ ─────────────────────
        self._lbl2 = QLabel(tr("現在の画像"))
        self._lbl2.setStyleSheet(_LABEL_GREEN)
        self._bar2 = self._mk_bar(_GREEN)
        layout.addWidget(self._lbl2)
        layout.addWidget(self._bar2)

        # ── バー3: 現在のステップ (amber) — 単体/バッチ時 ────────────────
        self._lbl3 = QLabel(tr("現在のステップ"))
        self._lbl3.setStyleSheet(_LABEL_AMBER)
        self._bar3 = self._mk_bar(_AMBER)
        layout.addWidget(self._lbl3)
        layout.addWidget(self._bar3)

        layout.addSpacing(4)

        # ── 経過時間 / 予測完了時間 ───────────────────────────────────────
        self._eta = QLabel("")
        self._eta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._eta.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self._eta)

        # ── 現在の生成で終了ボタン（バッチ専用）────────────────────────────
        self._finish_current_btn = QPushButton(tr("現在の生成で終了"))
        self._finish_current_btn.setStyleSheet("""
            QPushButton {
                background: #1a1500; color: #ffa726;
                border: 1px solid #7a5900; border-radius: 6px;
                padding: 6px 24px; font-size: 13px;
            }
            QPushButton:hover { background: #2a2100; border-color: #ffc107; color: #ffd54f; }
        """)
        self._finish_current_btn.setVisible(False)
        layout.addWidget(self._finish_current_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # ── 中断ボタン ────────────────────────────────────────────────────
        self._cancel_btn = QPushButton(tr("中断"))
        self._cancel_btn.setStyleSheet("""
            QPushButton {
                background: #2a1a1a; color: #f88;
                border: 1px solid #a33; border-radius: 6px;
                padding: 6px 24px; font-size: 13px;
            }
            QPushButton:hover { background: #3a1a1a; border-color: #f44; color: #faa; }
        """)
        self._cancel_btn.setVisible(False)
        layout.addWidget(self._cancel_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._mode = self._SETUP
        self._overall_pct: float = 0.0
        self._start_mono: float = time.monotonic()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self._apply_setup_layout()

    @staticmethod
    def _mk_bar(style: str) -> QProgressBar:
        b = QProgressBar()
        b.setRange(0, 100)
        b.setValue(0)
        b.setMinimumWidth(400)
        b.setFixedHeight(22)
        b.setStyleSheet(style)
        b.setFormat("%p%")
        return b

    # ── 画像プレビュー ────────────────────────────────────────────────────

    def set_preview_image(self, path: Path | None) -> None:
        """入力画像をプレビュー表示する。None で非表示。"""
        if path is None or not path.exists():
            self._img_preview.clear()
            self._img_preview.setVisible(False)
            self._img_name.setVisible(False)
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._img_preview.setVisible(False)
            self._img_name.setVisible(False)
            return
        scaled = pixmap.scaled(
            _IMG_MAX_W, _IMG_MAX_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._img_preview.setPixmap(scaled)
        self._img_preview.setVisible(True)
        # ファイル名 (長い場合は省略)
        name = path.name
        if len(name) > 40:
            name = name[:37] + "..."
        self._img_name.setText(name)
        self._img_name.setVisible(True)

    # ── レイアウト切り替え ────────────────────────────────────────────────

    def _apply_setup_layout(self) -> None:
        self._img_preview.setVisible(False)
        self._img_name.setVisible(False)
        self._finish_current_btn.setVisible(False)
        self._lbl1.setText(tr("進捗"))
        self._lbl1.setVisible(True)
        self._bar1.setVisible(True)
        for w in (self._lbl2, self._bar2, self._lbl3, self._bar3):
            w.setVisible(False)

    def enter_single(self, image_path: Path | None = None) -> None:
        """単体生成モードに切り替え (2バー + 画像プレビュー)。"""
        self._mode = self._SINGLE
        self._overall_pct = 0.0
        self._start_mono = time.monotonic()
        self._finish_current_btn.setVisible(False)
        self._lbl1.setText(tr("全体進捗"))
        self._bar1.setRange(0, 100)
        self._bar1.setValue(0)
        for w in (self._lbl1, self._bar1, self._lbl3, self._bar3):
            w.setVisible(True)
        for w in (self._lbl2, self._bar2):
            w.setVisible(False)
        self._bar3.setRange(0, 0)  # indeterminate until generation starts
        self._eta.setText("")
        self.set_preview_image(image_path)
        self._timer.start(500)

    def enter_batch(self) -> None:
        """バッチ生成モードに切り替え (3バー + 画像プレビュー)。"""
        self._mode = self._BATCH
        self._overall_pct = 0.0
        self._start_mono = time.monotonic()
        self._lbl1.setText(tr("全体進捗"))
        self._lbl2.setText(tr("現在の画像"))
        self._bar1.setRange(0, 100)
        self._bar1.setValue(0)
        self._bar2.setRange(0, 100)
        self._bar2.setValue(0)
        for w in (self._lbl1, self._bar1, self._lbl2, self._bar2, self._lbl3, self._bar3):
            w.setVisible(True)
        self._bar3.setRange(0, 0)
        self._eta.setText("")
        self._img_preview.clear()
        self._img_preview.setVisible(False)
        self._img_name.setVisible(False)
        self._timer.start(500)

    # ── 更新 ──────────────────────────────────────────────────────────────

    def update_setup(self, message: str, percent: float = -1.0) -> None:
        """セットアップ進捗 (1バー)。percent < 0 で不定。"""
        if self._mode != self._SETUP:
            self._mode = self._SETUP
            self._apply_setup_layout()
        self._title.setText(message)
        if percent < 0:
            self._bar1.setRange(0, 0)
        else:
            self._bar1.setRange(0, 100)
            self._bar1.setValue(int(percent))

    def update_single(
        self,
        message: str,
        overall_pct: float,
        task_pct: float,
        task_detail: str = "",
    ) -> None:
        """単体生成の進捗更新。task_pct < 0 で不定。"""
        self._title.setText(message)
        self._overall_pct = overall_pct
        self._bar1.setRange(0, 100)
        self._bar1.setValue(int(overall_pct))
        if task_pct < 0:
            self._bar3.setRange(0, 0)
        else:
            self._bar3.setRange(0, 100)
            self._bar3.setValue(int(task_pct))

    def update_batch(
        self,
        message: str,
        all_pct: float,
        image_pct: float,
        task_pct: float,
        task_detail: str = "",
    ) -> None:
        """バッチ生成の進捗更新。task_pct < 0 で不定。"""
        self._title.setText(message)
        self._overall_pct = all_pct
        self._bar1.setRange(0, 100)
        self._bar1.setValue(int(all_pct))
        self._bar2.setRange(0, 100)
        self._bar2.setValue(int(image_pct))
        if task_pct < 0:
            self._bar3.setRange(0, 0)
        else:
            self._bar3.setRange(0, 100)
            self._bar3.setValue(int(task_pct))

    # ── タイマー ──────────────────────────────────────────────────────────

    def _tick(self) -> None:
        elapsed = time.monotonic() - self._start_mono
        elapsed_str = tr_elapsed(elapsed)
        eta_str = ""
        if self._mode in (self._SINGLE, self._BATCH) and self._overall_pct > 3:
            try:
                remaining = elapsed / (self._overall_pct / 100) * (1 - self._overall_pct / 100)
                if remaining > 0:
                    eta_str = tr("予測完了: 約{remaining}").format(remaining=tr_elapsed(remaining))
            except ZeroDivisionError:
                pass
        text = tr("経過: {elapsed}").format(elapsed=elapsed_str)
        if eta_str:
            text += f"  |  {eta_str}"
        self._eta.setText(text)

    def stop(self) -> None:
        self._timer.stop()

    # ── 旧 API (後方互換) ─────────────────────────────────────────────────

    def update(self, message: str, percent: float = 0.0, detail: str = "") -> None:
        self.update_setup(message, percent)

    def set_indeterminate(self, message: str) -> None:
        self.update_setup(message, -1.0)

    def set_determinate(self) -> None:
        self._bar1.setRange(0, 100)

    def start_elapsed(self) -> None:
        self._start_mono = time.monotonic()
        self._timer.start(500)

    def stop_elapsed(self) -> None:
        self.stop()

    # ── 中断ボタン ────────────────────────────────────────────────────────

    def show_finish_current(self, callback) -> None:
        try:
            self._finish_current_btn.clicked.disconnect()
        except RuntimeError:
            pass
        self._finish_current_btn.clicked.connect(callback)
        self._finish_current_btn.setText(tr("現在の生成で終了"))
        self._finish_current_btn.setVisible(True)

    def set_finish_current_text(self, text: str) -> None:
        self._finish_current_btn.setText(text)

    def hide_finish_current(self) -> None:
        self._finish_current_btn.setVisible(False)

    def show_cancel(self, callback) -> None:
        try:
            self._cancel_btn.clicked.disconnect()
        except RuntimeError:
            pass
        self._cancel_btn.clicked.connect(callback)
        self._cancel_btn.setVisible(True)

    def hide_cancel(self) -> None:
        self._cancel_btn.setVisible(False)

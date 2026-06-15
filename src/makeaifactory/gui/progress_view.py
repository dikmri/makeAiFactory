from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

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


def _fmt(sec: float) -> str:
    m, s = int(sec // 60), int(sec % 60)
    return f"{m}分{s}秒" if m > 0 else f"{s}秒"


class ProgressView(QWidget):
    """
    セットアップ : 1バー (青)
    単体生成     : 2バー — 青=全体進捗, 橙=現在のステップ
    バッチ生成   : 3バー — 青=全体進捗, 緑=現在の画像, 橙=現在のステップ
    """

    _SETUP  = "setup"
    _SINGLE = "single"
    _BATCH  = "batch"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)
        layout.setContentsMargins(40, 20, 40, 20)

        # タイトル
        self._title = QLabel("処理中...")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setStyleSheet("color: #eee; font-size: 16px; font-weight: bold;")
        self._title.setWordWrap(True)
        layout.addWidget(self._title)
        layout.addSpacing(6)

        # バー1: 全体進捗 (blue) — 常時表示
        self._lbl1 = QLabel("全体進捗")
        self._lbl1.setStyleSheet(_LABEL_BLUE)
        self._bar1 = self._mk_bar(_BLUE)
        layout.addWidget(self._lbl1)
        layout.addWidget(self._bar1)

        # バー2: 現在の画像 (green) — バッチ時のみ
        self._lbl2 = QLabel("現在の画像")
        self._lbl2.setStyleSheet(_LABEL_GREEN)
        self._bar2 = self._mk_bar(_GREEN)
        layout.addWidget(self._lbl2)
        layout.addWidget(self._bar2)

        # バー3: 現在のステップ (amber) — 単体/バッチ時
        self._lbl3 = QLabel("現在のステップ")
        self._lbl3.setStyleSheet(_LABEL_AMBER)
        self._bar3 = self._mk_bar(_AMBER)
        layout.addWidget(self._lbl3)
        layout.addWidget(self._bar3)

        layout.addSpacing(4)

        # 経過時間 / 予測完了時間
        self._eta = QLabel("")
        self._eta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._eta.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self._eta)

        # 中断ボタン
        self._cancel_btn = QPushButton("中断")
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

    # ── レイアウト切り替え ─────────────────────────────────────────────

    def _apply_setup_layout(self) -> None:
        self._lbl1.setText("進捗")
        self._lbl1.setVisible(True)
        self._bar1.setVisible(True)
        for w in (self._lbl2, self._bar2, self._lbl3, self._bar3):
            w.setVisible(False)

    def enter_single(self) -> None:
        """単体生成モードに切り替え (2バー)。"""
        self._mode = self._SINGLE
        self._overall_pct = 0.0
        self._start_mono = time.monotonic()
        self._lbl1.setText("全体進捗")
        self._bar1.setRange(0, 100)
        self._bar1.setValue(0)
        for w in (self._lbl1, self._bar1, self._lbl3, self._bar3):
            w.setVisible(True)
        for w in (self._lbl2, self._bar2):
            w.setVisible(False)
        self._bar3.setRange(0, 0)  # indeterminate until generation starts
        self._eta.setText("")
        self._timer.start(500)

    def enter_batch(self) -> None:
        """バッチ生成モードに切り替え (3バー)。"""
        self._mode = self._BATCH
        self._overall_pct = 0.0
        self._start_mono = time.monotonic()
        self._lbl1.setText("全体進捗")
        self._lbl2.setText("現在の画像")
        self._bar1.setRange(0, 100)
        self._bar1.setValue(0)
        self._bar2.setRange(0, 100)
        self._bar2.setValue(0)
        for w in (self._lbl1, self._bar1, self._lbl2, self._bar2, self._lbl3, self._bar3):
            w.setVisible(True)
        self._bar3.setRange(0, 0)
        self._eta.setText("")
        self._timer.start(500)

    # ── 更新 ──────────────────────────────────────────────────────────

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

    # ── タイマー ──────────────────────────────────────────────────────

    def _tick(self) -> None:
        elapsed = time.monotonic() - self._start_mono
        elapsed_str = _fmt(elapsed)
        eta_str = ""
        if self._mode in (self._SINGLE, self._BATCH) and self._overall_pct > 3:
            try:
                remaining = elapsed / (self._overall_pct / 100) * (1 - self._overall_pct / 100)
                if remaining > 0:
                    eta_str = f"予測完了: 約{_fmt(remaining)}"
            except ZeroDivisionError:
                pass
        text = f"経過: {elapsed_str}"
        if eta_str:
            text += f"  |  {eta_str}"
        self._eta.setText(text)

    def stop(self) -> None:
        self._timer.stop()

    # ── 旧 API (後方互換) ──────────────────────────────────────────────

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

    # ── 中断ボタン ────────────────────────────────────────────────────

    def show_cancel(self, callback) -> None:
        try:
            self._cancel_btn.clicked.disconnect()
        except RuntimeError:
            pass
        self._cancel_btn.clicked.connect(callback)
        self._cancel_btn.setVisible(True)

    def hide_cancel(self) -> None:
        self._cancel_btn.setVisible(False)

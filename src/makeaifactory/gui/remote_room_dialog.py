"""インターネット投入口 β ダイアログ。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


def make_qr_pixmap(qr_url: str, target_size: int = 240) -> QPixmap | None:
    """qrcode の行列から Qt で直接 QR コード画像を生成する (PIL 不要)。

    PIL バイナリが frozen ビルドで読み込めないケースに対応するため、
    qrcode.get_matrix() で取得した真偽値行列を QPainter で直接描画する。
    """
    try:
        from qrcode import QRCode  # type: ignore[import]
        qr = QRCode(border=2)
        qr.add_data(qr_url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        n = len(matrix)
        cell = max(4, target_size // n)
        img_size = n * cell

        qimg = QImage(img_size, img_size, QImage.Format.Format_RGB888)
        qimg.fill(0xFFFFFF)
        painter = QPainter(qimg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0))
        for r, row in enumerate(matrix):
            for c, filled in enumerate(row):
                if filled:
                    painter.drawRect(QRect(c * cell, r * cell, cell, cell))
        painter.end()
        return QPixmap.fromImage(qimg)
    except Exception:
        logger.exception("QR コード生成失敗")
        return None


_STYLE = """
QDialog, QWidget {
    background: #0f0f1a;
    color: #eee;
    font-family: "Yu Gothic UI", "Meiryo", sans-serif;
}
QGroupBox {
    border: 1px solid #333;
    border-radius: 6px;
    margin-top: 12px;
    padding: 12px 10px 8px;
    font-size: 13px;
    color: #aaa;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QLabel { color: #ccc; }
QComboBox {
    background: #1a1a2e;
    color: #eee;
    border: 1px solid #444;
    border-radius: 4px;
    padding: 4px 8px;
    min-width: 120px;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView { background: #1a1a2e; color: #eee; selection-background-color: #253858; }
QPushButton {
    background: #1a1a2e;
    color: #ccc;
    border: 1px solid #444;
    border-radius: 4px;
    padding: 5px 14px;
    min-width: 80px;
}
QPushButton:hover { background: #253858; }
QPushButton:disabled { color: #555; background: #111; border-color: #333; }
QPushButton#startBtn {
    background: #1565c0;
    color: #fff;
    border: none;
    font-weight: bold;
    padding: 8px 20px;
}
QPushButton#startBtn:hover { background: #1976d2; }
QPushButton#stopBtn {
    background: #b71c1c;
    color: #fff;
    border: none;
    font-weight: bold;
    padding: 8px 20px;
}
QPushButton#stopBtn:hover { background: #c62828; }
"""


class RemoteRoomDialog(QDialog):
    start_requested  = Signal(dict)   # config dict
    stop_requested   = Signal()
    cancel_job       = Signal()
    stop_accepting   = Signal()
    clear_queue      = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("インターネット投入口 β")
        self.setMinimumWidth(480)
        self.setStyleSheet(_STYLE)
        self._on_start_cb: Callable | None = None
        self._on_stop_cb: Callable | None = None
        self._is_running = False
        self._build_ui()

        # モニタの高さに収まるようダイアログ自体の高さを制限する (内部はスクロール可能)
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail_h = screen.availableGeometry().height()
            target_h = min(720, avail_h - 80)
            self.resize(self.width(), target_h)
            self.setMaximumHeight(avail_h - 40)

    # ── UI 構築 ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setSpacing(8)
        outer_layout.setContentsMargins(16, 16, 16, 16)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer_layout.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # 説明
        desc = QLabel(
            "一時URLを発行し、離れた場所にいる人がブラウザから画像を\n"
            "アップロードして動画生成できるようにします。\n"
            "Cloudflare Quick Tunnel を使用します（Cloudflareアカウント不要）。"
        )
        desc.setStyleSheet("font-size:12px;color:#888;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # 状態表示
        status_box = QGroupBox("状態")
        sb_layout = QVBoxLayout(status_box)
        self._status_label = QLabel("● 停止中")
        self._status_label.setStyleSheet("font-size:14px;font-weight:bold;color:#666;")
        sb_layout.addWidget(self._status_label)

        self._url_label = QLabel("")
        self._url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._url_label.setStyleSheet("font-size:12px;color:#4fc3f7;")
        self._url_label.setWordWrap(True)
        sb_layout.addWidget(self._url_label)

        self._pin_label = QLabel("")
        self._pin_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._pin_label.setStyleSheet("font-size:12px;color:#aaa;")
        sb_layout.addWidget(self._pin_label)

        layout.addWidget(status_box)

        # QR コード
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.hide()
        layout.addWidget(self._qr_label)

        self._qr_hint_label = QLabel()
        self._qr_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_hint_label.setStyleSheet("font-size:12px;color:#aaa;margin-bottom:4px;")
        self._qr_hint_label.hide()
        layout.addWidget(self._qr_hint_label)

        # コピーボタン群
        copy_row = QHBoxLayout()
        self._copy_url_btn = QPushButton("URL をコピー")
        self._copy_url_btn.clicked.connect(self._copy_url)
        self._copy_url_btn.setEnabled(False)
        copy_row.addWidget(self._copy_url_btn)

        self._copy_both_btn = QPushButton("URL + PIN をコピー")
        self._copy_both_btn.clicked.connect(self._copy_both)
        self._copy_both_btn.setEnabled(False)
        copy_row.addWidget(self._copy_both_btn)
        layout.addLayout(copy_row)

        # 設定
        config_box = QGroupBox("公開設定")
        config_layout = QFormLayout(config_box)
        config_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._ttl_combo = QComboBox()
        for label, val in [("1時間", 60), ("3時間 (推奨)", 180), ("6時間", 360)]:
            self._ttl_combo.addItem(label, val)
        self._ttl_combo.setCurrentIndex(1)
        config_layout.addRow("有効期限:", self._ttl_combo)

        self._pin_combo = QComboBox()
        self._pin_combo.addItem("QR + PIN (推奨)", True)
        self._pin_combo.addItem("QR のみ", False)
        config_layout.addRow("認証:", self._pin_combo)

        self._queue_combo = QComboBox()
        for v in [1, 3, 5]:
            self._queue_combo.addItem(str(v) + "件", v)
        self._queue_combo.setCurrentIndex(1)
        config_layout.addRow("最大待ち件数:", self._queue_combo)

        self._cooldown_combo = QComboBox()
        for label, val in [("5分", 300), ("10分 (推奨)", 600), ("30分", 1800)]:
            self._cooldown_combo.addItem(label, val)
        self._cooldown_combo.setCurrentIndex(1)
        config_layout.addRow("1人あたりの連投制限:", self._cooldown_combo)

        layout.addWidget(config_box)

        # 稼働状況
        stats_box = QGroupBox("稼働状況")
        stats_layout = QFormLayout(stats_box)
        stats_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._stat_queued   = QLabel("0")
        self._stat_running  = QLabel("0")
        self._stat_completed = QLabel("0")
        self._stat_failed   = QLabel("0")
        stats_layout.addRow("待機:", self._stat_queued)
        stats_layout.addRow("生成中:", self._stat_running)
        stats_layout.addRow("完了:", self._stat_completed)
        stats_layout.addRow("失敗:", self._stat_failed)
        layout.addWidget(stats_box)

        # 緊急操作
        emg_box = QGroupBox("緊急操作")
        emg_layout = QHBoxLayout(emg_box)
        self._stop_accept_btn = QPushButton("受付停止")
        self._stop_accept_btn.clicked.connect(self._on_stop_accepting)
        self._stop_accept_btn.setEnabled(False)
        emg_layout.addWidget(self._stop_accept_btn)

        self._cancel_job_btn = QPushButton("生成を中断")
        self._cancel_job_btn.clicked.connect(self.cancel_job)
        self._cancel_job_btn.setEnabled(False)
        emg_layout.addWidget(self._cancel_job_btn)

        self._clear_queue_btn = QPushButton("キューを消去")
        self._clear_queue_btn.clicked.connect(self._on_clear_queue)
        self._clear_queue_btn.setEnabled(False)
        emg_layout.addWidget(self._clear_queue_btn)
        layout.addWidget(emg_box)

        # 起動・停止ボタン
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("投入口を開始")
        self._start_btn.setObjectName("startBtn")
        self._start_btn.clicked.connect(self._on_start_clicked)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("投入口を停止")
        self._stop_btn.setObjectName("stopBtn")
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        self._stop_btn.setEnabled(False)
        btn_row.addWidget(self._stop_btn)
        layout.addLayout(btn_row)

        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.close)
        close_btn.setFixedWidth(100)
        outer_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    # ── 外部から呼ばれるメソッド ────────────────────────────────────────────────

    def set_start_callback(self, cb: Callable) -> None:
        self._on_start_cb = cb

    def set_stop_callback(self, cb: Callable) -> None:
        self._on_stop_cb = cb

    def update_status(self, status_code: str, message: str) -> None:
        color_map = {
            "stopped":  "#666",
            "starting": "#ff9800",
            "running":  "#4caf50",
            "error":    "#f44336",
        }
        icon_map = {
            "stopped":  "●",
            "starting": "◐",
            "running":  "●",
            "error":    "✕",
        }
        color = color_map.get(status_code, "#aaa")
        icon = icon_map.get(status_code, "●")
        self._status_label.setText(f"{icon} {message}")
        self._status_label.setStyleSheet(f"font-size:14px;font-weight:bold;color:{color};")

        is_running = status_code == "running"
        is_starting = status_code == "starting"
        active = is_running or is_starting

        self._start_btn.setEnabled(not active)
        self._stop_btn.setEnabled(active)
        self._ttl_combo.setEnabled(not active)
        self._pin_combo.setEnabled(not active)
        self._queue_combo.setEnabled(not active)
        self._cooldown_combo.setEnabled(not active)
        self._stop_accept_btn.setEnabled(is_running)
        self._cancel_job_btn.setEnabled(is_running)
        self._clear_queue_btn.setEnabled(is_running)
        self._is_running = active

        if not active:
            self._url_label.setText("")
            self._pin_label.setText("")
            self._qr_label.hide()
            self._qr_hint_label.hide()
            self._copy_url_btn.setEnabled(False)
            self._copy_both_btn.setEnabled(False)

    def set_public_url(self, url: str, pin: str) -> None:
        self._public_url = url
        self._public_pin = pin
        self._url_label.setText(f"URL: {url}")
        if pin:
            self._pin_label.setText(f"PIN: {pin}")
        else:
            self._pin_label.setText("PIN: 不要")
        self._copy_url_btn.setEnabled(True)
        self._copy_both_btn.setEnabled(bool(pin))
        self._generate_qr(url)

    def update_stats(self, stats: dict) -> None:
        self._stat_queued.setText(str(stats.get("queued", 0)))
        self._stat_running.setText(str(stats.get("running", 0)))
        self._stat_completed.setText(str(stats.get("completed", 0)))
        self._stat_failed.setText(str(stats.get("failed", 0)))

    def show_error_msg(self, message: str) -> None:
        QMessageBox.warning(self, "投入口エラー", message)

    # ── プライベートメソッド ────────────────────────────────────────────────────

    def _on_start_clicked(self) -> None:
        config = {
            "room_ttl_minutes": self._ttl_combo.currentData(),
            "require_pin": self._pin_combo.currentData(),
            "max_upload_mb": 20,
            "max_queue_size": self._queue_combo.currentData(),
            "per_session_cooldown_seconds": self._cooldown_combo.currentData(),
            "output_retention_hours": 24,
        }
        if self._on_start_cb:
            self._on_start_cb(config)

    def _on_stop_clicked(self) -> None:
        if QMessageBox.question(
            self, "投入口を停止",
            "投入口を停止しますか？\n接続中のユーザーは切断され、URLは無効になります。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            if self._on_stop_cb:
                self._on_stop_cb()

    def _on_stop_accepting(self) -> None:
        self._stop_accept_btn.setEnabled(False)
        self.stop_accepting.emit()

    def _on_clear_queue(self) -> None:
        if QMessageBox.question(
            self, "キューを消去",
            "待機中のリクエストをすべてキャンセルしますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            self.clear_queue.emit()

    def _copy_url(self) -> None:
        if hasattr(self, "_public_url"):
            QApplication.clipboard().setText(self._public_url)

    def _copy_both(self) -> None:
        if hasattr(self, "_public_url") and hasattr(self, "_public_pin"):
            text = f"URL: {self._public_url}\nPIN: {self._public_pin}"
            QApplication.clipboard().setText(text)

    def _generate_qr(self, url: str) -> None:
        pin = getattr(self, "_public_pin", "")
        qr_url = f"{url}?pin={pin}" if pin else url
        pixmap = make_qr_pixmap(qr_url, 200)
        if pixmap:
            self._qr_label.setPixmap(pixmap)
            self._qr_label.show()
            hint = "📱 スキャンで入室 (PIN 自動入力)" if pin else "📱 スキャンして入室"
            self._qr_hint_label.setText(hint)
            self._qr_hint_label.show()
        else:
            self._qr_label.hide()
            self._qr_hint_label.hide()

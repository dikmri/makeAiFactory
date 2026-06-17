"""Discord Bot 設定ダイアログ。

トークン・チャンネルID・有効/無効をアプリ UI で設定できる。
save_requested シグナルで app.py に設定値を渡し、Bot の再起動を委譲する。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.settings_store import SettingsStore


class DiscordSettingsDialog(QDialog):
    save_requested = Signal(bool, str, list)  # enabled, token, channel_ids(list[int])

    def __init__(self, settings: SettingsStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Discord Bot 設定")
        self.setMinimumWidth(480)
        self.setModal(True)
        self._settings = settings
        self._build_ui()
        self._load_from_settings()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # ── 有効/無効チェックボックス ─────────────────────────────────
        self._enabled_cb = QCheckBox("Discord Bot を有効にする")
        self._enabled_cb.setStyleSheet("font-size: 14px;")
        layout.addWidget(self._enabled_cb)

        layout.addSpacing(4)

        # ── トークン入力 ─────────────────────────────────────────────
        layout.addWidget(self._make_label("Bot トークン:"))
        token_row = QHBoxLayout()
        self._token_edit = QLineEdit()
        self._token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._token_edit.setPlaceholderText("Discord Bot のトークンを貼り付けてください")
        self._token_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        token_row.addWidget(self._token_edit)

        self._show_token_btn = QPushButton("表示")
        self._show_token_btn.setCheckable(True)
        self._show_token_btn.setFixedWidth(60)
        self._show_token_btn.toggled.connect(self._on_show_token_toggled)
        token_row.addWidget(self._show_token_btn)
        layout.addLayout(token_row)

        hint_token = QLabel("ℹ  Discord Developer Portal → アプリ → Bot → Token でコピー")
        hint_token.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint_token)

        layout.addSpacing(4)

        # ── チャンネルID 入力 ─────────────────────────────────────────
        layout.addWidget(self._make_label("監視チャンネルID（カンマ区切り、空欄 = 全チャンネル）:"))
        self._channels_edit = QLineEdit()
        self._channels_edit.setPlaceholderText("例: 1234567890, 9876543210")
        layout.addWidget(self._channels_edit)

        hint_ch = QLabel("ℹ  Discord 設定→詳細→開発者モードを有効化し、チャンネルを右クリック→IDをコピー")
        hint_ch.setStyleSheet("color: #666; font-size: 11px;")
        hint_ch.setWordWrap(True)
        layout.addWidget(hint_ch)

        layout.addSpacing(8)

        # ── Bot 状態表示 ──────────────────────────────────────────────
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Bot 状態:"))
        self._status_lbl = QLabel("未確認")
        self._status_lbl.setStyleSheet("color: #aaa; font-size: 12px;")
        status_row.addWidget(self._status_lbl)
        status_row.addStretch()
        layout.addLayout(status_row)

        layout.addSpacing(8)

        # ── ボタン ────────────────────────────────────────────────────
        btn_box = QDialogButtonBox()
        self._save_btn = QPushButton("保存して適用")
        self._save_btn.setDefault(True)
        self._save_btn.setStyleSheet("""
            QPushButton {
                background: #1a3060; color: #fff;
                border: 1px solid #4fc3f7; border-radius: 6px;
                padding: 6px 24px; font-size: 13px;
            }
            QPushButton:hover { background: #253858; }
        """)
        self._save_btn.clicked.connect(self._on_save)

        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.reject)

        btn_box.addButton(self._save_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        btn_box.addButton(close_btn, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(btn_box)

    @staticmethod
    def _make_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-size: 12px; color: #ccc;")
        return lbl

    def _load_from_settings(self) -> None:
        self._enabled_cb.setChecked(self._settings.discord_bot_enabled)
        self._token_edit.setText(self._settings.discord_token)
        ids = self._settings.discord_channel_ids
        self._channels_edit.setText(", ".join(str(x) for x in ids))

    def _on_show_token_toggled(self, checked: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self._token_edit.setEchoMode(mode)
        self._show_token_btn.setText("隠す" if checked else "表示")

    def _on_save(self) -> None:
        enabled = self._enabled_cb.isChecked()
        token = self._token_edit.text().strip()
        raw_ids = self._channels_edit.text()
        channel_ids: list[int] = []
        for part in raw_ids.split(","):
            part = part.strip()
            if part.isdigit():
                channel_ids.append(int(part))
        self.save_requested.emit(enabled, token, channel_ids)
        self.accept()

    def update_bot_status(self, status_text: str) -> None:
        """Bot の状態ラベルをリアルタイム更新する。DiscordBotSignals.status_changed に接続する。"""
        self._status_lbl.setText(status_text)
        if "接続完了" in status_text:
            self._status_lbl.setStyleSheet("color: #66bb6a; font-size: 12px;")
        elif "エラー" in status_text or "無効" in status_text:
            self._status_lbl.setStyleSheet("color: #f88; font-size: 12px;")
        else:
            self._status_lbl.setStyleSheet("color: #aaa; font-size: 12px;")

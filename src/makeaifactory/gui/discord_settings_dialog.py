"""Discord Bot 設定ダイアログ。

トークン・チャンネルID・有効/無効をアプリ UI で設定できる。
「接続テスト」ボタンで Discord REST API を使ってトークンの有効性を確認できる。
「保存して適用」後はダイアログを閉じずに接続状態をそのまま表示する。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.settings_store import SettingsStore
from ..i18n import tr

_DISCORD_API = "https://discord.com/api/v10/users/@me"


class DiscordSettingsDialog(QDialog):
    # テストスレッドから main thread への結果通知 (status_code, status_text)
    _test_result = Signal(str, str)

    def __init__(self, settings: SettingsStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Discord Bot 設定"))
        self.setMinimumWidth(500)
        self.setModal(True)
        self._settings = settings
        self._save_callback = None  # set via set_save_callback()
        self._build_ui()
        self._test_result.connect(self._on_test_result)
        self._load_from_settings()

    def set_save_callback(self, cb) -> None:
        """保存ボタン押下時に呼び出すコールバックを登録する。
        cb(enabled: bool, token: str, channel_ids: list[int]) の形式で呼ばれる。
        """
        self._save_callback = cb

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        # ── 有効/無効チェックボックス ─────────────────────────────────
        self._enabled_cb = QCheckBox(tr("Discord Bot を有効にする"))
        self._enabled_cb.setStyleSheet("font-size: 14px;")
        layout.addWidget(self._enabled_cb)

        layout.addSpacing(4)

        # ── トークン入力 ─────────────────────────────────────────────
        layout.addWidget(self._make_label(tr("Bot トークン:")))
        token_row = QHBoxLayout()
        self._token_edit = QLineEdit()
        self._token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._token_edit.setPlaceholderText(tr("Discord Bot のトークンを貼り付けてください"))
        self._token_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        token_row.addWidget(self._token_edit)

        self._show_token_btn = QPushButton(tr("表示"))
        self._show_token_btn.setCheckable(True)
        self._show_token_btn.setFixedWidth(56)
        self._show_token_btn.toggled.connect(self._on_show_token_toggled)
        token_row.addWidget(self._show_token_btn)
        layout.addLayout(token_row)

        hint_token = QLabel(tr("ℹ  Discord Developer Portal → アプリ → Bot → Token でコピー"))
        hint_token.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint_token)

        layout.addSpacing(4)

        # ── チャンネルID 入力 ─────────────────────────────────────────
        layout.addWidget(self._make_label(tr("監視チャンネルID（カンマ区切り、空欄 = 全チャンネル）:")))
        self._channels_edit = QLineEdit()
        self._channels_edit.setPlaceholderText(tr("例: 1234567890, 9876543210"))
        layout.addWidget(self._channels_edit)

        hint_ch = QLabel(tr("ℹ  Discord 設定→詳細設定→開発者モードを有効化し、チャンネルを右クリック→IDをコピー"))
        hint_ch.setStyleSheet("color: #666; font-size: 11px;")
        hint_ch.setWordWrap(True)
        layout.addWidget(hint_ch)

        layout.addSpacing(8)

        # ── 割り込み生成 ──────────────────────────────────────────────
        self._interrupt_cb = QCheckBox(tr("フォルダ生成中に Discord 割り込みを許可する（友人の依頼を優先）"))
        self._interrupt_cb.setStyleSheet("font-size: 13px;")
        layout.addWidget(self._interrupt_cb)

        hint_intr = QLabel(tr("ℹ  ON にすると、フォルダ生成中でも Discord からの画像を現在の動画完了後すぐに処理します"))
        hint_intr.setStyleSheet("color: #666; font-size: 11px;")
        hint_intr.setWordWrap(True)
        layout.addWidget(hint_intr)

        layout.addSpacing(8)

        # ── Bot 状態表示 ──────────────────────────────────────────────
        status_row = QHBoxLayout()
        status_row.addWidget(self._make_label(tr("Bot 状態:")))
        self._status_lbl = QLabel(tr("未確認"))
        self._status_lbl.setStyleSheet("color: #aaa; font-size: 12px;")
        self._status_lbl.setWordWrap(True)
        status_row.addWidget(self._status_lbl, stretch=1)
        layout.addLayout(status_row)

        layout.addSpacing(8)

        # ── ボタン行 ─────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._test_btn = QPushButton(tr("接続テスト"))
        self._test_btn.setStyleSheet("""
            QPushButton {
                background: #0a2a1a; color: #66bb6a;
                border: 1px solid #2e7d32; border-radius: 6px;
                padding: 6px 16px; font-size: 13px;
            }
            QPushButton:hover { background: #0d3a24; border-color: #66bb6a; }
            QPushButton:disabled { color: #444; border-color: #333; }
        """)
        self._test_btn.clicked.connect(self._on_test_clicked)

        self._save_btn = QPushButton(tr("保存して適用"))
        self._save_btn.setDefault(True)
        self._save_btn.setStyleSheet("""
            QPushButton {
                background: #1a3060; color: #fff;
                border: 1px solid #4fc3f7; border-radius: 6px;
                padding: 6px 20px; font-size: 13px;
            }
            QPushButton:hover { background: #253858; }
        """)
        self._save_btn.clicked.connect(self._on_save)

        close_btn = QPushButton(tr("閉じる"))
        close_btn.setStyleSheet("""
            QPushButton {
                background: #1a1a2e; color: #ccc;
                border: 1px solid #444; border-radius: 6px;
                padding: 6px 16px; font-size: 13px;
            }
            QPushButton:hover { background: #253858; }
        """)
        close_btn.clicked.connect(self.reject)

        btn_row.addWidget(self._test_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

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
        self._interrupt_cb.setChecked(self._settings.discord_bot_interrupt)

    def _on_show_token_toggled(self, checked: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self._token_edit.setEchoMode(mode)
        self._show_token_btn.setText(tr("隠す") if checked else tr("表示"))

    def _on_save(self) -> None:
        enabled = self._enabled_cb.isChecked()
        token = self._token_edit.text().strip()
        channel_ids = self._parse_channel_ids()
        interrupt = self._interrupt_cb.isChecked()
        self.update_bot_status("saving", tr("保存中..."))
        if self._save_callback:
            self._save_callback(enabled, token, channel_ids, interrupt)

    def _on_test_clicked(self) -> None:
        token = self._token_edit.text().strip()
        if not token:
            self.update_bot_status("error", tr("エラー: トークンを入力してください"))
            return
        self.update_bot_status("testing", tr("テスト中..."))
        self._test_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        t = threading.Thread(target=self._run_test, args=(token,), daemon=True)
        t.start()

    def _run_test(self, token: str) -> None:
        try:
            req = urllib.request.Request(
                _DISCORD_API,
                headers={
                    "Authorization": f"Bot {token}",
                    # Discord API は DiscordBot 形式の User-Agent を要求する
                    "User-Agent": "DiscordBot (https://github.com/dikmri/makeAiFactory, 1)",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                username = data.get("username", "Unknown")
                self._test_result.emit("connected", tr("接続OK: {username}").format(username=username))
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode())
                discord_msg = body.get("message", "")
            except Exception:
                discord_msg = ""
            detail = f" ({discord_msg})" if discord_msg else ""
            if e.code == 401:
                self._test_result.emit("error", tr("エラー: トークンが無効です（401{detail}）").format(detail=detail))
            elif e.code == 403:
                self._test_result.emit(
                    "error",
                    tr("エラー: アクセス拒否（403{detail}）Bot設定を確認してください").format(detail=detail),
                )
            else:
                self._test_result.emit("error", tr("エラー: HTTP {code}{detail}").format(code=e.code, detail=detail))
        except urllib.error.URLError as e:
            self._test_result.emit("error", tr("エラー: ネットワークエラー ({reason})").format(reason=e.reason))
        except Exception as e:
            self._test_result.emit("error", tr("エラー: {e}").format(e=e))

    def _on_test_result(self, code: str, text: str) -> None:
        self.update_bot_status(code, text)
        self._test_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        # 接続テスト成功時は「有効にする」を自動チェック（忘れ防止）
        if code == "connected":
            self._enabled_cb.setChecked(True)

    def _parse_channel_ids(self) -> list:
        ids: list[int] = []
        for part in self._channels_edit.text().split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
        return ids

    def update_bot_status(self, status_code: str, status_text: str) -> None:
        """Bot の状態ラベルをリアルタイム更新する。DiscordBotSignals.status_changed に接続する。"""
        if status_code == "connected":
            color = "#66bb6a"
        elif status_code == "error":
            color = "#f88"
        elif status_code in ("testing", "connecting", "reconnecting", "saving"):
            color = "#ffa726"
        else:
            color = "#aaa"
        self._status_lbl.setStyleSheet(f"color: {color}; font-size: 12px;")
        self._status_lbl.setText(status_text)

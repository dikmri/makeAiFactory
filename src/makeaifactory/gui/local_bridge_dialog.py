"""ブラウザ連携 (Tampermonkey) 設定ダイアログ。

ローカルブリッジサーバーの有効化/無効化、ユーザースクリプトのインストール、
ローカルAPIトークンの表示を行う。完成動画はアプリ側の自動保存で受け取る。
"""
from __future__ import annotations

import webbrowser

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr


class LocalBridgeDialog(QDialog):
    """ブラウザ連携 (Tampermonkey) の設定ダイアログ。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("ブラウザ連携 (Tampermonkey)"))
        self.setMinimumWidth(500)
        self._port = 0
        self._toggle_cb = None
        self._build_ui()

    def set_toggle_callback(self, cb) -> None:
        self._toggle_cb = cb

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)

        desc = QLabel(tr(
            "ブラウザで見ている画像を、画像にマウスを乗せると出る転送ボタンから\n"
            "このアプリへ送ってそのまま動画化します。\n"
            "ご利用には Tampermonkey 拡張機能のインストールが必要です。"
        ))
        desc.setWordWrap(True)
        lay.addWidget(desc)

        self._enable_chk = QCheckBox(tr("ブラウザ連携を有効にする"))
        self._enable_chk.toggled.connect(self._on_toggled)
        lay.addWidget(self._enable_chk)

        self._install_btn = QPushButton(tr("ユーザースクリプトをインストール (ブラウザで開く)"))
        self._install_btn.clicked.connect(self._open_userscript)
        self._install_btn.setEnabled(False)
        lay.addWidget(self._install_btn)

        token_row = QHBoxLayout()
        token_row.addWidget(QLabel(tr("トークン:")))
        self._token_edit = QLineEdit()
        self._token_edit.setReadOnly(True)
        token_row.addWidget(self._token_edit)
        lay.addLayout(token_row)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        lay.addWidget(self._status_lbl)

        note = QLabel(tr(
            "※ 完成した動画は「設定 > 自動保存先」のフォルダに保存されます。\n"
            "　 ブラウザ連携を使う前に、自動保存を ON にしておいてください。"
        ))
        note.setWordWrap(True)
        lay.addWidget(note)

        close_btn = QPushButton(tr("閉じる"))
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)

    def _on_toggled(self, enabled: bool) -> None:
        if self._toggle_cb is not None:
            self._toggle_cb(enabled)

    def _open_userscript(self) -> None:
        if self._port:
            webbrowser.open(f"http://127.0.0.1:{self._port}/userscript.user.js")

    def set_active(self, active: bool, port: int, token: str) -> None:
        """サーバーの起動状態を画面へ反映する。"""
        self._port = port
        self._enable_chk.blockSignals(True)
        self._enable_chk.setChecked(active)
        self._enable_chk.blockSignals(False)
        self._install_btn.setEnabled(active)
        self._token_edit.setText(token if active else "")
        if active:
            self._status_lbl.setText(
                tr("待受中: http://127.0.0.1:{port}\n"
                   "上のボタンからスクリプトをインストールしてください。").format(port=port)
            )
        else:
            self._status_lbl.setText(tr("無効です。"))

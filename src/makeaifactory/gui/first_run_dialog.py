from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr

_TERMS_TEXT = """makeAiFactory 利用規約

本アプリを使用する前に、以下に同意してください。

【生成コンテンツに関する規約】
1. 実在する人物の同意なき性的コンテンツの生成を禁止します
2. 未成年または未成年に見える人物の性的コンテンツの生成を禁止します
3. 違法なコンテンツおよび権利侵害素材の利用を禁止します
4. 生成されたコンテンツの利用・公開に関する責任はすべてユーザーにあります

【プライバシーに関する説明】
- 入力画像はローカルPCのみで処理されます
- 本アプリが入力画像・生成動画を外部サーバへ送信することはありません
- 初回セットアップ時にのみ、必要なソフトウェアのダウンロードが行われます

【免責事項】
本アプリは「現状のまま」提供されます。
開発者は生成結果およびその利用により生じた損害について責任を負いません。
"""


class FirstRunDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("makeAiFactory - はじめに"))
        self.setMinimumWidth(520)
        self.setMinimumHeight(480)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(tr("makeAiFactory へようこそ"))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #4fc3f7;")
        layout.addWidget(title)

        terms_box = QTextEdit()
        # 法的文書のため機械翻訳はせず原文(日本語)のまま表示する
        terms_box.setPlainText(_TERMS_TEXT)
        terms_box.setReadOnly(True)
        terms_box.setStyleSheet("background: #111; color: #ccc; font-size: 12px;")
        layout.addWidget(terms_box)

        self._agree_check = QCheckBox(tr("上記の規約に同意します（必須）"))
        self._agree_check.setStyleSheet("color: #eee; font-size: 13px;")
        self._agree_check.stateChanged.connect(self._on_check_changed)
        layout.addWidget(self._agree_check)

        self._btn_box = QDialogButtonBox()
        self._ok_btn = self._btn_box.addButton(tr("同意してはじめる"), QDialogButtonBox.ButtonRole.AcceptRole)
        self._ok_btn.setEnabled(False)
        self._ok_btn.setStyleSheet("padding: 10px 24px; background: #1565c0; color: white; border: none; border-radius: 4px;")
        cancel_btn = self._btn_box.addButton(tr("キャンセル"), QDialogButtonBox.ButtonRole.RejectRole)
        cancel_btn.setStyleSheet("padding: 10px 24px;")
        self._btn_box.accepted.connect(self.accept)
        self._btn_box.rejected.connect(self.reject)
        layout.addWidget(self._btn_box)

    def _on_check_changed(self, state: int) -> None:
        self._ok_btn.setEnabled(bool(state))

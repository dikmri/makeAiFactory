from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr


class ErrorReportDialog(QDialog):
    """送信内容のプレビュー＋同意＋任意コメント入力。

    実際に送信するJSONペイロードをそのままプレビュー表示することで、
    何が送られるかをユーザーが必ず確認できるようにする。
    """

    send_requested = Signal(str)  # user_comment

    def __init__(self, preview_text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("エラーを報告する"))
        self.setMinimumSize(520, 480)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        notice = QLabel(tr(
            "開発者への報告として、以下の内容が送信されます。\n"
            "設定ファイルや生成画像・入力画像は送信されません。"
        ))
        notice.setWordWrap(True)
        notice.setStyleSheet("color: #eee; font-size: 13px;")
        layout.addWidget(notice)

        preview = QTextEdit()
        preview.setPlainText(preview_text)
        preview.setReadOnly(True)
        preview.setStyleSheet("background: #1a1a1a; color: #aaa; font-family: monospace; font-size: 11px;")
        layout.addWidget(preview, stretch=3)

        comment_label = QLabel(tr("状況の説明 (任意・個人情報は記載しないでください):"))
        comment_label.setStyleSheet("color: #eee; font-size: 12px;")
        layout.addWidget(comment_label)

        self._comment_edit = QPlainTextEdit()
        self._comment_edit.setMaximumHeight(80)
        layout.addWidget(self._comment_edit, stretch=1)

        btn_box = QDialogButtonBox()
        cancel_btn = btn_box.addButton(tr("キャンセル"), QDialogButtonBox.ButtonRole.RejectRole)
        send_btn = btn_box.addButton(tr("送信する"), QDialogButtonBox.ButtonRole.AcceptRole)
        send_btn.setStyleSheet("padding: 8px 20px; background: #1565c0; color: white; border: none; border-radius: 4px;")
        btn_box.accepted.connect(self._on_send)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_send(self) -> None:
        self.send_requested.emit(self._comment_edit.toPlainText())
        self.accept()

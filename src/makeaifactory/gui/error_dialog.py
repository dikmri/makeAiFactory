from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ErrorDialog(QDialog):
    def __init__(
        self,
        title: str,
        message: str,
        detail: str = "",
        parent: QWidget | None = None,
        show_repair: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle("エラー")
        self.setMinimumWidth(480)
        self._repair_requested = False

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title_label = QLabel(f"<b>{title}</b>")
        title_label.setStyleSheet("color: #f44336; font-size: 16px;")
        layout.addWidget(title_label)

        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("color: #eee; font-size: 13px;")
        layout.addWidget(msg_label)

        if detail:
            detail_box = QTextEdit()
            detail_box.setPlainText(detail)
            detail_box.setReadOnly(True)
            detail_box.setMaximumHeight(150)
            detail_box.setStyleSheet("background: #1a1a1a; color: #aaa; font-family: monospace;")
            layout.addWidget(detail_box)

        btn_box = QDialogButtonBox()
        ok_btn = btn_box.addButton("閉じる", QDialogButtonBox.ButtonRole.AcceptRole)
        ok_btn.setStyleSheet("padding: 8px 20px;")

        if show_repair:
            repair_btn = QPushButton("自動修復する")
            repair_btn.setStyleSheet("padding: 8px 20px; background: #1565c0; color: white; border: none; border-radius: 4px;")
            repair_btn.clicked.connect(self._on_repair)
            layout.addWidget(repair_btn)

        btn_box.accepted.connect(self.accept)
        layout.addWidget(btn_box)

    def _on_repair(self) -> None:
        self._repair_requested = True
        self.accept()

    @property
    def repair_requested(self) -> bool:
        return self._repair_requested

    @staticmethod
    def show_error(
        title: str,
        message: str,
        detail: str = "",
        parent: QWidget | None = None,
        show_repair: bool = False,
    ) -> bool:
        dialog = ErrorDialog(title, message, detail, parent, show_repair)
        dialog.exec()
        return dialog.repair_requested

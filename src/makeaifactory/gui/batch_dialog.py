from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class BatchDialog(QDialog):
    """入力フォルダと出力フォルダを指定するバッチ処理ダイアログ。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("フォルダを一括生成")
        self.setMinimumWidth(480)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        desc = QLabel(
            "フォルダ内の画像を順番に動画生成します。\n"
            "処理済み画像は入力フォルダ内の「end」フォルダに移動されます。"
        )
        desc.setStyleSheet("color: #aaa; font-size: 13px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        form = QFormLayout()
        form.setSpacing(10)

        self._input_edit = QLineEdit()
        self._input_edit.setPlaceholderText("画像が入っているフォルダ")
        self._input_edit.textChanged.connect(self._validate)
        input_row = self._make_row(self._input_edit, self._browse_input)
        form.addRow("入力フォルダ:", input_row)

        self._output_edit = QLineEdit()
        self._output_edit.setPlaceholderText("動画の保存先フォルダ")
        self._output_edit.textChanged.connect(self._validate)
        output_row = self._make_row(self._output_edit, self._browse_output)
        form.addRow("出力フォルダ:", output_row)

        layout.addLayout(form)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #f88; font-size: 12px;")
        layout.addWidget(self._status_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("開始")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setEnabled(False)
        layout.addWidget(buttons)

    def _make_row(self, edit: QLineEdit, browse_fn) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(edit)
        btn = QPushButton("参照...")
        btn.setFixedWidth(64)
        btn.clicked.connect(browse_fn)
        h.addWidget(btn)
        return row

    def _browse_input(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "入力フォルダを選択")
        if d:
            self._input_edit.setText(d)

    def _browse_output(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "出力フォルダを選択")
        if d:
            self._output_edit.setText(d)

    def _validate(self) -> None:
        in_text = self._input_edit.text().strip()
        out_text = self._output_edit.text().strip()
        if not in_text or not out_text:
            self._status_label.setText("")
            self._ok_btn.setEnabled(False)
            return
        in_path = Path(in_text)
        if not in_path.is_dir():
            self._status_label.setText("入力フォルダが見つかりません")
            self._ok_btn.setEnabled(False)
            return
        self._status_label.setText("")
        self._ok_btn.setEnabled(True)

    def input_folder(self) -> Path:
        return Path(self._input_edit.text().strip())

    def output_folder(self) -> Path:
        return Path(self._output_edit.text().strip())

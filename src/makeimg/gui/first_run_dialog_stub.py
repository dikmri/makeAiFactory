from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def ask_install_location(default: Path, parent: QWidget | None = None) -> Path | None:
    dlg = QDialog(parent)
    dlg.setWindowTitle("インストール場所の選択")
    dlg.setMinimumWidth(500)
    layout = QVBoxLayout(dlg)

    label = QLabel(
        "ComfyUIやモデルのインストール先を選択してください。\n"
        "十分な空き容量があるドライブを推奨します（55GB以上）。"
    )
    label.setWordWrap(True)
    layout.addWidget(label)

    path_layout = QVBoxLayout()
    path_edit = QLineEdit(str(default))
    browse_btn = QPushButton("参照...")

    def _browse() -> None:
        folder = QFileDialog.getExistingDirectory(dlg, "インストール先を選択", path_edit.text())
        if folder:
            path_edit.setText(folder)

    browse_btn.clicked.connect(_browse)
    path_layout.addWidget(path_edit)
    path_layout.addWidget(browse_btn)
    layout.addLayout(path_layout)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)

    if dlg.exec() == QDialog.DialogCode.Accepted:
        return Path(path_edit.text())
    return None


def show_terms_dialog(parent: QWidget | None = None) -> bool:
    result = QMessageBox.information(
        parent,
        "利用規約",
        "本ソフトウェアの利用規約に同意してください。\n\n"
        "- 本アプリはローカルPCで処理を行います\n"
        "- 入力データ・生成物は外部に送信されません\n"
        "- 生成コンテンツの利用に関する責任はユーザーに帰属します\n"
        "- 実在する人物の同意なき性的コンテンツや未成年を対象としたコンテンツの生成を禁じます\n"
        "- 本アプリは「現状のまま」提供され、開発者は生成結果による損害に責任を負いません",
        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
    )
    return result == QMessageBox.StandardButton.Ok

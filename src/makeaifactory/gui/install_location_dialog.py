from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_NEEDED_GB = 45.0


class InstallLocationDialog(QDialog):
    """初回起動時にruntime（モデル・環境）のインストール先を選ぶダイアログ。"""

    def __init__(self, default_path: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("makeAiFactory - インストール場所の選択")
        self.setMinimumWidth(600)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(28, 24, 28, 24)

        title = QLabel("インストール場所を選択してください")
        title.setStyleSheet(
            "font-size: 17px; font-weight: bold; color: #4fc3f7; margin-bottom: 4px;"
        )
        layout.addWidget(title)

        desc = QLabel(
            "AIモデルや処理環境を保存するフォルダを選択してください。\n"
            f"初回セットアップで約 {_NEEDED_GB:.0f} GB のデータがダウンロードされます。\n"
            "空き容量が十分なドライブを選んでください。\n"
            "※ パスに日本語などの全角文字が含まれるとインストールが失敗します。"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #bbb; font-size: 13px; line-height: 1.5;")
        layout.addWidget(desc)

        path_layout = QHBoxLayout()
        path_layout.setSpacing(8)
        self._path_edit = QLineEdit(str(default_path))
        self._path_edit.setStyleSheet(
            "background: #1a1a2e; color: #eee; border: 1px solid #555; "
            "border-radius: 4px; padding: 7px 10px; font-size: 13px;"
        )
        path_layout.addWidget(self._path_edit)

        browse_btn = QPushButton("参照...")
        browse_btn.setFixedWidth(80)
        browse_btn.setStyleSheet(
            "QPushButton { background: #2a2a40; color: #ccc; border: 1px solid #555; "
            "border-radius: 4px; padding: 7px 12px; font-size: 13px; } "
            "QPushButton:hover { background: #3a3a55; }"
        )
        browse_btn.clicked.connect(self._browse)
        path_layout.addWidget(browse_btn)
        layout.addLayout(path_layout)

        self._space_label = QLabel()
        self._space_label.setStyleSheet("font-size: 12px;")
        layout.addWidget(self._space_label)

        self._warn_label = QLabel()
        self._warn_label.setWordWrap(True)
        self._warn_label.setStyleSheet("color: #ff6b6b; font-size: 12px;")
        self._warn_label.setVisible(False)
        layout.addWidget(self._warn_label)

        layout.addSpacing(6)

        self._ok_btn = QPushButton("このフォルダにインストールする")
        self._ok_btn.setStyleSheet(
            "QPushButton { background: #1565c0; color: white; border: none; "
            "border-radius: 6px; padding: 12px 32px; font-size: 14px; font-weight: bold; } "
            "QPushButton:hover { background: #1976d2; } "
            "QPushButton:pressed { background: #0d47a1; } "
            "QPushButton:disabled { background: #2a2a40; color: #666; }"
        )
        self._ok_btn.clicked.connect(self.accept)
        layout.addWidget(self._ok_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._path_edit.textChanged.connect(self._validate)
        self._validate()

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "インストール先のフォルダを選択（runtime フォルダが自動作成されます）",
        )
        if chosen:
            self._path_edit.setText(str(Path(chosen) / "runtime"))

    def _validate(self) -> None:
        text = self._path_edit.text().strip()

        if not text:
            self._warn_label.setVisible(False)
            self._space_label.setText("")
            self._ok_btn.setEnabled(False)
            return

        # ASCII チェック
        try:
            text.encode("ascii")
            is_ascii = True
        except UnicodeEncodeError:
            is_ascii = False

        if not is_ascii:
            self._warn_label.setText(
                "パスに日本語などの全角文字が含まれています。"
                "別の場所（例: D:\\makeAiFactory\\runtime）を選んでください。"
            )
            self._warn_label.setVisible(True)
            self._space_label.setText("")
            self._ok_btn.setEnabled(False)
            return

        self._warn_label.setVisible(False)

        # 空き容量表示
        try:
            anchor = Path(text).anchor or "."
            usage = shutil.disk_usage(anchor)
            free_gb = usage.free / 1024 ** 3
            if free_gb < _NEEDED_GB:
                self._space_label.setText(
                    f"空き容量: {free_gb:.1f} GB  ※推奨 {_NEEDED_GB:.0f} GB 以上"
                )
                self._space_label.setStyleSheet("color: #ffb74d; font-size: 12px;")
            else:
                self._space_label.setText(f"空き容量: {free_gb:.1f} GB")
                self._space_label.setStyleSheet("color: #81c784; font-size: 12px;")
        except Exception:
            self._space_label.setText("")

        self._ok_btn.setEnabled(True)

    def chosen_path(self) -> Path:
        return Path(self._path_edit.text().strip())

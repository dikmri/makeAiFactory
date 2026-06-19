from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class AboutDialog(QDialog):
    """ヘルプ > バージョン情報。手動でのアップデート確認・適用に対応する。"""

    check_update_requested = Signal()
    update_now_requested = Signal()

    def __init__(self, app_name: str, app_version: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._app_version = app_version
        self._mode = "idle"  # "idle" | "checking" | "update_available" | "downloading"

        self.setWindowTitle(f"{app_name}について")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(f"{app_name} v{app_version}")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        desc = QLabel(
            "画像をドラッグ＆ドロップするだけでAI動画を生成するアプリです。\n"
            "生成はすべてローカルPCで行われます。\n"
            "入力画像・生成動画が外部送信されることはありません。"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(desc)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #4fc3f7; font-size: 12px;")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        btn_row = QHBoxLayout()
        self._action_btn = QPushButton("アップデートを確認")
        self._action_btn.clicked.connect(self._on_action_clicked)
        btn_row.addWidget(self._action_btn)
        btn_row.addStretch()
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _on_action_clicked(self) -> None:
        if self._mode == "update_available":
            self.update_now_requested.emit()
        else:
            self.check_update_requested.emit()

    def show_checking(self) -> None:
        self._mode = "checking"
        self._action_btn.setEnabled(False)
        self._status_label.setText("アップデートを確認しています...")

    def show_up_to_date(self) -> None:
        self._mode = "idle"
        self._action_btn.setEnabled(True)
        self._action_btn.setText("アップデートを確認")
        self._status_label.setText(f"最新バージョンです (v{self._app_version})")

    def show_update_available(self, version: str) -> None:
        self._mode = "update_available"
        self._action_btn.setEnabled(True)
        self._action_btn.setText("ダウンロードして更新")
        self._status_label.setText(f"新しいバージョン v{version} が利用可能です")

    def show_check_failed(self, message: str) -> None:
        self._mode = "idle"
        self._action_btn.setEnabled(True)
        self._action_btn.setText("アップデートを確認")
        self._status_label.setText(f"確認に失敗しました: {message}")

    def show_downloading(self, pct: float) -> None:
        self._mode = "downloading"
        self._action_btn.setEnabled(False)
        self._status_label.setText(f"ダウンロード中... {pct:.0f}%")

    def show_apply_skipped_dev_mode(self) -> None:
        self._mode = "idle"
        self._action_btn.setEnabled(True)
        self._action_btn.setText("アップデートを確認")
        self._status_label.setText("開発モードのため適用をスキップしました")

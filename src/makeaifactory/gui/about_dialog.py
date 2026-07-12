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

from ..i18n import tr


class AboutDialog(QDialog):
    """ヘルプ > バージョン情報。手動でのアップデート確認・適用に対応する。"""

    check_update_requested = Signal()
    update_now_requested = Signal()

    def __init__(self, app_name: str, app_version: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._app_version = app_version
        self._mode = "idle"  # "idle" | "checking" | "update_available" | "downloading"

        self.setWindowTitle(tr("{app_name}について").format(app_name=app_name))
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(f"{app_name} v{app_version}")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        desc = QLabel(tr(
            "画像をドラッグ＆ドロップするだけでAI動画を生成するアプリです。\n"
            "通常のローカル生成では、入力画像・生成動画が外部へ送信されることはありません。\n"
            "ただし、Discord Bot・インターネット投入口/ブラウザ連携・アップデート確認・\n"
            "エラー報告など、利用者が任意で有効化する機能を使う場合は、その機能に応じて\n"
            "外部と通信します。"
        ))
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(desc)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #4fc3f7; font-size: 12px;")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        btn_row = QHBoxLayout()
        self._action_btn = QPushButton(tr("アップデートを確認"))
        self._action_btn.clicked.connect(self._on_action_clicked)
        btn_row.addWidget(self._action_btn)
        btn_row.addStretch()
        close_btn = QPushButton(tr("閉じる"))
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
        self._status_label.setText(tr("アップデートを確認しています..."))

    def show_up_to_date(self) -> None:
        self._mode = "idle"
        self._action_btn.setEnabled(True)
        self._action_btn.setText(tr("アップデートを確認"))
        self._status_label.setText(tr("最新バージョンです (v{version})").format(version=self._app_version))

    def show_update_available(self, version: str) -> None:
        self._mode = "update_available"
        self._action_btn.setEnabled(True)
        self._action_btn.setText(tr("ダウンロードして更新"))
        self._status_label.setText(tr("新しいバージョン v{version} が利用可能です").format(version=version))

    def show_check_failed(self, message: str) -> None:
        self._mode = "idle"
        self._action_btn.setEnabled(True)
        self._action_btn.setText(tr("アップデートを確認"))
        self._status_label.setText(tr("確認に失敗しました: {message}").format(message=message))

    def show_downloading(self, pct: float) -> None:
        self._mode = "downloading"
        self._action_btn.setEnabled(False)
        self._status_label.setText(tr("ダウンロード中... {pct:.0f}%").format(pct=pct))

    def show_apply_skipped_dev_mode(self) -> None:
        self._mode = "idle"
        self._action_btn.setEnabled(True)
        self._action_btn.setText(tr("アップデートを確認"))
        self._status_label.setText(tr("開発モードのため適用をスキップしました"))

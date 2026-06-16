from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ..constants import MODEL_PRESETS


def _fmt_gb(byte_count: int) -> str:
    return f"{byte_count / (1024 ** 3):.1f} GB"


class ModelPresetDialog(QDialog):
    """インストール済みではないプリセットを選択してDLするダイアログ。"""

    def __init__(
        self,
        runtime_root: Path,
        manifest,
        installed_presets: list[str],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("プリセットを追加インストール")
        self.setMinimumWidth(480)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        from ..runtime.model_installer import estimate_download_bytes

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        title = QLabel("追加するプリセットを選択してください")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #4fc3f7;")
        layout.addWidget(title)

        self._checks: dict[str, QCheckBox] = {}
        uninstalled = [k for k in MODEL_PRESETS if k not in installed_presets]

        if not uninstalled:
            layout.addWidget(QLabel("すべてのプリセットはインストール済みです。"))
        else:
            for key in uninstalled:
                info = MODEL_PRESETS[key]
                dl_bytes = estimate_download_bytes(runtime_root, manifest, [key])
                dl_str = _fmt_gb(dl_bytes) if dl_bytes > 0 else "DL不要"

                row = QHBoxLayout()
                cb = QCheckBox(info["label"])
                cb.setStyleSheet("font-size: 13px; color: #eee;")
                row.addWidget(cb)

                desc_lbl = QLabel(f"{info['desc']}  |  追加DL: {dl_str}")
                desc_lbl.setStyleSheet("color: #999; font-size: 11px;")
                desc_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                row.addWidget(desc_lbl, stretch=1)

                layout.addLayout(row)
                self._checks[key] = cb

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._progress.setStyleSheet("QProgressBar { background:#111; border:1px solid #333; border-radius:4px; height:14px; } QProgressBar::chunk { background:#4fc3f7; border-radius:3px; }")
        layout.addWidget(self._progress)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        self._status_lbl.setVisible(False)
        layout.addWidget(self._status_lbl)

        self._btn_box = QDialogButtonBox()
        self._install_btn = self._btn_box.addButton(
            "インストール開始", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._install_btn.setStyleSheet(
            "padding: 10px 24px; background: #1565c0; color: white; border: none; border-radius: 4px;"
        )
        self._install_btn.setEnabled(bool(uninstalled))
        cancel_btn = self._btn_box.addButton("キャンセル", QDialogButtonBox.ButtonRole.RejectRole)
        cancel_btn.setStyleSheet("padding: 10px 24px;")
        self._btn_box.accepted.connect(self.accept)
        self._btn_box.rejected.connect(self.reject)
        layout.addWidget(self._btn_box)

    @property
    def selected_presets(self) -> list[str]:
        return [k for k, cb in self._checks.items() if cb.isChecked()]

    def show_progress(self, message: str, pct: float) -> None:
        self._progress.setVisible(True)
        self._status_lbl.setVisible(True)
        self._progress.setValue(int(pct))
        self._status_lbl.setText(message)
        self._install_btn.setEnabled(False)

    def mark_done(self) -> None:
        self._progress.setValue(100)
        self._status_lbl.setText("インストール完了！")
        self._install_btn.setEnabled(False)
        cancel_btn = self._btn_box.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText("閉じる")

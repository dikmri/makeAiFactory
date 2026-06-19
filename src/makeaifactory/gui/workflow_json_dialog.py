"""開発モード — 実際の生成に使われるComfyUIワークフロー(API形式JSON)をそのまま表示・編集する。"""
from __future__ import annotations

from typing import Callable

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_BG      = "#0d0d1a"
_BG_CARD = "#181828"
_BORDER  = "#252540"
_TEXT    = "#dde"
_ACCENT  = "#4fc3f7"


class WorkflowJsonDialog(QDialog):
    """画像→動画生成の裏側で実際に動いているComfyUIワークフローJSON
    (makeAiFactory_api_source.json) をそのまま表示・編集するダイアログ。
    """

    def __init__(
        self,
        workflow_json_text: str,
        apply_fn: Callable[[str], str],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._apply_fn = apply_fn
        self._saved_text = workflow_json_text

        self.setWindowTitle("ワークフローJSON — makeAiFactory_api_source.json")
        self.setMinimumSize(900, 700)
        self.setStyleSheet(f"""
            QDialog, QWidget {{ background: {_BG}; color: {_TEXT}; }}
            QPlainTextEdit {{
                background: {_BG_CARD};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                color: {_TEXT};
                padding: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        desc = QLabel(
            "画像をドロップして動画化するとき、裏側では実際にこのComfyUIワークフロー (API形式) が"
            "そのまま使われています。値を直接編集して「保存して適用」すると、次回の生成から反映されます。"
            "(makeAiFactory_api_source.json)"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(desc)

        self._editor = QPlainTextEdit()
        self._editor.setPlainText(workflow_json_text)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._editor.setFont(mono)
        layout.addWidget(self._editor, stretch=1)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet("font-size: 12px;")
        layout.addWidget(self._status_lbl)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton("保存済みの内容に戻す")
        reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        apply_btn = QPushButton("保存して適用")
        apply_btn.setStyleSheet(f"""
            QPushButton {{
                background: #0d3050; color: {_ACCENT};
                border: 1px solid {_ACCENT}; border-radius: 6px;
                padding: 6px 18px;
            }}
            QPushButton:hover {{ background: {_ACCENT}22; }}
        """)
        apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(apply_btn)
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _on_reset(self) -> None:
        self._editor.setPlainText(self._saved_text)
        self._status_lbl.setText("")

    def _on_apply(self) -> None:
        text = self._editor.toPlainText()
        error = self._apply_fn(text)
        if error:
            self._status_lbl.setStyleSheet("color: #f88; font-size: 12px;")
            self._status_lbl.setText(error)
            return
        self._saved_text = text
        self._status_lbl.setStyleSheet("color: #66bb6a; font-size: 12px;")
        self._status_lbl.setText("保存しました。次回の生成から反映されます。")

"""開発モード — 実際の生成に使われるComfyUIワークフローの全パラメーターを
ノードごとのつまみ・入力欄で調整するダイアログ (生JSONテキストではなく)。
"""
from __future__ import annotations

import copy
import json
import logging
from typing import Any, Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .dev_widgets import CollapseSection

logger = logging.getLogger(__name__)

_BG      = "#0d0d1a"
_BG_MID  = "#12121f"
_BG_CARD = "#181828"
_BORDER  = "#252540"
_TEXT    = "#dde"
_DIM     = "#888"
_ACCENT  = "#4fc3f7"

# rgthree Power Lora Loaderのみが持つUI専用プレースホルダ (実際の値ではない)
_UI_PLACEHOLDER_KEYS = {"➕ Add Lora"}


def _is_node_ref(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], int)
    )


def _is_ui_placeholder(key: str, value: Any) -> bool:
    if key in _UI_PLACEHOLDER_KEYS:
        return True
    return isinstance(value, dict) and "type" in value


def _make_value_widget(key: str, value: Any) -> tuple[QWidget | None, Callable[[], Any] | None]:
    """値の型に応じた編集ウィジェットと、現在値を取り出すgetterを返す。"""
    if isinstance(value, bool):
        cb = QCheckBox()
        cb.setChecked(value)
        return cb, cb.isChecked

    if isinstance(value, int):
        if -2_147_483_648 <= value <= 2_147_483_647:
            sb = QSpinBox()
            sb.setRange(-2_147_483_648, 2_147_483_647)
            sb.setValue(value)
            return sb, sb.value
        # QSpinBoxは32bit整数までしか扱えない (シード値など巨大な整数用のフォールバック)
        le = QLineEdit(str(value))

        def _get_big_int(line_edit: QLineEdit = le, fallback: int = value) -> int:
            try:
                return int(line_edit.text())
            except ValueError:
                return fallback

        return le, _get_big_int

    if isinstance(value, float):
        ds = QDoubleSpinBox()
        ds.setRange(-1_000_000.0, 1_000_000.0)
        ds.setDecimals(4)
        ds.setSingleStep(0.1)
        ds.setValue(value)
        return ds, ds.value

    if isinstance(value, str):
        if len(value) > 80 or "\n" in value:
            te = QPlainTextEdit()
            te.setPlainText(value)
            te.setFixedHeight(70)
            return te, te.toPlainText
        le = QLineEdit(value)
        return le, le.text

    if isinstance(value, dict):
        return _make_dict_widget(value)

    return None, None


def _make_dict_widget(value: dict) -> tuple[QWidget, Callable[[], dict]]:
    """LoRAスロットのような {on, lora, strength} 形式の値を1行にまとめて編集する。"""
    container = QWidget()
    h = QHBoxLayout(container)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)

    getters: dict[str, Callable[[], Any]] = {}
    for sub_key, sub_value in value.items():
        widget, getter = _make_value_widget(sub_key, sub_value)
        if widget is None or getter is None:
            continue
        lbl = QLabel(sub_key)
        lbl.setStyleSheet(f"color: {_DIM}; font-size: 10px;")
        h.addWidget(lbl)
        h.addWidget(widget, stretch=1 if isinstance(sub_value, str) else 0)
        getters[sub_key] = getter

    def _get() -> dict:
        return {k: g() for k, g in getters.items()}

    return container, _get


class _NodePanel(CollapseSection):
    """1ノード分の編集可能な入力をまとめたパネル。"""

    def __init__(self, node_id: str, node_data: dict, parent: QWidget | None = None):
        class_type = node_data.get("class_type", "")
        title = node_data.get("_meta", {}).get("title", "")
        label = f"#{node_id}  {title or class_type}  ({class_type})" if title else f"#{node_id}  {class_type}"
        super().__init__(label, parent)
        self.node_id = node_id
        self.searchable_text = f"{node_id} {class_type} {title}".lower()

        self._getters: dict[str, Callable[[], Any]] = {}
        form = QFormLayout()
        form.setSpacing(6)
        for key, value in node_data.get("inputs", {}).items():
            if _is_node_ref(value) or _is_ui_placeholder(key, value):
                continue
            widget, getter = _make_value_widget(key, value)
            if widget is None or getter is None:
                continue
            form.addRow(key, widget)
            self._getters[key] = getter

        if self._getters:
            form_w = QWidget()
            form_w.setLayout(form)
            self.add_widget(form_w)

    def has_params(self) -> bool:
        return bool(self._getters)

    def values(self) -> dict[str, Any]:
        return {k: g() for k, g in self._getters.items()}


class WorkflowJsonDialog(QDialog):
    """画像→動画生成の裏側で実際に動いているComfyUIワークフロー
    (makeAiFactory_api_source.json) の値を、ノードごとのつまみ・入力欄で調整する。
    """

    def __init__(
        self,
        workflow_json_text: str,
        apply_fn: Callable[[str], str],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._apply_fn = apply_fn
        self._panels: list[_NodePanel] = []

        self.setWindowTitle("ワークフローパラメーター — makeAiFactory_api_source.json")
        self.setMinimumSize(900, 700)
        self.setStyleSheet(f"""
            QDialog, QWidget {{ background: {_BG}; color: {_TEXT}; }}
            QScrollArea {{ border: none; }}
            QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
                background: {_BG_CARD};
                border: 1px solid {_BORDER};
                border-radius: 5px;
                color: {_TEXT};
                padding: 3px 6px;
                font-size: 12px;
            }}
            QCheckBox::indicator {{
                width: 14px; height: 14px;
                border: 1px solid {_BORDER}; border-radius: 3px;
                background: {_BG_CARD};
            }}
            QCheckBox::indicator:checked {{ background: {_ACCENT}; border-color: {_ACCENT}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        desc = QLabel(
            "画像をドロップして動画化するとき、裏側では実際にこのComfyUIワークフローが"
            "そのまま使われています。各ノードを開いて値を直接調整し、「保存して適用」すると"
            "次回の生成から反映されます。(makeAiFactory_api_source.json)"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(desc)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("ノードID・class_type・タイトルで絞り込み...")
        self._search_edit.textChanged.connect(self._on_search_changed)
        layout.addWidget(self._search_edit)

        try:
            workflow = json.loads(workflow_json_text)
        except json.JSONDecodeError as e:
            workflow = {}
            self._status_lbl_init_error = str(e)
        else:
            self._status_lbl_init_error = ""

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ background: {_BG_MID}; }}")
        inner = QWidget()
        inner.setStyleSheet(f"background: {_BG_MID};")
        inner_lay = QVBoxLayout(inner)
        inner_lay.setSpacing(2)

        for node_id, node_data in sorted(workflow.items(), key=lambda kv: _sort_key(kv[0])):
            if not isinstance(node_data, dict):
                continue
            panel = _NodePanel(node_id, node_data)
            if not panel.has_params():
                continue
            self._panels.append(panel)
            inner_lay.addWidget(panel)
        inner_lay.addStretch()

        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)

        self._status_lbl = QLabel(self._status_lbl_init_error)
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet("color: #f88; font-size: 12px;" if self._status_lbl_init_error else "font-size: 12px;")
        layout.addWidget(self._status_lbl)

        btn_row = QHBoxLayout()
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

        self._workflow = workflow

    def _on_search_changed(self, text: str) -> None:
        needle = text.strip().lower()
        for panel in self._panels:
            panel.setVisible(not needle or needle in panel.searchable_text)

    def _on_apply(self) -> None:
        merged = copy.deepcopy(self._workflow)
        for panel in self._panels:
            node = merged.get(panel.node_id)
            if node is None:
                continue
            node.setdefault("inputs", {}).update(panel.values())

        text = json.dumps(merged, ensure_ascii=False, indent=2)
        error = self._apply_fn(text)
        if error:
            self._status_lbl.setStyleSheet("color: #f88; font-size: 12px;")
            self._status_lbl.setText(error)
            return
        self._workflow = merged
        self._status_lbl.setStyleSheet("color: #66bb6a; font-size: 12px;")
        self._status_lbl.setText("保存しました。次回の生成から反映されます。")


def _sort_key(node_id: str) -> tuple[int, str]:
    try:
        return (0, "%020d" % int(node_id))
    except ValueError:
        return (1, node_id)

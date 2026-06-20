"""開発モード用カスタムウィジェット (ノブ・フェーダー・セクションヘッダー)。"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QConicalGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDial,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr

_ACCENT   = "#4fc3f7"
_BG_DARK  = "#0d0d1a"
_BG_MID   = "#1a1a2e"
_TRACK    = "#252540"
_TEXT_DIM = "#888"
_TEXT_VAL = "#e0f7fa"


# ── ノブウィジェット ─────────────────────────────────────────────────────────

class _KnobDial(QDial):
    """QDial に円弧グラデーション描画を追加したサブクラス。"""

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        r = min(w, h) / 2 - 4
        cx, cy = w / 2, h / 2

        ratio = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        start_angle = 225   # 7時方向 (Qt: 反時計回り度数、描画は12時=90度)
        span = 270          # 270度分

        # トラック (背景弧)
        pen_track = QPen(QColor(_TRACK), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen_track)
        p.drawArc(
            int(cx - r), int(cy - r), int(r * 2), int(r * 2),
            int((start_angle) * 16),
            int(-span * 16),
        )

        # 値弧 (アクセントカラー)
        if ratio > 0:
            pen_val = QPen(QColor(_ACCENT), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen_val)
            p.drawArc(
                int(cx - r), int(cy - r), int(r * 2), int(r * 2),
                int(start_angle * 16),
                int(-span * ratio * 16),
            )

        # ニードル点
        angle_deg = start_angle - span * ratio
        angle_rad = math.radians(angle_deg)
        nx = cx + (r - 6) * math.cos(angle_rad)
        ny = cy - (r - 6) * math.sin(angle_rad)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_ACCENT))
        p.drawEllipse(int(nx - 3), int(ny - 3), 6, 6)
        p.end()


class KnobWidget(QWidget):
    """ノブ型スピン: ラベル + カスタム QDial + 値ラベル。"""
    valueChanged = Signal(float)

    def __init__(
        self,
        label: str,
        min_val: float,
        max_val: float,
        default: float,
        decimals: int = 0,
        step: float = 1.0,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._min = min_val
        self._max = max_val
        self._step = step
        self._decimals = decimals

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        # タイトルラベル
        title = QLabel(label)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 10px; letter-spacing: 1px;")
        lay.addWidget(title)

        # ダイアル
        self._dial = _KnobDial()
        self._dial.setMinimum(0)
        self._dial.setMaximum(int((max_val - min_val) / step))
        self._dial.setNotchesVisible(False)
        self._dial.setFixedSize(72, 72)
        self._dial.setStyleSheet(f"""
            QDial {{
                background: {_BG_DARK};
                border-radius: 36px;
                border: none;
            }}
        """)
        tick = int((default - min_val) / step)
        self._dial.setValue(tick)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._dial)
        row.addStretch()
        lay.addLayout(row)

        # 値ラベル
        self._val_lbl = QLabel(self._fmt(default))
        self._val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._val_lbl.setStyleSheet(
            f"color: {_TEXT_VAL}; font-size: 15px; font-weight: bold;"
        )
        lay.addWidget(self._val_lbl)

        self._dial.valueChanged.connect(self._on_dial)

    def _fmt(self, v: float) -> str:
        return f"{v:.{self._decimals}f}" if self._decimals else str(int(v))

    def _on_dial(self, tick: int) -> None:
        v = self._min + tick * self._step
        self._val_lbl.setText(self._fmt(v))
        self.valueChanged.emit(v)

    def value(self) -> float:
        return self._min + self._dial.value() * self._step

    def set_value(self, v: float) -> None:
        self._dial.setValue(int((v - self._min) / self._step))


# ── フェーダーウィジェット ───────────────────────────────────────────────────

_FADER_CSS = f"""
QSlider::groove:horizontal {{
    background: {_TRACK};
    border-radius: 4px;
    height: 6px;
}}
QSlider::handle:horizontal {{
    background: {_ACCENT};
    width: 16px;
    height: 16px;
    border-radius: 8px;
    margin: -5px 0;
    border: 2px solid {_BG_DARK};
}}
QSlider::sub-page:horizontal {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #0d3050, stop:1 #1a6090);
    border-radius: 4px;
}}
"""


class FaderWidget(QWidget):
    """水平スライダー型フェーダー: ラベル + スライダー + 値ラベル。"""
    valueChanged = Signal(float)

    def __init__(
        self,
        label: str,
        min_val: float,
        max_val: float,
        default: float,
        decimals: int = 0,
        step: float = 1.0,
        unit: str = "",
        label_width: int = 140,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._min = min_val
        self._max = max_val
        self._step = step
        self._decimals = decimals
        self._unit = unit

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(10)

        lbl = QLabel(label)
        lbl.setFixedWidth(label_width)
        lbl.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 12px;")
        lay.addWidget(lbl)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(int((max_val - min_val) / step))
        self._slider.setStyleSheet(_FADER_CSS)
        self._slider.setValue(int((default - min_val) / step))
        lay.addWidget(self._slider, stretch=1)

        self._val_lbl = QLabel(self._fmt(default))
        self._val_lbl.setFixedWidth(64)
        self._val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._val_lbl.setStyleSheet(
            f"color: {_TEXT_VAL}; font-size: 12px; font-weight: bold;"
        )
        lay.addWidget(self._val_lbl)

        self._slider.valueChanged.connect(self._on_slide)

    def _fmt(self, v: float) -> str:
        base = f"{v:.{self._decimals}f}" if self._decimals else str(int(v))
        return f"{base}{self._unit}"

    def _on_slide(self, tick: int) -> None:
        v = self._min + tick * self._step
        self._val_lbl.setText(self._fmt(v))
        self.valueChanged.emit(v)

    def value(self) -> float:
        return self._min + self._slider.value() * self._step

    def set_value(self, v: float) -> None:
        self._slider.setValue(int((v - self._min) / self._step))


# ── セクションヘッダー ──────────────────────────────────────────────────────

class SectionHeader(QLabel):
    """パラメーターグループの見出し。"""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setStyleSheet(f"""
            QLabel {{
                color: {_ACCENT};
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 2px;
                padding: 6px 0 2px 0;
            }}
        """)


class Separator(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setStyleSheet(f"color: {_TRACK};")


class CollapseSection(QWidget):
    """折りたたみ可能なセクション。"""

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._btn = QPushButton(f"▶  {title}")
        self._btn.setCheckable(True)
        self._btn.setChecked(False)
        self._btn.setStyleSheet(f"""
            QPushButton {{
                color: {_TEXT_DIM};
                background: transparent;
                border: none;
                text-align: left;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1px;
                padding: 6px 0;
            }}
            QPushButton:checked {{ color: {_ACCENT}; }}
        """)
        outer.addWidget(self._btn)

        self._body = QWidget()
        self._body.setVisible(False)
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 4, 0, 0)
        self._body_lay.setSpacing(6)
        outer.addWidget(self._body)

        self._btn.toggled.connect(self._toggle)

    def _toggle(self, checked: bool) -> None:
        self._body.setVisible(checked)
        self._btn.setText(f"{'▼' if checked else '▶'}  {self._btn.text()[2:]}")

    def add_widget(self, w: QWidget) -> None:
        self._body_lay.addWidget(w)

    def add_layout(self, lay) -> None:
        self._body_lay.addLayout(lay)


# ── LoRA 行/リストウィジェット ────────────────────────────────────────────────

class LoraRowWidget(QWidget):
    """1スロット分のLoRA設定: ON/OFF・ファイル名 (編集可能コンボ)・強度・削除ボタン。"""
    changed = Signal()
    remove_requested = Signal(object)  # self を渡す

    def __init__(
        self,
        lora: str = "",
        strength: float = 1.0,
        on: bool = True,
        choices: list[str] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(6)

        self._on_cb = QCheckBox()
        self._on_cb.setChecked(on)
        self._on_cb.toggled.connect(lambda _checked: self.changed.emit())
        lay.addWidget(self._on_cb)

        self._name_combo = QComboBox()
        self._name_combo.setEditable(True)
        self._name_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._apply_choices(choices or [], lora)
        self._name_combo.currentTextChanged.connect(lambda _text: self.changed.emit())
        lay.addWidget(self._name_combo, stretch=1)

        self._strength_spin = QDoubleSpinBox()
        self._strength_spin.setRange(-10.0, 10.0)
        self._strength_spin.setSingleStep(0.05)
        self._strength_spin.setDecimals(2)
        self._strength_spin.setValue(strength)
        self._strength_spin.setFixedWidth(70)
        self._strength_spin.valueChanged.connect(lambda _v: self.changed.emit())
        lay.addWidget(self._strength_spin)

        del_btn = QPushButton("✕")
        del_btn.setFixedSize(22, 22)
        del_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {_TEXT_DIM};
                border: 1px solid {_TRACK}; border-radius: 4px; font-size: 11px;
            }}
            QPushButton:hover {{ color: #f88; border-color: #a33; }}
        """)
        del_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        lay.addWidget(del_btn)

    def _apply_choices(self, choices: list[str], current: str) -> None:
        self._name_combo.blockSignals(True)
        self._name_combo.clear()
        self._name_combo.addItems(choices)
        idx = self._name_combo.findText(current)
        if idx >= 0:
            self._name_combo.setCurrentIndex(idx)
        else:
            self._name_combo.setCurrentText(current)
        self._name_combo.blockSignals(False)

    def set_choices(self, choices: list[str]) -> None:
        self._apply_choices(choices, self._name_combo.currentText())

    def value(self) -> dict:
        return {
            "on": self._on_cb.isChecked(),
            "lora": self._name_combo.currentText().strip(),
            "strength": round(self._strength_spin.value(), 3),
        }


class LoraListWidget(QWidget):
    """LoRAスロットを可変長で管理するリスト。「+ LoRAを追加」で無制限に追加できる。"""
    changed = Signal()

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._choices: list[str] = []
        self._rows: list[LoraRowWidget] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(4)

        header_row = QHBoxLayout()
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 11px; font-weight: bold;")
        header_row.addWidget(title_lbl)
        header_row.addStretch()
        add_btn = QPushButton(tr("+ LoRAを追加"))
        add_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {_ACCENT};
                border: 1px solid {_ACCENT}; border-radius: 4px;
                padding: 2px 8px; font-size: 11px;
            }}
            QPushButton:hover {{ background: {_ACCENT}22; }}
        """)
        add_btn.clicked.connect(lambda: self.add_row())
        header_row.addWidget(add_btn)
        outer.addLayout(header_row)

        self._rows_lay = QVBoxLayout()
        self._rows_lay.setSpacing(2)
        outer.addLayout(self._rows_lay)

    def set_choices(self, choices: list[str]) -> None:
        self._choices = choices
        for row in self._rows:
            row.set_choices(choices)

    def add_row(self, lora: str = "", strength: float = 1.0, on: bool = True) -> None:
        row = LoraRowWidget(lora, strength, on, choices=self._choices)
        row.changed.connect(self.changed.emit)
        row.remove_requested.connect(self._remove_row)
        self._rows.append(row)
        self._rows_lay.addWidget(row)
        self.changed.emit()

    def _remove_row(self, row: LoraRowWidget) -> None:
        self._rows.remove(row)
        self._rows_lay.removeWidget(row)
        row.deleteLater()
        self.changed.emit()

    def clear_rows(self) -> None:
        for row in list(self._rows):
            self._rows_lay.removeWidget(row)
            row.deleteLater()
        self._rows.clear()

    def set_loras(self, loras: list[dict]) -> None:
        self.clear_rows()
        for entry in loras:
            self.add_row(
                entry.get("lora", ""),
                float(entry.get("strength", 1.0)),
                bool(entry.get("on", True)),
            )

    def value(self) -> list[dict]:
        return [row.value() for row in self._rows]

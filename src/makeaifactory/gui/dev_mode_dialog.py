"""開発モードダイアログ — 全 ComfyUI パラメーターをグラフィカル UI で操作する。"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..comfy.workflow_patcher import DevModeOverrides
from ..constants import SUPPORTED_IMAGE_EXTENSIONS
from ..domain.progress import JobProgress, JobState
from ..i18n import tr
from .dev_widgets import (
    CollapseSection,
    FaderWidget,
    KnobWidget,
    LoraListWidget,
    SectionHeader,
    Separator,
)
from .workflow_json_dialog import WorkflowJsonDialog

logger = logging.getLogger(__name__)

_ACCENT  = "#4fc3f7"
_BG      = "#0d0d1a"
_BG_MID  = "#12121f"
_BG_CARD = "#181828"
_BORDER  = "#252540"
_TEXT    = "#dde"
_DIM     = "#666"

_RESOLUTION_OPTIONS = [
    "Low (480x854 Pixel Count)",
    "Medium (720x1280 Pixel Count)",
    "Low (854x480 Pixel Count)",
    "Medium (1280x720 Pixel Count)",
    "High (1080x1920 Pixel Count)",
    "High (1920x1080 Pixel Count)",
]

_SAGE_OPTIONS = ["disabled", "auto", "enabled"]


# ── シグナル ─────────────────────────────────────────────────────────────────

class _DevSignals(QObject):
    progress     = Signal(JobProgress)
    done         = Signal(str)   # output_path
    error        = Signal(str)   # error_message
    lora_choices = Signal(list)  # ComfyUIから取得したLoRAファイル名一覧


# ── バックグラウンドワーカー ─────────────────────────────────────────────────

class _DevWorker(QRunnable):
    def __init__(self, coro, sigs: _DevSignals):
        super().__init__()
        self._coro = coro
        self._sigs = sigs

    def run(self) -> None:
        asyncio.run(self._coro)


# ── 画像プレビュードロップエリア ─────────────────────────────────────────────

class _ImageDropArea(QLabel):
    image_selected = Signal(Path)

    _CSS_IDLE = f"""
        QLabel {{
            border: 2px dashed {_BORDER};
            border-radius: 10px;
            background: {_BG};
            color: {_DIM};
            font-size: 13px;
        }}
    """
    _CSS_HOVER = f"""
        QLabel {{
            border: 2px dashed {_ACCENT};
            border-radius: 10px;
            background: #0a1520;
            color: {_ACCENT};
            font-size: 13px;
        }}
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._reset()

    def _reset(self) -> None:
        self.setText(tr("入力画像をここにドロップ\nまたはクリックして選択"))
        self.setStyleSheet(self._CSS_IDLE)
        self.setPixmap(QPixmap())

    def set_image(self, path: Path) -> None:
        img = QImage(str(path))
        if img.isNull():
            return
        pix = QPixmap.fromImage(img).scaled(
            self.width() - 4,
            self.height() - 4,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(pix)
        self.setText("")
        self.setStyleSheet(self._CSS_IDLE)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("入力画像を選択"),
            "",
            tr("画像ファイル (*.png *.jpg *.jpeg *.webp)"),
        )
        if path:
            self.image_selected.emit(Path(path))

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(self._CSS_HOVER)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.setStyleSheet(self._CSS_IDLE)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self.setStyleSheet(self._CSS_IDLE)
        urls = event.mimeData().urls()
        for url in urls:
            p = Path(url.toLocalFile())
            if p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                self.image_selected.emit(p)
                return


# ── 開発モードダイアログ ────────────────────────────────────────────────────

class DevModeDialog(QDialog):
    """
    全 ComfyUI パラメーターを操作できる開発モードダイアログ。

    run_job_fn: async (input_image: Path, overrides: DevModeOverrides,
                       on_progress: Callable) -> tuple[Path, Any]
    save_params_fn: Callable[[dict], None] — ダイアログを閉じる時にパラメーターを永続化
    load_params: dict — 前回保存したパラメーター (なければ {})
    workflow_json_text: str — makeAiFactory_api_source.json の生テキスト (空なら非表示)
    apply_workflow_json_fn: Callable[[str], str] — 編集後テキストを保存・適用する。
        失敗時はエラーメッセージを、成功時は空文字を返す。
    """

    def __init__(
        self,
        run_job_fn: Callable,
        save_params_fn: Callable[[dict], None],
        load_params: dict,
        template_defaults: DevModeOverrides | None = None,
        workflow_json_text: str = "",
        apply_workflow_json_fn: Callable[[str], str] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("開発モード — makeAiFactory"))
        self.setMinimumSize(1100, 720)
        self.resize(1200, 800)
        self.setModal(False)
        self._run_job_fn = run_job_fn
        self._save_params_fn = save_params_fn
        self._template_defaults = template_defaults
        self._workflow_json_text = workflow_json_text
        self._apply_workflow_json_fn = apply_workflow_json_fn
        self._workflow_json_dialog: WorkflowJsonDialog | None = None
        self._input_image: Path | None = None
        self._generating = False
        self._sigs = _DevSignals()

        self._sigs.progress.connect(self._on_progress,         Qt.ConnectionType.QueuedConnection)
        self._sigs.done.connect(self._on_done,                 Qt.ConnectionType.QueuedConnection)
        self._sigs.error.connect(self._on_error,               Qt.ConnectionType.QueuedConnection)
        self._sigs.lora_choices.connect(self._on_lora_choices, Qt.ConnectionType.QueuedConnection)

        self.setStyleSheet(f"""
            QDialog, QWidget {{
                background: {_BG};
                color: {_TEXT};
                font-family: "Yu Gothic UI", "Meiryo", sans-serif;
            }}
            QScrollArea {{ border: none; background: {_BG}; }}
            QPlainTextEdit {{
                background: {_BG_CARD};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                color: {_TEXT};
                font-size: 12px;
                padding: 6px;
            }}
            QComboBox {{
                background: {_BG_CARD};
                border: 1px solid {_BORDER};
                border-radius: 5px;
                color: {_TEXT};
                padding: 4px 8px;
                font-size: 12px;
                min-width: 200px;
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background: {_BG_CARD};
                color: {_TEXT};
                selection-background-color: #1a3a5a;
            }}
            QCheckBox {{ color: {_TEXT}; font-size: 12px; spacing: 6px; }}
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                border: 1px solid {_BORDER}; border-radius: 3px;
                background: {_BG_CARD};
            }}
            QCheckBox::indicator:checked {{
                background: {_ACCENT};
                border-color: {_ACCENT};
            }}
            QSpinBox, QDoubleSpinBox {{
                background: {_BG_CARD};
                border: 1px solid {_BORDER};
                border-radius: 5px;
                color: {_TEXT};
                padding: 4px 6px;
                font-size: 12px;
            }}
            QProgressBar {{
                background: #111;
                border: 1px solid {_BORDER};
                border-radius: 5px;
                height: 8px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #0d4a7a, stop:1 {_ACCENT});
                border-radius: 5px;
            }}
        """)

        self._build_ui()
        if load_params:
            self._load_params(load_params)
        elif template_defaults is not None:
            self._load_params(template_defaults.to_dict())

    # ── UI 構築 ─────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 左パネル (スクロール可能なパラメーター列) ─────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(520)
        scroll.setStyleSheet(f"QScrollArea {{ background: {_BG_MID}; border-right: 1px solid {_BORDER}; }}")

        inner = QWidget()
        inner.setStyleSheet(f"background: {_BG_MID};")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(8)

        self._build_workflow_json_link(lay)
        lay.addWidget(Separator())
        self._build_prompts(lay)
        lay.addWidget(Separator())
        self._build_quality(lay)
        lay.addWidget(Separator())
        self._build_video(lay)
        lay.addWidget(Separator())
        self._build_resolution_seed(lay)
        lay.addWidget(Separator())
        self._build_lora(lay)
        lay.addWidget(Separator())
        self._build_advanced(lay)
        lay.addStretch()

        scroll.setWidget(inner)
        root.addWidget(scroll)

        # ── 右パネル ─────────────────────────────────────────────────────
        right = QWidget()
        right.setStyleSheet(f"background: {_BG};")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(20, 20, 20, 20)
        right_lay.setSpacing(12)

        # 入力画像ドロップエリア
        right_lay.addWidget(SectionHeader(tr("入力画像")))
        self._drop_area = _ImageDropArea()
        self._drop_area.image_selected.connect(self._on_image_selected)
        right_lay.addWidget(self._drop_area, stretch=3)

        # 出力プレビュー
        right_lay.addWidget(SectionHeader(tr("生成結果")))
        self._result_lbl = QLabel(tr("生成すると動画パスが表示されます"))
        self._result_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._result_lbl.setWordWrap(True)
        self._result_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 11px; "
            f"border: 1px solid {_BORDER}; border-radius: 8px; padding: 12px;"
        )
        self._result_lbl.setMinimumHeight(60)
        right_lay.addWidget(self._result_lbl)

        self._open_btn = QPushButton(tr("フォルダで開く"))
        self._open_btn.setVisible(False)
        self._open_btn.setStyleSheet(self._btn_css("#1a3050", _ACCENT))
        right_lay.addWidget(self._open_btn)

        right_lay.addWidget(Separator())

        # ステータス
        self._status_lbl = QLabel(tr("画像をドロップして生成を開始"))
        self._status_lbl.setStyleSheet(f"color: {_DIM}; font-size: 11px;")
        right_lay.addWidget(self._status_lbl)

        # プログレスバー
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(False)
        right_lay.addWidget(self._progress_bar)

        # 生成 / キャンセルボタン
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._gen_btn = QPushButton(tr("⚡  生成開始"))
        self._gen_btn.setFixedHeight(44)
        self._gen_btn.setStyleSheet(self._btn_css("#0d3050", _ACCENT, font_size=14))
        self._gen_btn.clicked.connect(self._on_generate)
        btn_row.addWidget(self._gen_btn, stretch=2)

        self._cancel_btn = QPushButton(tr("キャンセル"))
        self._cancel_btn.setFixedHeight(44)
        self._cancel_btn.setVisible(False)
        self._cancel_btn.setStyleSheet(self._btn_css("#300d0d", "#f88"))
        btn_row.addWidget(self._cancel_btn, stretch=1)

        right_lay.addLayout(btn_row)
        right_lay.addStretch()
        root.addWidget(right, stretch=1)

    def _build_workflow_json_link(self, lay: QVBoxLayout) -> None:
        desc = QLabel(tr(
            "画像をドロップして動画化するとき、裏側では実際にこのComfyUIワークフローが"
            "使われています。下のボタンからノードごとの値をつまみ・入力欄で直接調整できます。"
        ))
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {_DIM}; font-size: 11px;")
        lay.addWidget(desc)

        btn = QPushButton(tr("🎛  ワークフローの全パラメーターを調整 (makeAiFactory_api_source.json)"))
        btn.setStyleSheet(self._btn_css("#0d3050", _ACCENT, font_size=12))
        btn.clicked.connect(self._on_open_workflow_json)
        btn.setEnabled(bool(self._workflow_json_text and self._apply_workflow_json_fn))
        lay.addWidget(btn)

    def _on_open_workflow_json(self) -> None:
        if self._workflow_json_dialog is None:
            self._workflow_json_dialog = WorkflowJsonDialog(
                self._workflow_json_text,
                self._apply_workflow_json_fn,
                parent=self,
            )
        self._workflow_json_dialog.show()
        self._workflow_json_dialog.raise_()
        self._workflow_json_dialog.activateWindow()

    def _build_prompts(self, lay: QVBoxLayout) -> None:
        lay.addWidget(SectionHeader("PROMPT"))

        lbl_p = QLabel("Positive")
        lbl_p.setStyleSheet(f"color: {_DIM}; font-size: 10px;")
        lay.addWidget(lbl_p)
        self._positive_edit = QPlainTextEdit()
        self._positive_edit.setFixedHeight(90)
        self._positive_edit.setPlaceholderText(tr("生成内容の説明（英語推奨）"))
        lay.addWidget(self._positive_edit)

        lbl_n = QLabel("Negative")
        lbl_n.setStyleSheet(f"color: {_DIM}; font-size: 10px;")
        lay.addWidget(lbl_n)
        self._negative_edit = QPlainTextEdit()
        self._negative_edit.setFixedHeight(56)
        self._negative_edit.setPlaceholderText(tr("除外したい要素（省略可）"))
        lay.addWidget(self._negative_edit)

    def _build_quality(self, lay: QVBoxLayout) -> None:
        lay.addWidget(SectionHeader("GENERATION QUALITY"))

        knob_row = QHBoxLayout()
        knob_row.setSpacing(4)

        self._steps_knob = KnobWidget("STEPS", 1, 50, 8, decimals=0, step=1)
        self._cfg_knob   = KnobWidget("CFG",   0.5, 8.0, 1.0, decimals=1, step=0.1)
        self._mcfg_knob  = KnobWidget("MOTION CFG", 0.5, 15.0, 3.0, decimals=1, step=0.1)

        knob_row.addWidget(self._steps_knob)
        knob_row.addWidget(self._cfg_knob)
        knob_row.addWidget(self._mcfg_knob)
        lay.addLayout(knob_row)

        self._mpass_fader = FaderWidget(
            "Motion Pass Steps", 0, 10, 2, decimals=0, step=1,
        )
        lay.addWidget(self._mpass_fader)

    def _build_video(self, lay: QVBoxLayout) -> None:
        lay.addWidget(SectionHeader("VIDEO SETTINGS"))

        self._len_fader = FaderWidget(
            tr("動画の長さ"), 1, 30, 5, decimals=0, step=1, unit=" " + tr("秒"),
        )
        lay.addWidget(self._len_fader)

        self._fps_fader = FaderWidget(
            "FPS", 8, 60, 16, decimals=0, step=1, unit=" fps",
        )
        lay.addWidget(self._fps_fader)

        self._upscale_fader = FaderWidget(
            tr("アップスケール"), 1, 4, 2, decimals=0, step=1, unit=" ×",
        )
        lay.addWidget(self._upscale_fader)

        self._crf_fader = FaderWidget(
            tr("品質 CRF (低いほど高品質)"), 0, 51, 19, decimals=0, step=1,
        )
        lay.addWidget(self._crf_fader)

    def _build_resolution_seed(self, lay: QVBoxLayout) -> None:
        lay.addWidget(SectionHeader("RESOLUTION & SEED"))

        res_row = QHBoxLayout()
        res_lbl = QLabel(tr("解像度"))
        res_lbl.setStyleSheet(f"color: {_DIM}; font-size: 12px;")
        res_lbl.setFixedWidth(140)
        res_row.addWidget(res_lbl)
        self._res_combo = QComboBox()
        self._res_combo.addItems(_RESOLUTION_OPTIONS)
        res_row.addWidget(self._res_combo, stretch=1)
        lay.addLayout(res_row)

        seed_row = QHBoxLayout()
        seed_lbl = QLabel(tr("シード"))
        seed_lbl.setStyleSheet(f"color: {_DIM}; font-size: 12px;")
        seed_lbl.setFixedWidth(140)
        seed_row.addWidget(seed_lbl)

        self._seed_spin = QSpinBox()
        self._seed_spin.setRange(0, 2_147_483_647)
        self._seed_spin.setValue(random.randint(0, 2_147_483_647))
        seed_row.addWidget(self._seed_spin, stretch=1)

        self._rand_cb = QCheckBox(tr("ランダム"))
        self._rand_cb.setChecked(True)
        self._rand_cb.toggled.connect(self._seed_spin.setDisabled)
        self._seed_spin.setDisabled(True)
        seed_row.addWidget(self._rand_cb)

        rand_btn = QPushButton("🔀")
        rand_btn.setFixedSize(28, 28)
        rand_btn.setStyleSheet(
            f"QPushButton {{ background: {_BG_CARD}; border: 1px solid {_BORDER}; border-radius: 4px; }}"
        )
        rand_btn.clicked.connect(
            lambda: self._seed_spin.setValue(random.randint(0, 2_147_483_647))
        )
        seed_row.addWidget(rand_btn)
        lay.addLayout(seed_row)

    def _build_lora(self, lay: QVBoxLayout) -> None:
        lay.addWidget(SectionHeader("LORA"))

        self._lora_high_list = LoraListWidget(tr("High ノイズ段"))
        lay.addWidget(self._lora_high_list)

        self._lora_low_list = LoraListWidget(tr("Low ノイズ段"))
        lay.addWidget(self._lora_low_list)

    def _build_advanced(self, lay: QVBoxLayout) -> None:
        adv = CollapseSection(tr("上級設定 ADVANCED"))

        # Sage Attention
        sage_row = QHBoxLayout()
        sage_lbl = QLabel("Sage Attention")
        sage_lbl.setStyleSheet(f"color: {_DIM}; font-size: 12px;")
        sage_lbl.setFixedWidth(140)
        sage_row.addWidget(sage_lbl)
        self._sage_combo = QComboBox()
        self._sage_combo.addItems(_SAGE_OPTIONS)
        sage_row.addWidget(self._sage_combo, stretch=1)
        sage_w = QWidget()
        sage_w.setLayout(sage_row)
        adv.add_widget(sage_w)

        # Model Shift
        self._shift_fader = FaderWidget(
            "Model Shift (H/L)", 1.0, 20.0, 7.0, decimals=1, step=0.1,
        )
        adv.add_widget(self._shift_fader)

        # lightx2v 強度 (High/Low 個別)
        self._lightx2v_high_fader = FaderWidget(
            tr("lightx2v 強度 (High)"), 0.0, 2.0, 1.0, decimals=2, step=0.01,
        )
        adv.add_widget(self._lightx2v_high_fader)
        self._lightx2v_low_fader = FaderWidget(
            tr("lightx2v 強度 (Low)"), 0.0, 2.0, 1.0, decimals=2, step=0.01,
        )
        adv.add_widget(self._lightx2v_low_fader)

        # NAG
        adv.add_widget(SectionHeader("NAG ATTENTION"))
        nag_row = QHBoxLayout()
        nag_row.setSpacing(4)
        self._nag_scale_knob = KnobWidget("SCALE",  0, 30,  11,   decimals=0,  step=1)
        self._nag_alpha_knob = KnobWidget("ALPHA",  0.0, 1.0, 0.25, decimals=2, step=0.01)
        self._nag_tau_knob   = KnobWidget("TAU",    0.0, 10.0, 2.37, decimals=2, step=0.01)
        nag_row.addWidget(self._nag_scale_knob)
        nag_row.addWidget(self._nag_alpha_knob)
        nag_row.addWidget(self._nag_tau_knob)
        nag_w = QWidget()
        nag_w.setLayout(nag_row)
        adv.add_widget(nag_w)

        lay.addWidget(adv)

    # ── パラメーター保存/復元 ────────────────────────────────────────────────

    def _current_overrides(self) -> DevModeOverrides:
        ov = DevModeOverrides(
            positive_prompt=self._positive_edit.toPlainText(),
            negative_prompt=self._negative_edit.toPlainText(),
            steps=int(self._steps_knob.value()),
            cfg=round(self._cfg_knob.value(), 2),
            motion_cfg=round(self._mcfg_knob.value(), 2),
            motion_pass_steps=int(self._mpass_fader.value()),
            video_length_sec=int(self._len_fader.value()),
            video_fps=int(self._fps_fader.value()),
            resolution_mode=self._res_combo.currentText(),
            upscale_multiplier=int(self._upscale_fader.value()),
            crf=int(self._crf_fader.value()),
            seed=None if self._rand_cb.isChecked() else self._seed_spin.value(),
            sage_attention=self._sage_combo.currentText(),
            model_shift=round(self._shift_fader.value(), 2),
            lightx2v_strength_high=round(self._lightx2v_high_fader.value(), 3),
            lightx2v_strength_low=round(self._lightx2v_low_fader.value(), 3),
            nag_scale=self._nag_scale_knob.value(),
            nag_alpha=round(self._nag_alpha_knob.value(), 3),
            nag_tau=round(self._nag_tau_knob.value(), 3),
            loras_high=self._lora_high_list.value(),
            loras_low=self._lora_low_list.value(),
        )
        return ov

    def _load_params(self, params: dict) -> None:
        if not params:
            return
        try:
            ov = DevModeOverrides.from_dict(params)
            if ov.positive_prompt is not None:
                self._positive_edit.setPlainText(ov.positive_prompt)
            if ov.negative_prompt is not None:
                self._negative_edit.setPlainText(ov.negative_prompt)
            if ov.steps is not None:           self._steps_knob.set_value(ov.steps)
            if ov.cfg is not None:             self._cfg_knob.set_value(ov.cfg)
            if ov.motion_cfg is not None:      self._mcfg_knob.set_value(ov.motion_cfg)
            if ov.motion_pass_steps is not None: self._mpass_fader.set_value(ov.motion_pass_steps)
            if ov.video_length_sec is not None: self._len_fader.set_value(ov.video_length_sec)
            if ov.video_fps is not None:       self._fps_fader.set_value(ov.video_fps)
            if ov.resolution_mode:
                idx = self._res_combo.findText(ov.resolution_mode)
                if idx >= 0:
                    self._res_combo.setCurrentIndex(idx)
            if ov.upscale_multiplier is not None: self._upscale_fader.set_value(ov.upscale_multiplier)
            if ov.crf is not None:             self._crf_fader.set_value(ov.crf)
            if ov.seed is not None:
                self._rand_cb.setChecked(False)
                self._seed_spin.setValue(ov.seed)
            if ov.sage_attention:
                idx = self._sage_combo.findText(ov.sage_attention)
                if idx >= 0:
                    self._sage_combo.setCurrentIndex(idx)
            if ov.model_shift is not None:        self._shift_fader.set_value(ov.model_shift)
            if ov.lightx2v_strength_high is not None: self._lightx2v_high_fader.set_value(ov.lightx2v_strength_high)
            if ov.lightx2v_strength_low is not None:  self._lightx2v_low_fader.set_value(ov.lightx2v_strength_low)
            if ov.nag_scale is not None:           self._nag_scale_knob.set_value(ov.nag_scale)
            if ov.nag_alpha is not None:           self._nag_alpha_knob.set_value(ov.nag_alpha)
            if ov.nag_tau is not None:             self._nag_tau_knob.set_value(ov.nag_tau)
            if ov.loras_high is not None:          self._lora_high_list.set_loras(ov.loras_high)
            if ov.loras_low is not None:           self._lora_low_list.set_loras(ov.loras_low)
        except Exception as e:
            logger.warning("dev_mode_params 復元失敗: %s", e)

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self._save_params_fn(self._current_overrides().to_dict())
        except Exception as e:
            logger.warning("dev_mode_params 保存失敗: %s", e)
        super().closeEvent(event)

    # ── 画像選択 ────────────────────────────────────────────────────────────

    @Slot(Path)
    def _on_image_selected(self, path: Path) -> None:
        self._input_image = path
        self._drop_area.set_image(path)
        self._status_lbl.setText(tr("入力: {name}").format(name=path.name))
        self._status_lbl.setStyleSheet(f"color: {_ACCENT}; font-size: 11px;")

    # ── 生成コントロール ─────────────────────────────────────────────────────

    @Slot()
    def _on_generate(self) -> None:
        if self._generating:
            return
        if self._input_image is None:
            self._status_lbl.setText(tr("画像を選択してください"))
            self._status_lbl.setStyleSheet("color: #f88; font-size: 11px;")
            return

        overrides = self._current_overrides()
        if self._rand_cb.isChecked():
            overrides.seed = random.randint(0, 2_147_483_647)
            self._seed_spin.setValue(overrides.seed)

        self._generating = True
        self._gen_btn.setEnabled(False)
        self._cancel_btn.setVisible(True)
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._result_lbl.setText(tr("生成中..."))
        self._open_btn.setVisible(False)
        self._status_lbl.setStyleSheet(f"color: {_ACCENT}; font-size: 11px;")

        sigs = self._sigs
        run_fn = self._run_job_fn
        img = self._input_image

        async def _run() -> None:
            def _cb(p: JobProgress) -> None:
                sigs.progress.emit(p)
            try:
                output, _bench = await run_fn(img, overrides, _cb)
                sigs.done.emit(str(output))
            except Exception as e:
                sigs.error.emit(str(e))

        pool = QThreadPool.globalInstance()
        pool.start(_DevWorker(_run(), self._sigs))

    @Slot(JobProgress)
    def _on_progress(self, p: JobProgress) -> None:
        state_pct = {
            JobState.UPLOADING: 5,
            JobState.QUEUED: 8,
            JobState.RESOLVING_OUTPUT: 92,
            JobState.COMPLETED: 100,
        }
        if p.state == JobState.GENERATING:
            pct = int(10.0 + p.percent * 0.80)
        else:
            pct = state_pct.get(p.state, 0)
        self._progress_bar.setValue(pct)
        self._status_lbl.setText(p.message or tr("生成中..."))

    @Slot(str)
    def _on_done(self, output_path: str) -> None:
        self._generating = False
        self._gen_btn.setEnabled(True)
        self._cancel_btn.setVisible(False)
        self._progress_bar.setValue(100)
        self._status_lbl.setText(tr("生成完了！"))
        self._status_lbl.setStyleSheet(f"color: #66bb6a; font-size: 11px;")
        self._result_lbl.setText(output_path)
        self._result_lbl.setStyleSheet(
            f"color: {_ACCENT}; font-size: 11px; "
            f"border: 1px solid {_BORDER}; border-radius: 8px; padding: 12px;"
        )
        self._open_btn.setVisible(True)
        folder = str(Path(output_path).parent)
        self._open_btn.clicked.connect(lambda: os.startfile(folder), Qt.ConnectionType.UniqueConnection)

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        self._generating = False
        self._gen_btn.setEnabled(True)
        self._cancel_btn.setVisible(False)
        self._progress_bar.setValue(0)
        self._status_lbl.setText(tr("エラー: {msg}").format(msg=msg))
        self._status_lbl.setStyleSheet("color: #f88; font-size: 11px;")

    # ── LoRA 選択肢 ───────────────────────────────────────────────────────────

    def fetch_lora_choices(self, fetch_fn: Callable) -> None:
        """ComfyUIから利用可能なLoRAファイル名一覧を非同期取得する。

        fetch_fn: async () -> list[str]
        取得できなくても自由入力で利用は継続できるため、失敗は無視する。
        """
        sigs = self._sigs

        async def _run() -> None:
            try:
                choices = await fetch_fn()
                sigs.lora_choices.emit(choices)
            except Exception as e:
                logger.debug("LoRA一覧取得スキップ: %s", e)

        pool = QThreadPool.globalInstance()
        pool.start(_DevWorker(_run(), self._sigs))

    @Slot(list)
    def _on_lora_choices(self, choices: list[str]) -> None:
        self._lora_high_list.set_choices(choices)
        self._lora_low_list.set_choices(choices)

    # ── ヘルパー ────────────────────────────────────────────────────────────

    @staticmethod
    def _btn_css(bg: str, border: str, font_size: int = 13) -> str:
        return f"""
            QPushButton {{
                background: {bg};
                color: #eee;
                border: 1px solid {border};
                border-radius: 7px;
                padding: 8px 20px;
                font-size: {font_size}px;
            }}
            QPushButton:hover {{ background: {border}22; border-color: {border}; }}
            QPushButton:disabled {{ color: #444; border-color: #333; }}
        """

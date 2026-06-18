from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal, Slot, QTimer
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QLinearGradient, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..constants import APP_NAME, APP_VERSION
from .prompt_highlighter import PromptHighlighter
from .loading_clock import LoadingClock

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    generate_requested = Signal(str, str, str, int)
    cancel_requested = Signal()
    prompt_changed = Signal(str, str)
    preset_save_requested = Signal(str, str, str)
    preset_load_requested = Signal(str)
    preset_delete_requested = Signal(str)
    preset_overwrite_requested = Signal(str, str, str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(900, 650)
        self._gallery_items: list[tuple[str, Path]] = []
        self._setup_style()
        self._build_ui()
        self._build_menu()
        self._loading_clock = LoadingClock(self)
        self._loading_clock.hide()

    def _setup_style(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #0f0f1a;
                color: #eee;
                font-family: "Yu Gothic UI", "Meiryo", sans-serif;
            }
            QMenuBar {
                background: #111;
                color: #ccc;
            }
            QMenuBar::item:selected { background: #1a1a2e; }
            QMenu {
                background: #1a1a2e;
                color: #ccc;
                border: 1px solid #333;
            }
            QMenu::item:selected { background: #253858; }
            QStatusBar { background: #111; color: #999; font-size: 12px; }
            QPlainTextEdit, QLineEdit, QSpinBox, QComboBox {
                background: #1a1a2e;
                color: #eee;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QPushButton {
                background: #1a1a2e;
                color: #ccc;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 6px 16px;
                font-size: 13px;
            }
            QPushButton:hover { background: #253858; }
            QPushButton:disabled { color: #555; background: #111; }
            QPlainTextEdit:disabled { color: #444; background: #0a0a12; }
            QLineEdit:disabled { color: #444; background: #0a0a12; }
            QSpinBox:disabled { color: #444; background: #0a0a12; }
            QComboBox:disabled { color: #444; background: #0a0a12; }
            QCheckBox:disabled { color: #555; }
            QPushButton#generate_btn {
                background: #1b5e20;
                color: #fff;
                font-weight: bold;
                font-size: 14px;
                padding: 8px 24px;
                border: none;
                border-radius: 6px;
            }
            QPushButton#generate_btn:hover { background: #2e7d32; }
            QPushButton#generate_btn:disabled { background: #333; color: #666; }
            QPushButton#cancel_btn {
                background: #b71c1c;
                color: #fff;
                font-weight: bold;
                padding: 8px 24px;
                border: none;
                border-radius: 6px;
            }
            QPushButton#cancel_btn:hover { background: #c62828; }
            QGroupBox {
                border: 1px solid #333;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 16px;
                font-weight: bold;
                color: #aaa;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
            }
            QProgressBar {
                background: #1a1a2e;
                border: 1px solid #333;
                border-radius: 4px;
                text-align: center;
                color: #eee;
                min-height: 18px;
            }
            QProgressBar::chunk {
                background: #1b5e20;
                border-radius: 3px;
            }
            QListWidget {
                background: #111;
                border: 1px solid #333;
                border-radius: 4px;
            }
            QListWidget::item { padding: 4px; }
            QListWidget::item:selected { background: #253858; }
            QSplitter::handle { background: #333; }
            QLabel#preview_label {
                background: #111;
                border: 1px solid #333;
                border-radius: 4px;
            }
        """)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_layout.setSpacing(8)

        prompt_group = QGroupBox("プロンプト")
        prompt_layout = QVBoxLayout(prompt_group)

        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("プリセット:"))
        self._preset_combo = QComboBox()
        self._preset_combo.setMinimumWidth(150)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        preset_layout.addWidget(self._preset_combo, 1)

        self._preset_save_btn = QPushButton("保存")
        self._preset_save_btn.setStyleSheet("padding: 4px 12px; font-size: 12px;")
        self._preset_save_btn.clicked.connect(self._on_save_preset)
        preset_layout.addWidget(self._preset_save_btn)

        self._preset_delete_btn = QPushButton("削除")
        self._preset_delete_btn.setStyleSheet("padding: 4px 12px; font-size: 12px;")
        self._preset_delete_btn.clicked.connect(self._on_delete_preset)
        preset_layout.addWidget(self._preset_delete_btn)

        prompt_layout.addLayout(preset_layout)

        pos_label = QLabel("Positive Prompt:")
        pos_label.setStyleSheet("color: #81c784; font-size: 12px;")
        prompt_layout.addWidget(pos_label)

        self._positive_edit = QPlainTextEdit()
        self._positive_edit.setPlaceholderText("生成したい画像の説明を入力...\n# または // で行をコメントアウトできます")
        self._positive_edit.setMinimumHeight(100)
        self._positive_edit.setMaximumHeight(180)
        self._positive_highlighter = PromptHighlighter(self._positive_edit.document())
        self._positive_edit.textChanged.connect(self._on_prompt_text_changed)
        prompt_layout.addWidget(self._positive_edit)

        neg_label = QLabel("Negative Prompt:")
        neg_label.setStyleSheet("color: #e57373; font-size: 12px;")
        prompt_layout.addWidget(neg_label)

        self._negative_edit = QPlainTextEdit()
        self._negative_edit.setPlaceholderText("生成したくない要素を入力...\n# または // で行をコメントアウトできます")
        self._negative_edit.setMinimumHeight(60)
        self._negative_edit.setMaximumHeight(120)
        self._negative_highlighter = PromptHighlighter(self._negative_edit.document())
        self._negative_edit.textChanged.connect(self._on_prompt_text_changed)
        prompt_layout.addWidget(self._negative_edit)

        self._positive_edit.installEventFilter(self)
        self._negative_edit.installEventFilter(self)

        size_layout = QHBoxLayout()
        size_layout.addWidget(QLabel("解像度:"))
        self._width_spin = QSpinBox()
        self._width_spin.setRange(64, 4096)
        self._width_spin.setValue(1280)
        self._width_spin.setSingleStep(64)
        size_layout.addWidget(self._width_spin)
        size_layout.addWidget(QLabel("x"))
        self._height_spin = QSpinBox()
        self._height_spin.setRange(64, 4096)
        self._height_spin.setValue(720)
        self._height_spin.setSingleStep(64)
        size_layout.addWidget(self._height_spin)
        size_layout.addStretch()
        prompt_layout.addLayout(size_layout)

        seed_layout = QHBoxLayout()
        self._seed_random_cb = QCheckBox("ランダムSEED")
        self._seed_random_cb.setChecked(True)
        seed_layout.addWidget(self._seed_random_cb)
        seed_layout.addWidget(QLabel("SEED:"))
        self._seed_spin = QSpinBox()
        self._seed_spin.setRange(0, 2147483647)
        self._seed_spin.setValue(0)
        self._seed_spin.setEnabled(False)
        seed_layout.addWidget(self._seed_spin)
        seed_layout.addStretch()
        prompt_layout.addLayout(seed_layout)

        self._seed_random_cb.toggled.connect(lambda checked: self._seed_spin.setEnabled(not checked))

        left_layout.addWidget(prompt_group)

        gen_group = QGroupBox("生成")
        gen_layout = QVBoxLayout(gen_group)

        wf_layout = QHBoxLayout()
        wf_layout.addWidget(QLabel("ワークフロー:"))
        self._workflow_combo = QComboBox()
        self._workflow_combo.setMinimumWidth(200)
        wf_layout.addWidget(self._workflow_combo, 1)
        gen_layout.addLayout(wf_layout)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        self._gen_once_btn = QPushButton("1回生成")
        self._gen_once_btn.setObjectName("generate_btn")
        self._gen_once_btn.clicked.connect(lambda: self._on_generate("once"))
        btn_layout.addWidget(self._gen_once_btn)

        self._gen_n_btn = QPushButton("5回生成")
        self._gen_n_btn.clicked.connect(lambda: self._on_generate("n"))
        btn_layout.addWidget(self._gen_n_btn)

        self._gen_inf_btn = QPushButton("∞回生成")
        self._gen_inf_btn.clicked.connect(lambda: self._on_generate("inf"))
        btn_layout.addWidget(self._gen_inf_btn)

        gen_layout.addLayout(btn_layout)

        n_layout = QHBoxLayout()
        n_layout.addWidget(QLabel("回数:"))
        self._n_spin = QSpinBox()
        self._n_spin.setRange(2, 999)
        self._n_spin.setValue(5)
        self._n_spin.valueChanged.connect(self._update_n_btn_label)
        n_layout.addWidget(self._n_spin)
        n_layout.addStretch()
        gen_layout.addLayout(n_layout)

        self._cancel_btn = QPushButton("キャンセル")
        self._cancel_btn.setObjectName("cancel_btn")
        self._cancel_btn.setVisible(False)
        self._cancel_btn.clicked.connect(self.cancel_requested)
        gen_layout.addWidget(self._cancel_btn)

        left_layout.addWidget(gen_group)

        progress_group = QGroupBox("進捗")
        progress_layout = QVBoxLayout(progress_group)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        progress_layout.addWidget(self._progress_bar)
        self._progress_msg = QLabel("")
        self._progress_msg.setStyleSheet("font-size: 12px; color: #aaa;")
        progress_layout.addWidget(self._progress_msg)
        left_layout.addWidget(progress_group)

        left_layout.addStretch()

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_layout.setSpacing(8)

        preview_group = QGroupBox("プレビュー")
        preview_layout = QVBoxLayout(preview_group)

        self._preview_label = QLabel("生成された画像がここに表示されます")
        self._preview_label.setObjectName("preview_label")
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setMinimumSize(400, 300)
        self._preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._preview_label.setScaledContents(False)
        preview_layout.addWidget(self._preview_label)

        self._preview_pixmap: QPixmap | None = None

        right_layout.addWidget(preview_group, 3)

        gallery_group = QGroupBox("生成物一覧")
        gallery_layout = QVBoxLayout(gallery_group)

        self._gallery_list = QListWidget()
        self._gallery_list.setIconSize(QSize(64, 64))
        self._gallery_list.itemClicked.connect(self._on_gallery_item_clicked)
        gallery_layout.addWidget(self._gallery_list)

        gal_btn_layout = QHBoxLayout()
        self._open_output_btn = QPushButton("保存フォルダを開く")
        self._open_output_btn.clicked.connect(self._open_output_dir)
        gal_btn_layout.addWidget(self._open_output_btn)
        self._clear_gallery_btn = QPushButton("一覧クリア")
        self._clear_gallery_btn.clicked.connect(self._clear_gallery)
        gal_btn_layout.addWidget(self._clear_gallery_btn)
        gallery_layout.addLayout(gal_btn_layout)

        right_layout.addWidget(gallery_group, 2)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([350, 550])

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        self._output_dir: Path | None = None
        self._logs_dir: Path | None = None
        self._system_info_text: str = ""
        self._repair_callback = None
        self._change_location_cb = None
        self._auto_save_folder_cb = None
        self._auto_toggle_cb = None
        self._vram_mode_callback = None
        self._se_enabled_callback = None
        self._se_batch_callback = None
        self._se_volume_callback = None
        self._always_on_top_callback = None
        self._naming_pattern_cb = None
        self._current_naming_pattern = "{timestamp}_{seed}"
        self._gaming_skin_active = False
        self._gaming_timer = QTimer(self)
        self._gaming_hue = 0
        self._gaming_timer.timeout.connect(self._update_gaming_animation)
        self._gaming_stats: dict = {}
        self._gaming_stats_lock = threading.Lock()
        self._gaming_stats_thread: threading.Thread | None = None
        self._gaming_stats_stop = threading.Event()
        self._gradient_offset = 0.0
        self._gradient_timer = QTimer(self)
        self._gradient_timer.setInterval(50)
        self._gradient_timer.timeout.connect(self._update_gradient_animation)
        self._is_generating = False

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("ファイル")
        open_output_action = QAction("保存フォルダを開く", self)
        open_output_action.triggered.connect(self._open_output_dir)
        file_menu.addAction(open_output_action)
        naming_action = QAction("ファイル名パターン...", self)
        naming_action.triggered.connect(self._change_naming_pattern)
        file_menu.addAction(naming_action)

        file_menu.addSeparator()
        save_preset_action = QAction("プリセットを上書き保存", self)
        save_preset_action.setShortcut(QKeySequence("Ctrl+S"))
        save_preset_action.triggered.connect(self._on_save_preset_overwrite)
        file_menu.addAction(save_preset_action)

        file_menu.addSeparator()
        quit_action = QAction("終了", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        settings_menu = menu_bar.addMenu("設定")

        change_loc_action = QAction("インストール場所を変更...", self)
        change_loc_action.triggered.connect(self._change_install_location)
        settings_menu.addAction(change_loc_action)

        auto_save_action = QAction("自動保存先を設定...", self)
        auto_save_action.triggered.connect(self._change_auto_save_folder)
        settings_menu.addAction(auto_save_action)

        self._auto_save_enabled_action = QAction("完成時に自動保存する", self)
        self._auto_save_enabled_action.setCheckable(True)
        self._auto_save_enabled_action.setChecked(False)
        self._auto_save_enabled_action.triggered.connect(self._on_auto_save_toggled)
        settings_menu.addAction(self._auto_save_enabled_action)

        self._always_on_top_action = QAction("常に最前面に表示", self)
        self._always_on_top_action.setCheckable(True)
        self._always_on_top_action.setChecked(False)
        self._always_on_top_action.triggered.connect(self._on_always_on_top_toggled)
        settings_menu.addAction(self._always_on_top_action)

        settings_menu.addSeparator()
        vram_menu = settings_menu.addMenu("VRAMモード")
        from PySide6.QtGui import QActionGroup
        self._vram_group = QActionGroup(self)
        self._vram_group.setExclusive(True)
        self._vram_actions: dict[str, QAction] = {}
        for mode, label in [
            ("normal", "通常モード (推奨: 16GB+)"),
            ("novram", "超省VRAMモード --novram"),
        ]:
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(mode == "normal")
            act.triggered.connect(lambda checked, m=mode: self._on_vram_mode_selected(m))
            self._vram_group.addAction(act)
            vram_menu.addAction(act)
            self._vram_actions[mode] = act

        settings_menu.addSeparator()
        se_menu = settings_menu.addMenu("完成通知音")

        self._se_enabled_action = QAction("通知音を鳴らす", self)
        self._se_enabled_action.setCheckable(True)
        self._se_enabled_action.setChecked(True)
        self._se_enabled_action.triggered.connect(self._on_se_enabled_toggled)
        se_menu.addAction(self._se_enabled_action)

        se_menu.addSeparator()
        self._se_volume_group = QActionGroup(self)
        self._se_volume_group.setExclusive(True)
        self._se_volume_actions: dict[int, QAction] = {}
        for vol in (25, 50, 75, 100):
            act = QAction(f"音量 {vol}%", self)
            act.setCheckable(True)
            act.triggered.connect(lambda checked, v=vol: self._on_se_volume_selected(v))
            self._se_volume_group.addAction(act)
            se_menu.addAction(act)
            self._se_volume_actions[vol] = act

        se_menu.addSeparator()
        se_batch_menu = se_menu.addMenu("連続生成時の通知音")
        self._se_batch_each_action = QAction("毎回鳴らす", self)
        self._se_batch_each_action.setCheckable(True)
        self._se_batch_each_action.triggered.connect(lambda checked: self._on_se_batch_mode("each"))
        se_batch_menu.addAction(self._se_batch_each_action)
        self._se_batch_final_action = QAction("全て完了時のみ", self)
        self._se_batch_final_action.setCheckable(True)
        self._se_batch_final_action.setChecked(True)
        self._se_batch_final_action.triggered.connect(lambda checked: self._on_se_batch_mode("final"))
        se_batch_menu.addAction(self._se_batch_final_action)
        self._se_batch_group = QActionGroup(self)
        self._se_batch_group.addAction(self._se_batch_each_action)
        self._se_batch_group.addAction(self._se_batch_final_action)

        help_menu = menu_bar.addMenu("ヘルプ")
        log_action = QAction("ログを開く", self)
        log_action.triggered.connect(self._open_logs)
        help_menu.addAction(log_action)

        diag_action = QAction("runtime診断", self)
        diag_action.triggered.connect(self._show_diagnostics)
        help_menu.addAction(diag_action)

        help_menu.addSeparator()
        self._gaming_skin_action = QAction("⚡特殊スキン⚡", self)
        self._gaming_skin_action.setCheckable(True)
        self._gaming_skin_action.setChecked(False)
        self._gaming_skin_action.triggered.connect(self._toggle_gaming_skin)
        help_menu.addAction(self._gaming_skin_action)

        help_menu.addSeparator()
        about_action = QAction("バージョン情報", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def set_paths(self, logs_dir: Path, output_dir: Path) -> None:
        self._logs_dir = logs_dir
        self._output_dir = output_dir

    def set_system_info(self, text: str) -> None:
        self._system_info_text = text

    def set_repair_callback(self, cb) -> None:
        self._repair_callback = cb

    def set_change_location_callback(self, cb) -> None:
        self._change_location_cb = cb

    def set_auto_save_folder_callback(self, cb) -> None:
        self._auto_save_folder_cb = cb

    def set_auto_save_toggle_callback(self, cb) -> None:
        self._auto_toggle_cb = cb

    def set_auto_save_checked(self, checked: bool) -> None:
        self._auto_save_enabled_action.setChecked(checked)

    def _on_auto_save_toggled(self, checked: bool) -> None:
        if self._auto_toggle_cb:
            self._auto_toggle_cb(checked)

    def set_always_on_top_callback(self, cb) -> None:
        self._always_on_top_callback = cb

    def set_always_on_top(self, enabled: bool) -> None:
        self._always_on_top_action.setChecked(enabled)
        self._apply_always_on_top(enabled)

    def _apply_always_on_top(self, enabled: bool) -> None:
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if was_visible:
            self.show()

    def _on_always_on_top_toggled(self, checked: bool) -> None:
        self._apply_always_on_top(checked)
        if self._always_on_top_callback:
            self._always_on_top_callback(checked)

    def set_se_enabled_callback(self, cb) -> None:
        self._se_enabled_callback = cb

    def set_se_enabled_checked(self, checked: bool) -> None:
        self._se_enabled_action.setChecked(checked)

    def _on_se_enabled_toggled(self, checked: bool) -> None:
        if self._se_enabled_callback:
            self._se_enabled_callback(checked)

    def set_se_batch_callback(self, cb) -> None:
        self._se_batch_callback = cb

    def set_se_batch_checked(self, checked: bool) -> None:
        pass

    def set_se_batch_mode_checked(self, mode: str) -> None:
        if mode == "each":
            self._se_batch_each_action.setChecked(True)
        else:
            self._se_batch_final_action.setChecked(True)

    def _on_se_batch_mode(self, mode: str) -> None:
        if self._se_batch_callback:
            self._se_batch_callback(mode)

    def set_se_volume_callback(self, cb) -> None:
        self._se_volume_callback = cb

    def set_se_volume_checked(self, volume: int) -> None:
        if volume in self._se_volume_actions:
            self._se_volume_actions[volume].setChecked(True)

    def _on_se_volume_selected(self, volume: int) -> None:
        if self._se_volume_callback:
            self._se_volume_callback(volume)

    def set_vram_mode_callback(self, cb) -> None:
        self._vram_mode_callback = cb

    def set_current_vram_mode(self, mode: str) -> None:
        if mode in self._vram_actions:
            self._vram_actions[mode].setChecked(True)

    def _on_vram_mode_selected(self, mode: str) -> None:
        if self._vram_mode_callback:
            self._vram_mode_callback(mode)

    def set_preset_change_callback(self, cb) -> None:
        pass

    def set_preset_add_callback(self, cb) -> None:
        pass

    def update_preset_menu(self, installed_presets: list, active_preset: str) -> None:
        pass

    def set_sage_attention_callback(self, cb) -> None:
        pass

    def set_sage_attention_checked(self, checked: bool) -> None:
        pass

    def set_sage_attention_available(self, available: bool) -> None:
        pass

    def update_workflow_list(self, workflows: list[str], active: str) -> None:
        self._workflow_combo.clear()
        for wf in workflows:
            self._workflow_combo.addItem(wf)
        idx = self._workflow_combo.findText(active)
        if idx >= 0:
            self._workflow_combo.setCurrentIndex(idx)

    def set_prompts(self, positive: str, negative: str) -> None:
        self.set_prompts_without_signal(positive, negative)

    def set_resolution(self, width: int, height: int) -> None:
        self._width_spin.setValue(width)
        self._height_spin.setValue(height)

    def get_active_workflow(self) -> str:
        return self._workflow_combo.currentText()

    def is_seed_random(self) -> bool:
        return self._seed_random_cb.isChecked()

    def get_seed(self) -> int:
        return self._seed_spin.value()

    def set_seed_mode(self, mode: str, value: int = 0) -> None:
        if mode == "fixed":
            self._seed_random_cb.setChecked(False)
            self._seed_spin.setValue(value)
        else:
            self._seed_random_cb.setChecked(True)

    def set_naming_pattern_callback(self, cb) -> None:
        self._naming_pattern_cb = cb

    def set_gaming_skin_callback(self, cb) -> None:
        self._gaming_skin_cb = cb

    def set_gaming_skin_checked(self, checked: bool) -> None:
        self._gaming_skin_action.setChecked(checked)
        self._toggle_gaming_skin(checked)

    def _update_n_btn_label(self, value: int) -> None:
        self._gen_n_btn.setText(f"{value}回生成")

    def _on_generate(self, mode: str) -> None:
        positive = self._positive_edit.toPlainText().strip()
        negative = self._negative_edit.toPlainText().strip()
        workflow = self._workflow_combo.currentText()
        n = self._n_spin.value()

        if not positive and not negative:
            QMessageBox.warning(self, "警告", "プロンプトを入力してください")
            return

        self.generate_requested.emit(positive, negative, mode, n)

    def show_generating(self, cancellable: bool = True) -> None:
        self._is_generating = cancellable
        self._progress_bar.setValue(0)
        self._progress_msg.setText("")
        self._cancel_btn.setVisible(cancellable)
        self._gen_once_btn.setEnabled(False)
        self._gen_n_btn.setEnabled(False)
        self._gen_inf_btn.setEnabled(False)
        self._positive_edit.setEnabled(False)
        self._negative_edit.setEnabled(False)
        self._width_spin.setEnabled(False)
        self._height_spin.setEnabled(False)
        self._seed_random_cb.setEnabled(False)
        self._seed_spin.setEnabled(False)
        self._workflow_combo.setEnabled(False)
        self._preset_combo.setEnabled(False)
        self._preset_save_btn.setEnabled(False)
        self._preset_delete_btn.setEnabled(False)
        if not cancellable:
            self._loading_clock.setGeometry(self.rect())
            self._loading_clock.raise_()
            self._loading_clock.start()
            self._loading_clock.show()
            self._hide_countdown = 0
        else:
            self._loading_clock.stop()
            self._loading_clock.hide()

    def hide_generating(self) -> None:
        self._is_generating = False
        if self._loading_clock.isVisible():
            self._loading_clock.finish_setup()
        self._progress_msg.setText("")
        self._cancel_btn.setVisible(False)
        self._gen_once_btn.setEnabled(True)
        self._gen_n_btn.setEnabled(True)
        self._gen_inf_btn.setEnabled(True)
        self._positive_edit.setEnabled(True)
        self._negative_edit.setEnabled(True)
        self._width_spin.setEnabled(True)
        self._height_spin.setEnabled(True)
        self._seed_random_cb.setEnabled(True)
        self._seed_spin.setEnabled(not self._seed_random_cb.isChecked())
        self._workflow_combo.setEnabled(True)
        self._preset_combo.setEnabled(True)
        self._preset_save_btn.setEnabled(True)
        self._preset_delete_btn.setEnabled(self._preset_combo.currentIndex() > 0)

    def update_progress(self, message: str, percent: float, detail: str = "") -> None:
        self._progress_msg.setText(message)
        self._progress_bar.setValue(int(percent))
        if self._loading_clock.isVisible():
            self._loading_clock.set_progress(message, percent, detail)

    def show_preview(self, image_path: Path) -> None:
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            return
        self._preview_pixmap = pixmap
        self._rescale_preview()

    def show_preview_image(self, image: QImage) -> None:
        if image.isNull():
            return
        pixmap = QPixmap.fromImage(image)
        self._preview_pixmap = pixmap
        self._rescale_preview()

    def _rescale_preview(self) -> None:
        if self._preview_pixmap and not self._preview_pixmap.isNull():
            scaled = self._preview_pixmap.scaled(
                self._preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._preview_label.setPixmap(scaled)

    def add_to_gallery(self, image_path: Path) -> None:
        try:
            mtime = datetime.fromtimestamp(image_path.stat().st_mtime)
            label = mtime.strftime("%Y%m%d %H:%M:%S")
        except Exception:
            label = image_path.stem
        self._gallery_items.append((label, image_path))
        item = QListWidgetItem(label)
        try:
            pixmap = QPixmap(str(image_path))
            if not pixmap.isNull():
                thumb = pixmap.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                from PySide6.QtGui import QIcon
                item.setIcon(QIcon(thumb))
        except Exception:
            pass
        self._gallery_list.insertItem(0, item)

    def _on_gallery_item_clicked(self, item: QListWidgetItem) -> None:
        idx = self._gallery_list.row(item)
        if 0 <= idx < len(self._gallery_items):
            _, path = self._gallery_items[idx]
            self.show_preview(path)

    def _clear_gallery(self) -> None:
        self._gallery_list.clear()
        self._gallery_items.clear()
        self._preview_label.setText("生成された画像がここに表示されます")
        self._preview_label.setPixmap(QPixmap())
        self._preview_pixmap = None

    def _open_output_dir(self) -> None:
        if self._output_dir and self._output_dir.exists():
            os.startfile(str(self._output_dir))

    def _open_logs(self) -> None:
        if self._logs_dir and self._logs_dir.exists():
            os.startfile(str(self._logs_dir))

    def _show_diagnostics(self) -> None:
        QMessageBox.information(self, "runtime診断", self._system_info_text or "診断情報がありません。")

    def _change_install_location(self) -> None:
        if self._change_location_cb:
            self._change_location_cb()

    def _change_auto_save_folder(self) -> None:
        if self._auto_save_folder_cb:
            self._auto_save_folder_cb()

    def _change_naming_pattern(self) -> None:
        from PySide6.QtWidgets import QComboBox as _QComboBox, QDialog as _QDialog, QDialogButtonBox as _QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("ファイル名パターン")
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel("出力ファイル名のパターンを設定します。\n"
                                  "変数: {timestamp} {seed} {job_id}\n"
                                  "例: {timestamp}_{seed}  →  20260617_142654_3688324904"))
        combo = _QComboBox()
        combo.setEditable(True)
        combo.addItems(["{timestamp}_{seed}", "{timestamp}", "{seed}", "{job_id}"])
        combo.setCurrentText(self._current_naming_pattern)
        layout.addWidget(combo)

        btns = _QDialogButtonBox(_QDialogButtonBox.StandardButton.Ok | _QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec() == _QDialog.DialogCode.Accepted:
            pattern = combo.currentText().strip()
            if pattern:
                self._current_naming_pattern = pattern
                if self._naming_pattern_cb:
                    self._naming_pattern_cb(pattern)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            f"{APP_NAME}について",
            f"{APP_NAME} v{APP_VERSION}\n\n"
            "プロンプトからAI画像を生成するアプリです。\n"
            "生成はすべてローカルPCで行われます。",
        )

    def show_error(self, title: str, message: str, detail: str = "", show_repair: bool = False) -> None:
        QMessageBox.critical(self, title, message)

    def update_status(self, message: str) -> None:
        self._status_bar.showMessage(message)

    def _on_prompt_text_changed(self) -> None:
        self.prompt_changed.emit(
            self._positive_edit.toPlainText(),
            self._negative_edit.toPlainText(),
        )

    def _on_save_preset(self) -> None:
        name, ok = QInputDialog.getText(
            self,
            "プリセットを保存",
            "プリセット名を入力してください:",
        )
        if ok and name.strip():
            self.preset_save_requested.emit(
                name.strip(),
                self._positive_edit.toPlainText(),
                self._negative_edit.toPlainText(),
            )

    def _on_save_preset_overwrite(self) -> None:
        if self._gaming_skin_active:
            self._loading_clock.play_special_key("save")
        idx = self._preset_combo.currentIndex()
        if idx > 0:
            name = self._preset_combo.currentText()
            self.preset_overwrite_requested.emit(
                name,
                self._positive_edit.toPlainText(),
                self._negative_edit.toPlainText(),
            )
        else:
            self._on_save_preset()

    def _on_delete_preset(self) -> None:
        idx = self._preset_combo.currentIndex()
        if idx <= 0:
            return
        name = self._preset_combo.currentText()
        reply = QMessageBox.question(
            self,
            "プリセットを削除",
            f"プリセット「{name}」を削除しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.preset_delete_requested.emit(name)

    def _on_preset_selected(self, index: int) -> None:
        if index <= 0:
            return
        name = self._preset_combo.currentText()
        self.preset_load_requested.emit(name)

    def update_preset_list(self, presets: list[dict], current_name: str = "") -> None:
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem("(プリセットを選択)")
        for p in presets:
            self._preset_combo.addItem(p.get("name", ""))
        if current_name:
            idx = self._preset_combo.findText(current_name)
            if idx >= 0:
                self._preset_combo.setCurrentIndex(idx)
        self._preset_combo.blockSignals(False)
        self._preset_delete_btn.setEnabled(self._preset_combo.currentIndex() > 0)

    def set_prompts_without_signal(self, positive: str, negative: str) -> None:
        self._positive_edit.blockSignals(True)
        self._negative_edit.blockSignals(True)
        self._positive_edit.setPlainText(positive)
        self._negative_edit.setPlainText(negative)
        self._positive_edit.blockSignals(False)
        self._negative_edit.blockSignals(False)

    def _toggle_gaming_skin(self, checked: bool) -> None:
        self._gaming_skin_active = checked
        if checked:
            self._gaming_timer.start(50)
            self._gaming_stats_stop.clear()
            self._gaming_stats_thread = threading.Thread(target=self._gaming_stats_loop, daemon=True)
            self._gaming_stats_thread.start()
            self._gradient_timer.start(50)
            self._apply_gaming_style()
        else:
            self._gaming_timer.stop()
            self._gaming_stats_stop.set()
            self._gaming_stats_thread = None
            self._gradient_timer.stop()
            self._setup_style()
            self._gen_once_btn.setStyleSheet("")
            self._gen_n_btn.setStyleSheet("")
            self._gen_inf_btn.setStyleSheet("")
            self._cancel_btn.setStyleSheet("")
            self._preset_save_btn.setStyleSheet("padding: 4px 12px; font-size: 12px;")
            self._preset_delete_btn.setStyleSheet("padding: 4px 12px; font-size: 12px;")
            self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        if hasattr(self, "_gaming_skin_cb") and self._gaming_skin_cb:
            self._gaming_skin_cb(checked)

    def _apply_gaming_style(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #0a0a1a;
                color: #0ff;
                font-family: "Impact", "Arial Black", sans-serif;
                font-size: 14px;
            }
            QMenuBar {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff0080, stop:0.3 #8000ff, stop:0.6 #00ffff, stop:1 #00ff80);
                color: #000;
                font-weight: bold;
                font-size: 13px;
            }
            QMenuBar::item:selected {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ffff00, stop:1 #ff00ff);
            }
            QMenu {
                background: #1a0030;
                color: #ff0;
                border: 2px solid #ff00ff;
                font-weight: bold;
            }
            QMenu::item:selected {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff0080, stop:1 #8000ff);
                color: #fff;
            }
            QStatusBar {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #8000ff, stop:1 #ff0080);
                color: #0ff;
                font-weight: bold;
                font-size: 14px;
            }
            QPlainTextEdit, QLineEdit, QSpinBox, QComboBox {
                background: #0a0020;
                color: #0ff;
                border: 2px solid #ff00ff;
                border-radius: 8px;
                padding: 4px 8px;
                font-weight: bold;
                selection-background-color: #8000ff;
            }
            QPlainTextEdit:focus, QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
                border: 2px solid #00ffff;
            }
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ff0080, stop:1 #8000ff);
                color: #fff;
                border: 2px solid #ff0;
                border-radius: 8px;
                padding: 8px 18px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ff40a0, stop:1 #a040ff);
                border: 2px solid #0ff;
            }
            QPushButton:disabled {
                background: #222;
                color: #555;
                border: 1px solid #444;
            }
            QPlainTextEdit:disabled { color: #333; background: #0a0a12; }
            QLineEdit:disabled { color: #333; background: #0a0a12; }
            QSpinBox:disabled { color: #333; background: #0a0a12; }
            QComboBox:disabled { color: #333; background: #0a0a12; }
            QCheckBox:disabled { color: #444; }
            QPushButton#generate_btn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #00ff00, stop:0.5 #ffff00, stop:1 #ff8800);
                color: #000;
                font-weight: bold;
                font-size: 18px;
                padding: 10px 30px;
                border: 3px solid #0ff;
                border-radius: 12px;
            }
            QPushButton#generate_btn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #44ff44, stop:0.5 #ffff88, stop:1 #ffaa44);
                border: 3px solid #ff0;
            }
            QPushButton#generate_btn:disabled {
                background: #333;
                color: #666;
                border: 2px solid #555;
            }
            QPushButton#cancel_btn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff0000, stop:1 #ff00ff);
                color: #fff;
                font-weight: bold;
                font-size: 16px;
                padding: 10px 30px;
                border: 3px solid #ff0;
                border-radius: 12px;
            }
            QPushButton#cancel_btn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff4444, stop:1 #ff44ff);
            }
            QGroupBox {
                border: 2px solid #ff00ff;
                border-radius: 10px;
                margin-top: 16px;
                padding-top: 20px;
                font-weight: bold;
                color: #ff0;
                font-size: 14px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 6px;
            }
            QProgressBar {
                background: #0a0020;
                border: 2px solid #ff00ff;
                border-radius: 8px;
                text-align: center;
                color: #0ff;
                font-weight: bold;
                min-height: 24px;
                font-size: 14px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #00ff00, stop:0.5 #ffff00, stop:1 #ff8800);
                border-radius: 6px;
            }
            QListWidget {
                background: #0a0020;
                border: 2px solid #ff00ff;
                border-radius: 8px;
                color: #0ff;
                font-weight: bold;
            }
            QListWidget::item {
                padding: 4px;
            }
            QListWidget::item:selected {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff0080, stop:1 #8000ff);
                color: #fff;
            }
            QSplitter::handle { background: #ff00ff; width: 4px; }
            QLabel#preview_label {
                background: #0a0020;
                border: 3px solid #00ffff;
                border-radius: 10px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                background: #ff00ff;
                border: 1px solid #8000ff;
            }
            QComboBox::drop-down {
                background: #ff00ff;
                border: 1px solid #8000ff;
            }
            QCheckBox::indicator {
                border: 2px solid #ff00ff;
                border-radius: 4px;
                background: #0a0020;
                width: 18px;
                height: 18px;
            }
            QCheckBox::indicator:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #00ffff, stop:1 #ff00ff);
            }
        """)
        self._gen_once_btn.setStyleSheet(
            "QPushButton { font-size: 16px; }")
        self._gen_n_btn.setStyleSheet(
            "QPushButton { font-size: 16px; }")
        self._gen_inf_btn.setStyleSheet(
            "QPushButton { font-size: 16px; }")
        self._cancel_btn.setStyleSheet(
            "QPushButton { font-size: 16px; }")
        self._preset_save_btn.setStyleSheet(
            "padding: 4px 14px; font-size: 13px; font-weight: bold;")
        self._preset_delete_btn.setStyleSheet(
            "padding: 4px 14px; font-size: 13px; font-weight: bold;")

    def _update_gradient_animation(self) -> None:
        if not self._gaming_skin_active:
            return
        speed = 0.03 if self._is_generating else 0.005
        self._gradient_offset = (self._gradient_offset + speed) % 1.0
        h1 = int(self._gradient_offset * 360) % 360
        h2 = (h1 + 120) % 360
        h3 = (h1 + 240) % 360
        self.menuBar().setStyleSheet(f"""
            QMenuBar {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 hsl({h1},255,200), stop:0.5 hsl({h2},255,200), stop:1 hsl({h3},255,200));
                color: #000;
                font-weight: bold;
                font-size: 13px;
            }}
            QMenuBar::item:selected {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ffff00, stop:1 #ff00ff);
            }}
        """)
        self._status_bar.setStyleSheet(f"""
            QStatusBar {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 hsl({h2},255,180), stop:0.5 hsl({h3},255,180), stop:1 hsl({h1},255,180));
                color: #000;
                font-weight: bold;
                font-size: 13px;
            }}
        """)

    def _update_gaming_animation(self) -> None:
        if not self._gaming_skin_active:
            return
        self._gaming_hue = (self._gaming_hue + 2) % 360
        with self._gaming_stats_lock:
            s = dict(self._gaming_stats)
        if self._is_generating:
            flash = "⚡🔥" if self._gaming_hue % 20 < 10 else "🔥⚡"
            title = f"{flash} 生成中!! {flash}  CPU:{s.get('cpu_pct','--')}%  GPU:{s.get('gpu_pct','--')}%  VRAM:{s.get('vram_pct','--')}%"
        else:
            title = f"⚡makeImg🌈  CPU:{s.get('cpu_pct','--')}%  RAM:{s.get('ram_pct','--')}%  GPU:{s.get('gpu_pct','--')}%  VRAM:{s.get('vram_pct','--')}%  {s.get('gpu_temp','--')}°C"
        self.setWindowTitle(title)

    def _gaming_stats_loop(self) -> None:
        import ctypes
        import shutil
        import subprocess
        while not self._gaming_stats_stop.is_set():
            info = {"cpu_pct": "--", "ram_pct": "--", "gpu_pct": "--", "vram_pct": "--", "gpu_temp": "--"}
            try:
                if os.name == "nt":
                    class _MEMSTAT(ctypes.Structure):
                        _fields_ = [
                            ("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                        ]
                    m = _MEMSTAT()
                    m.dwLength = ctypes.sizeof(_MEMSTAT)
                    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
                    info["ram_pct"] = f"{m.dwMemoryLoad}"
            except Exception:
                pass
            try:
                r = subprocess.run(
                    ["wmic", "cpu", "get", "loadpercentage", "/value"],
                    capture_output=True, text=True, timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                for line in r.stdout.strip().splitlines():
                    if "=" in line:
                        val = line.split("=", 1)[1].strip()
                        if val.isdigit():
                            info["cpu_pct"] = val
                            break
            except Exception:
                pass
            smi = shutil.which("nvidia-smi")
            if smi:
                try:
                    r = subprocess.run(
                        [smi,
                         "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                         "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=3,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    parts = r.stdout.strip().split(",")
                    if len(parts) >= 4:
                        info["gpu_pct"] = parts[0].strip()
                        try:
                            vram_pct = float(parts[1].strip()) / float(parts[2].strip()) * 100
                            info["vram_pct"] = f"{vram_pct:.0f}"
                        except (ValueError, ZeroDivisionError):
                            pass
                        info["gpu_temp"] = parts[3].strip()
                except Exception:
                    pass
            with self._gaming_stats_lock:
                self._gaming_stats = info
            self._gaming_stats_stop.wait(2.0)

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent
        if isinstance(event, QKeyEvent) and event.type() == QEvent.Type.KeyPress:
            if self._gaming_skin_active:
                text = event.text()
                if text == '{':
                    self._loading_clock.play_special_key("brace_open")
                elif text == '}':
                    self._loading_clock.play_special_key("brace_close")
                elif text == '|':
                    self._loading_clock.play_special_key("pipe")
                elif text == ',':
                    self._loading_clock.play_special_key("comma")
                elif text == ':':
                    self._loading_clock.play_special_key("colon")
                elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self._loading_clock.play_enter()
                elif not event.modifiers():
                    self._loading_clock.play_typekey()
        return super().eventFilter(obj, event)

    def closeEvent(self, event) -> None:
        self._gaming_stats_stop.set()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._loading_clock.setGeometry(self.rect())
        self._rescale_preview()
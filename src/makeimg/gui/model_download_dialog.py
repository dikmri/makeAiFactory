from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QUrl, Qt, QTimer
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class ModelDownloadDialog(QDialog):
    """モデルダウンロード用のブラウザダイアログ。

    内部ブラウザでダウンロードページを開き、JavaScriptでダウンロードボタンを自動クリック。
    ダウンロード完了時にファイルを所定の場所に移動する。
    """

    def __init__(
        self,
        model_name: str,
        download_page_url: str,
        target_path: Path,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._model_name = model_name
        self._download_page_url = download_page_url
        self._target_path = target_path
        self._download_dir = target_path.parent
        self._downloaded_file: Path | None = None
        self._auto_click_attempted = False
        self._login_detected = False

        self.setWindowTitle(f"モデルダウンロード: {model_name}")
        self.setMinimumSize(900, 700)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._status_label = QLabel(
            f"モデル: {self._model_name}\n"
            f"保存先: {self._target_path}\n\n"
            "ダウンロードページを読み込んでいます..."
        )
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(self._status_label)

        self._browser = QWebEngineView()
        self._browser.page().profile().downloadRequested.connect(self._on_download_requested)
        self._browser.page().loadFinished.connect(self._on_page_loaded)
        self._browser.page().urlChanged.connect(self._on_url_changed)
        self._browser.setUrl(QUrl(self._download_page_url))
        layout.addWidget(self._browser, 1)

        btn_layout = QHBoxLayout()

        self._retry_btn = QPushButton("再試行")
        self._retry_btn.clicked.connect(self._retry_download)
        self._retry_btn.setVisible(False)
        btn_layout.addWidget(self._retry_btn)

        btn_layout.addStretch()

        self._done_btn = QPushButton("閉じる")
        self._done_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self._done_btn)

        layout.addLayout(btn_layout)

    def _on_url_changed(self, url: QUrl) -> None:
        """URL変更を検出してログインページかどうかを判定"""
        url_str = url.toString()
        if "login" in url_str.lower() or "signin" in url_str.lower():
            self._login_detected = True
            self._status_label.setText(
                f"モデル: {self._model_name}\n"
                f"保存先: {self._target_path}\n\n"
                "ログインが必要です。ブラウザ内でログインしてください。\n"
                "ログイン後、ダウンロードが自動的に開始されます。"
            )
            self._status_label.setStyleSheet("color: #ffb74d; font-size: 12px;")

    def _on_page_loaded(self, success: bool) -> None:
        """ページ読み込み完了時にダウンロードボタンを自動クリック"""
        if not success:
            self._status_label.setText(
                f"モデル: {self._model_name}\n\n"
                "ページの読み込みに失敗しました。"
            )
            self._status_label.setStyleSheet("color: #e57373; font-size: 12px;")
            self._retry_btn.setVisible(True)
            return

        if self._login_detected:
            # ログインページの場合は自動クリックしない
            return

        if self._auto_click_attempted:
            return

        self._auto_click_attempted = True

        # JavaScriptでダウンロードボタンを自動クリック
        js_script = """
        (function() {
            // ダウンロードボタンを探す（複数のセレクターを試す）
            const selectors = [
                'a[href*="download"]',
                'button:contains("Download")',
                '[data-slot="button"]',
                '.download-button',
                'a:has-text("Download")'
            ];
            
            for (const selector of selectors) {
                try {
                    const elements = document.querySelectorAll(selector);
                    for (const el of elements) {
                        const text = (el.textContent || '').toLowerCase();
                        const href = el.href || '';
                        if (text.includes('download') || href.includes('download')) {
                            el.click();
                            return 'clicked: ' + selector;
                        }
                    }
                } catch (e) {}
            }
            
            // fallback: すべてのリンクとボタンをチェック
            const allElements = document.querySelectorAll('a, button, [role="button"]');
            for (const el of allElements) {
                const text = (el.textContent || '').toLowerCase();
                const href = el.href || '';
                if (text.includes('download') || href.includes('download')) {
                    el.click();
                    return 'clicked: fallback';
                }
            }
            
            return 'not_found';
        })();
        """

        self._browser.page().runJavaScript(js_script, self._on_js_result)

    def _on_js_result(self, result) -> None:
        """JavaScript実行結果の処理"""
        if result == 'clicked':
            self._status_label.setText(
                f"モデル: {self._model_name}\n"
                f"保存先: {self._target_path}\n\n"
                "ダウンロードを開始しました。完了までお待ちください..."
            )
            self._status_label.setStyleSheet("color: #81c784; font-size: 12px;")
        else:
            self._status_label.setText(
                f"モデル: {self._model_name}\n\n"
                "ダウンロードボタンが見つかりませんでした。\n"
                "手動でダウンロードボタンをクリックしてください。"
            )
            self._status_label.setStyleSheet("color: #ffb74d; font-size: 12px;")
            self._retry_btn.setVisible(True)

    def _on_download_requested(self, download: QWebEngineDownloadRequest) -> None:
        """ダウンロード要求時に保存先を設定"""
        self._download_dir.mkdir(parents=True, exist_ok=True)

        filename = download.downloadFileName()
        save_path = self._download_dir / filename
        download.setDownloadDirectory(str(self._download_dir))
        download.setDownloadFileName(filename)
        download.accept()

        self._downloaded_file = save_path
        download.finished.connect(lambda: self._on_download_finished(save_path))

        self._status_label.setText(
            f"モデル: {self._model_name}\n"
            f"保存先: {self._target_path}\n\n"
            "ダウンロード中... 完了までお待ちください。"
        )
        self._status_label.setStyleSheet("color: #64b5f6; font-size: 12px;")

    def _on_download_finished(self, file_path: Path) -> None:
        """ダウンロード完了時に所定の場所に移動"""
        if not file_path.exists():
            self._status_label.setText(
                f"モデル: {self._model_name}\n\n"
                "ダウンロードに失敗しました。"
            )
            self._status_label.setStyleSheet("color: #e57373; font-size: 12px;")
            return

        self._target_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.move(str(file_path), str(self._target_path))
            logger.info("モデル移動完了: %s → %s", file_path, self._target_path)
            self._status_label.setText(
                f"モデル: {self._model_name}\n"
                f"保存先: {self._target_path}\n\n"
                "✓ ダウンロード完了！"
            )
            self._status_label.setStyleSheet("color: #81c784; font-size: 12px;")
            QMessageBox.information(
                self,
                "ダウンロード完了",
                f"モデル「{self._model_name}」を保存しました:\n{self._target_path}",
            )
        except Exception as e:
            logger.error("モデル移動失敗: %s", e)
            self._status_label.setText(
                f"モデル: {self._model_name}\n\n"
                f"移動に失敗しました: {e}\n\n"
                f"手動で移動してください:\n"
                f"ダウンロード: {file_path}\n"
                f"保存先: {self._target_path}"
            )
            self._status_label.setStyleSheet("color: #e57373; font-size: 12px;")

    def _retry_download(self) -> None:
        """再試行"""
        self._auto_click_attempted = False
        self._login_detected = False
        self._browser.setUrl(QUrl(self._download_page_url))
        self._retry_btn.setVisible(False)
        self._status_label.setText(
            f"モデル: {self._model_name}\n"
            f"保存先: {self._target_path}\n\n"
            "ダウンロードページを再読み込みしています..."
        )
        self._status_label.setStyleSheet("color: #aaa; font-size: 12px;")

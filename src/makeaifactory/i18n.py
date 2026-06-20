"""簡易多言語対応。

日本語の原文をキーとする辞書ベースの仕組み。tr(原文) で現在の言語に変換する
(未登録または日本語設定時は原文をそのまま返す)。翻訳データは app/i18n/*.json
にあり、tools/sync_i18n.py が新しい原文を機械翻訳で自動的に追記する。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("ja", "en", "zh", "ko")
LANGUAGE_LABELS = {
    "ja": "日本語",
    "en": "English",
    "zh": "中文(简体)",
    "ko": "한국어",
}

_current_lang = "ja"
_cache: dict[str, dict[str, str]] = {}


def i18n_dir() -> Path:
    from .core.paths import _app_root
    return _app_root() / "app" / "i18n"


def set_language(lang: str) -> None:
    global _current_lang
    _current_lang = lang if lang in SUPPORTED_LANGUAGES else "ja"


def get_language() -> str:
    return _current_lang


def _load(lang: str) -> dict[str, str]:
    if lang not in _cache:
        table: dict[str, str] = {}
        path = i18n_dir() / f"{lang}.json"
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    table = json.load(f)
            except Exception as e:
                logger.warning("翻訳ファイル読み込み失敗 (%s): %s", lang, e)
        _cache[lang] = table
    return _cache[lang]


def tr(text: str) -> str:
    """日本語の原文を現在の言語に翻訳する。未登録または日本語設定時は原文を返す。"""
    if _current_lang == "ja" or not text:
        return text
    return _load(_current_lang).get(text, text)


def tr_elapsed(seconds: float) -> str:
    """経過時間を「X分Y秒」のように現在の言語で整形する。"""
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    if mins > 0:
        return tr("{mins}分{secs}秒").format(mins=mins, secs=secs)
    return tr("{secs}秒").format(secs=secs)


def detect_system_language() -> str:
    try:
        from PySide6.QtCore import QLocale
        code = QLocale.system().name().split("_")[0]
    except Exception:
        return "ja"
    return code if code in SUPPORTED_LANGUAGES else "ja"

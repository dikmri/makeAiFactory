import json
import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory import i18n


@pytest.fixture(autouse=True)
def _reset_i18n_state():
    """各テスト後に言語設定とキャッシュを初期状態に戻す。"""
    yield
    i18n.set_language("ja")
    i18n._cache.clear()


def test_tr_returns_original_when_japanese():
    i18n.set_language("ja")
    assert i18n.tr("こんにちは") == "こんにちは"


def test_tr_returns_original_when_unsupported_language():
    i18n.set_language("xx")
    assert i18n.get_language() == "ja"


def test_set_language_only_accepts_supported_codes():
    i18n.set_language("en")
    assert i18n.get_language() == "en"
    i18n.set_language("not-a-real-lang")
    assert i18n.get_language() == "ja"


def test_tr_falls_back_to_original_when_translation_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(i18n, "i18n_dir", lambda: tmp_path)
    i18n._cache.clear()
    i18n.set_language("en")
    assert i18n.tr("未登録のテキスト") == "未登録のテキスト"


def test_tr_uses_translation_table_when_present(monkeypatch, tmp_path):
    (tmp_path / "en.json").write_text(
        json.dumps({"こんにちは": "Hello"}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(i18n, "i18n_dir", lambda: tmp_path)
    i18n._cache.clear()
    i18n.set_language("en")
    assert i18n.tr("こんにちは") == "Hello"


def test_tr_empty_string_returns_empty():
    i18n.set_language("en")
    assert i18n.tr("") == ""


def test_tr_elapsed_minutes_and_seconds():
    i18n.set_language("ja")
    assert i18n.tr_elapsed(75) == "1分15秒"
    assert i18n.tr_elapsed(45) == "45秒"


def test_detect_system_language_returns_supported_code():
    lang = i18n.detect_system_language()
    assert lang in i18n.SUPPORTED_LANGUAGES

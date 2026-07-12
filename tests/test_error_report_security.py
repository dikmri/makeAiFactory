"""P0 PR-5: 危険な配布経路の停止 (ERR-01 + UPD-01) の回帰テスト。

対象:
- tools/fetch_error_reports.py の _is_valid_report_id
  (report_id をローカルディレクトリ名として使う際のパストラバーサル対策)
- src/makeaifactory/core/error_reporter.py の mask_sensitive / _truncate_detail
  (送信直前マスキング・detailの容量上限)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from fetch_error_reports import _is_valid_report_id  # noqa: E402

from makeaifactory.core.error_reporter import (  # noqa: E402
    _MAX_DETAIL_BYTES,
    _truncate_detail,
    mask_sensitive,
)


# ── _is_valid_report_id ──────────────────────────────────────────────────────

def test_valid_report_id_accepts_dashed_uuid():
    assert _is_valid_report_id("550e8400-e29b-41d4-a716-446655440000")


def test_valid_report_id_accepts_hex_uuid_without_dashes():
    assert _is_valid_report_id("550e8400e29b41d4a716446655440000")


def test_valid_report_id_accepts_snowflake_17digits():
    assert _is_valid_report_id("12345678901234567")


def test_valid_report_id_accepts_snowflake_20digits():
    assert _is_valid_report_id("12345678901234567890")


def test_valid_report_id_rejects_unix_path_traversal():
    assert not _is_valid_report_id("../x")


def test_valid_report_id_rejects_windows_path_traversal():
    assert not _is_valid_report_id("..\\x")


def test_valid_report_id_rejects_empty_string():
    assert not _is_valid_report_id("")


def test_valid_report_id_rejects_value_with_slash():
    assert not _is_valid_report_id("abc/def")


def test_valid_report_id_rejects_value_with_backslash():
    assert not _is_valid_report_id("abc\\def")


def test_valid_report_id_rejects_alpha_mixed_snowflake_like():
    assert not _is_valid_report_id("1234567890123456x")


def test_valid_report_id_rejects_too_short_numeric():
    assert not _is_valid_report_id("12345")


def test_valid_report_id_rejects_non_string():
    assert not _is_valid_report_id(None)  # type: ignore[arg-type]


# ── mask_sensitive ───────────────────────────────────────────────────────────

def test_mask_sensitive_masks_username_in_home_path():
    text = r"C:\Users\Alice\AppData\Local\app.log"
    result = mask_sensitive(text)
    assert "Alice" not in result
    assert r"C:\Users\<USER_REDACTED>\AppData" in result


def test_mask_sensitive_masks_unc_share_path():
    text = r"共有フォルダ \\host\share\secret.txt を参照"
    result = mask_sensitive(text)
    assert r"\\host\share" not in result
    assert "<UNC_PATH_REDACTED>" in result


def test_mask_sensitive_masks_credentials_in_url():
    text = "https://user:pass@h/resource"
    result = mask_sensitive(text)
    assert "user:pass" not in result
    assert "<CREDENTIALS_REDACTED>" in result


def test_mask_sensitive_masks_webhook_like_url():
    text = "webhook: https://discord.com/api/webhooks/123456789012345678/abcDEF-_token"
    result = mask_sensitive(text)
    assert "abcDEF-_token" not in result
    assert "<WEBHOOK_URL_REDACTED>" in result


def test_mask_sensitive_leaves_normal_text_unchanged():
    text = "通常のエラーメッセージです"
    assert mask_sensitive(text) == text


def test_mask_sensitive_handles_empty_string():
    assert mask_sensitive("") == ""


# ── detail truncate ──────────────────────────────────────────────────────────

def test_truncate_detail_leaves_short_text_unchanged():
    text = "短いエラー詳細\nTraceback (most recent call last):\n  ..."
    assert _truncate_detail(text) == text


def test_truncate_detail_truncates_when_over_limit():
    text = "a" * (_MAX_DETAIL_BYTES + 100)
    result = _truncate_detail(text)
    result_bytes = len(result.encode("utf-8"))
    assert result_bytes < len(text.encode("utf-8"))
    assert result_bytes <= _MAX_DETAIL_BYTES + 100  # 省略表示の付加分を許容
    assert "省略" in result


def test_truncate_detail_handles_empty_string():
    assert _truncate_detail("") == ""

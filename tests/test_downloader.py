"""downloader.py の純関数と download_file() の resume/検証ロジックのテスト。

HTTP実接続は行わない。純関数(_parse_content_range_start, _resume_plan)は
直接呼び出して検証し、download_file() 本体は httpx.MockTransport で
ネットワークを介さずに応答を偽装して検証する。
"""
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx

from makeaifactory.runtime import downloader
from makeaifactory.runtime.downloader import (
    _parse_content_range_start,
    _resume_plan,
    download_file,
)
from makeaifactory.domain.errors import HashMismatchError


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# _parse_content_range_start
# ---------------------------------------------------------------------------

def test_parse_content_range_start_normal():
    assert _parse_content_range_start("bytes 100-999/1000") == 100


def test_parse_content_range_start_zero():
    assert _parse_content_range_start("bytes 0-9/10") == 0


def test_parse_content_range_start_unsatisfied_range():
    # 416応答で付く「bytes */total」形式は開始位置を特定できない
    assert _parse_content_range_start("bytes */1000") is None


def test_parse_content_range_start_none():
    assert _parse_content_range_start(None) is None


def test_parse_content_range_start_empty():
    assert _parse_content_range_start("") is None


def test_parse_content_range_start_malformed():
    assert _parse_content_range_start("not-a-valid-header") is None


def test_parse_content_range_start_wrong_unit():
    assert _parse_content_range_start("items 1-2/3") is None


# ---------------------------------------------------------------------------
# _resume_plan
# ---------------------------------------------------------------------------

def test_resume_plan_200_writes_from_scratch():
    mode, restart = _resume_plan(200, existing_size=0, content_range=None)
    assert mode == "wb"
    assert restart is True


def test_resume_plan_200_with_existing_size_still_writes_from_scratch():
    # 200はサーバがRangeを無視して全体を返した場合。既存partは信用せず上書き。
    mode, restart = _resume_plan(200, existing_size=123, content_range=None)
    assert mode == "wb"
    assert restart is True


def test_resume_plan_206_matching_offset_appends():
    mode, restart = _resume_plan(206, existing_size=100, content_range="bytes 100-999/1000")
    assert mode == "ab"
    assert restart is False


def test_resume_plan_206_mismatched_offset_restarts_from_zero():
    # サーバが要求と異なる位置から返した場合は既存partを信用せず破棄して取り直す
    mode, restart = _resume_plan(206, existing_size=100, content_range="bytes 500-999/1000")
    assert mode == "restart"
    assert restart is True


def test_resume_plan_206_missing_content_range_restarts_from_zero():
    mode, restart = _resume_plan(206, existing_size=100, content_range=None)
    assert mode == "restart"
    assert restart is True


def test_resume_plan_416_requests_verify_only():
    # 416: 書き込みは行わず、既存partをそのまま検証する分岐であることを表現
    mode, restart = _resume_plan(416, existing_size=1000, content_range="bytes */1000")
    assert mode == "verify"
    assert restart is True


# ---------------------------------------------------------------------------
# download_file(): httpx.MockTransport でネットワークを偽装した統合的な確認
# ---------------------------------------------------------------------------

def _patch_async_client(monkeypatch, handler):
    """downloader内で使われる httpx.AsyncClient を MockTransport 経由に差し替える。

    実ネットワーク接続は一切発生しない。
    """
    original_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(downloader.httpx, "AsyncClient", _factory)


async def test_download_file_hash_mismatch_removes_part(monkeypatch, tmp_path):
    """検証失敗(sha256不一致)時、壊れた.partファイルを残さないこと。"""
    body = b"correct content from server"
    call_count = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, content=body, headers={"content-length": str(len(body))})

    _patch_async_client(monkeypatch, handler)

    dest = tmp_path / "model.bin"
    part = tmp_path / "model.bin.part"
    wrong_sha256 = "0" * 64

    with pytest.raises(HashMismatchError):
        await download_file("http://example.invalid/model.bin", dest, sha256=wrong_sha256)

    assert call_count["n"] == 1
    assert not part.exists(), "hash不一致後に壊れた.partが残っている"
    assert not dest.exists()


async def test_download_file_416_verifies_existing_part_without_refetch(monkeypatch, tmp_path):
    """416応答時、既存.partをtruncateせず検証だけして完了させること。"""
    body = b"already fully downloaded content"
    expected_sha256 = _sha256(body)
    call_count = {"n": 0}

    dest = tmp_path / "model.bin"
    part = tmp_path / "model.bin.part"
    part.write_bytes(body)

    async def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        # 既存partが完全体のため、レジューム要求(Range指定あり)に対し416を返す
        assert request.headers.get("range") == f"bytes={len(body)}-"
        return httpx.Response(
            416,
            content=b"Requested Range Not Satisfiable",
            headers={"content-range": f"bytes */{len(body)}"},
        )

    _patch_async_client(monkeypatch, handler)

    result = await download_file("http://example.invalid/model.bin", dest, sha256=expected_sha256)

    assert result == dest
    assert call_count["n"] == 1, "416検証成功時は再取得のリクエストを送ってはいけない"
    assert not part.exists()
    assert dest.read_bytes() == body


async def test_download_file_416_with_corrupt_part_refetches_from_zero(monkeypatch, tmp_path):
    """416だが既存.partがsha256と一致しない場合、破棄して0から再取得すること。"""
    real_body = b"the real full content"
    corrupt_existing = b"XXXXX"  # 壊れた既存part(サイズも内容も異なる)
    expected_sha256 = _sha256(real_body)
    calls = []

    dest = tmp_path / "model.bin"
    part = tmp_path / "model.bin.part"
    part.write_bytes(corrupt_existing)

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("range"))
        if len(calls) == 1:
            # 1回目: レジューム要求 → 416(既存partが範囲外)
            return httpx.Response(
                416,
                content=b"Requested Range Not Satisfiable",
                headers={"content-range": f"bytes */{len(corrupt_existing)}"},
            )
        # 2回目: Rangeヘッダ無しで0から再取得
        return httpx.Response(200, content=real_body, headers={"content-length": str(len(real_body))})

    _patch_async_client(monkeypatch, handler)

    result = await download_file("http://example.invalid/model.bin", dest, sha256=expected_sha256)

    assert result == dest
    assert len(calls) == 2
    assert calls[0] == f"bytes={len(corrupt_existing)}-"
    assert calls[1] is None, "2回目はRangeヘッダ無しで再取得すること"
    assert dest.read_bytes() == real_body


async def test_download_file_206_matching_offset_resumes(monkeypatch, tmp_path):
    """206でContent-Rangeの開始位置が既存サイズと一致する場合、追記継続すること。"""
    first_part = b"HELLO"
    remainder = b"WORLD"
    full_content = first_part + remainder
    expected_sha256 = _sha256(full_content)

    dest = tmp_path / "model.bin"
    part = tmp_path / "model.bin.part"
    part.write_bytes(first_part)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("range") == f"bytes={len(first_part)}-"
        return httpx.Response(
            206,
            content=remainder,
            headers={
                "content-range": f"bytes {len(first_part)}-{len(full_content) - 1}/{len(full_content)}",
                "content-length": str(len(remainder)),
            },
        )

    _patch_async_client(monkeypatch, handler)

    result = await download_file("http://example.invalid/model.bin", dest, sha256=expected_sha256)

    assert result == dest
    assert dest.read_bytes() == full_content


async def test_download_file_206_mismatched_offset_restarts_from_zero(monkeypatch, tmp_path):
    """206でContent-Rangeの開始位置が既存サイズと不一致なら、既存partを破棄し、
    Rangeヘッダ無しで0から取り直すこと。"""
    stale_existing = b"HELLO"  # 既存.part(古い/信用できない断片)
    full_content = b"COMPLETELY_DIFFERENT_FULL_BODY"
    expected_sha256 = _sha256(full_content)

    dest = tmp_path / "model.bin"
    part = tmp_path / "model.bin.part"
    part.write_bytes(stale_existing)

    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("range"))
        if len(calls) == 1:
            # 1回目: レジューム要求に対し、要求(bytes=5-)と異なるオフセットの
            # 断片を206で返す(信用できない応答)
            return httpx.Response(
                206,
                content=b"partial-from-wrong-offset",
                headers={
                    "content-range": f"bytes 500-999/{len(full_content)}",
                    "content-length": "25",
                },
            )
        # 2回目: Rangeヘッダ無しで0から全体取得
        return httpx.Response(
            200, content=full_content, headers={"content-length": str(len(full_content))}
        )

    _patch_async_client(monkeypatch, handler)

    result = await download_file("http://example.invalid/model.bin", dest, sha256=expected_sha256)

    assert result == dest
    assert len(calls) == 2
    assert calls[0] == f"bytes={len(stale_existing)}-"
    assert calls[1] is None, "2回目はRangeヘッダ無しで0から取り直すこと"
    # 誤オフセットの断片が混ざらず、2回目の全体取得のみが保存されていること
    assert dest.read_bytes() == full_content

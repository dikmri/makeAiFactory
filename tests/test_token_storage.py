"""DAT-01: Discord Bot トークンの保管方式(DPAPI暗号化)の単体テスト。

- core.dpapi: Windows DPAPI(CryptProtectData/CryptUnprotectData)ラッパー
  そのものの往復・改ざん検知・空文字の扱い。
- SettingsStore.discord_token / set_discord_token: settings.json に平文が
  残らないこと、旧バージョン(平文保存)からの一度きりの移行、暗号化データが
  壊れている場合のfail-safe(例外を出さず空文字扱い)を検証する。

Windows実環境 (DPAPIが利用可能な環境) での実行を前提とする。
"""
import json
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.core import dpapi
from makeaifactory.core.settings_store import SettingsStore


# ── core.dpapi 単体 ──────────────────────────────────────────────────────────

def test_dpapi_roundtrip():
    plain = "super-secret-discord-bot-token.abcDEF-123"
    enc = dpapi.encrypt_to_b64(plain)
    assert dpapi.decrypt_from_b64(enc) == plain


def test_dpapi_roundtrip_unicode():
    plain = "トークン😀テスト"
    enc = dpapi.encrypt_to_b64(plain)
    assert dpapi.decrypt_from_b64(enc) == plain


def test_dpapi_empty_string_roundtrip():
    enc = dpapi.encrypt_to_b64("")
    assert dpapi.decrypt_from_b64(enc) == ""


def test_dpapi_tampered_blob_raises():
    enc = dpapi.encrypt_to_b64("some-token-value")
    # base64として妥当なまま(長さを変えず)、中身の数文字だけ書き換える。
    # DPAPI blobは改ざん検知されるため、CryptUnprotectDataが失敗しOSErrorになる。
    chars = list(enc)
    mid = len(chars) // 2
    chars[mid] = "A" if chars[mid] != "A" else "B"
    chars[mid + 1] = "Z" if chars[mid + 1] != "Z" else "Y"
    tampered = "".join(chars)
    assert tampered != enc

    try:
        dpapi.decrypt_from_b64(tampered)
        assert False, "改ざんされたblobの復号が例外を出さずに成功してしまった"
    except Exception:
        pass


def test_dpapi_invalid_base64_raises():
    try:
        dpapi.decrypt_from_b64("これは base64 ではない !!!")
        assert False, "不正なBase64が例外を出さずに通ってしまった"
    except Exception:
        pass


# ── SettingsStore.discord_token / set_discord_token ─────────────────────────

def test_set_discord_token_not_stored_as_plaintext():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "settings.json"
        settings = SettingsStore(path)

        token = "plaintext-must-not-appear-in-file"
        settings.set_discord_token(token)

        raw = path.read_text(encoding="utf-8")
        assert token not in raw

        # getter経由では復号され元の値が返ること
        assert settings.discord_token == token

        # 再読み込みしたインスタンスからも復元できること(永続化されている)
        reloaded = SettingsStore(path)
        assert reloaded.discord_token == token


def test_legacy_plaintext_migrates_to_encrypted_on_read():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "settings.json"
        # 旧バージョンが書いたであろう平文settings.jsonを直接用意する
        # (discord_token_enc キーはまだ存在しない状態を再現)。
        legacy_token = "legacy-plaintext-token-999"
        path.write_text(
            json.dumps({"discord_token": legacy_token}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        settings = SettingsStore(path)
        value = settings.discord_token

        assert value == legacy_token

        # 読み出し(1回目のgetter呼び出し)で暗号化形式へ移行し、
        # 平文キーは空になっていること。
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw.get("discord_token") == ""
        assert raw.get("discord_token_enc")
        assert legacy_token not in path.read_text(encoding="utf-8")

        # 移行後も同じ値を返し続けること(暗号化パス経由で読めること)
        assert settings.discord_token == legacy_token
        reloaded = SettingsStore(path)
        assert reloaded.discord_token == legacy_token


def test_corrupted_encrypted_token_returns_empty_without_raising():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "settings.json"
        path.write_text(
            json.dumps({"discord_token_enc": "not-a-real-dpapi-blob=="}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        settings = SettingsStore(path)

        # 例外を出さず、未設定(空文字)として扱われること
        assert settings.discord_token == ""


def test_set_discord_token_empty_clears_both_keys():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "settings.json"
        settings = SettingsStore(path)
        settings.set_discord_token("some-token")
        assert settings.discord_token == "some-token"

        settings.set_discord_token("")

        assert settings.discord_token == ""
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw.get("discord_token") == ""
        assert raw.get("discord_token_enc") == ""

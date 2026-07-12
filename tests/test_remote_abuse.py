import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.remote_room.auth import (
    AuthManager,
    _MAX_PIN_FAILURES,
    _PIN_LOCK_SECONDS,
)
from makeaifactory.remote_room.controller import _generate_pin
from makeaifactory.remote_room import upload_validator
from makeaifactory.remote_room.upload_validator import _validate_and_reencode


# ── PIN 失敗ロック ────────────────────────────────────────────────────────────

def _auth() -> AuthManager:
    return AuthManager(pin="123456", require_pin=True, ttl_seconds=600)


def test_pin_lock_after_max_failures():
    a = _auth()
    ip = "ipA"
    for _ in range(_MAX_PIN_FAILURES):
        assert not a.is_pin_locked(ip, now=0.0)
        a.record_pin_failure(ip, now=0.0)
    assert a.is_pin_locked(ip, now=0.0)


def test_pin_lock_is_per_ip():
    a = _auth()
    for _ in range(_MAX_PIN_FAILURES):
        a.record_pin_failure("ipA", now=0.0)
    assert a.is_pin_locked("ipA", now=0.0)
    assert not a.is_pin_locked("ipB", now=0.0)


def test_pin_lock_expires_after_window():
    a = _auth()
    for _ in range(_MAX_PIN_FAILURES):
        a.record_pin_failure("ipA", now=0.0)
    assert a.is_pin_locked("ipA", now=0.0)
    # ロック期間経過後は解除される
    assert not a.is_pin_locked("ipA", now=_PIN_LOCK_SECONDS + 1)


def test_pin_reset_clears_lock():
    a = _auth()
    for _ in range(_MAX_PIN_FAILURES):
        a.record_pin_failure("ipA", now=0.0)
    assert a.is_pin_locked("ipA", now=0.0)
    a.reset_pin_failures("ipA")
    assert not a.is_pin_locked("ipA", now=0.0)


def test_old_failures_outside_window_do_not_count():
    a = _auth()
    # 古い失敗(ウィンドウ外)は数に入らないためロックされない
    for i in range(_MAX_PIN_FAILURES - 1):
        a.record_pin_failure("ipA", now=float(i))
    a.record_pin_failure("ipA", now=_PIN_LOCK_SECONDS + 100)  # 十分後の1回
    assert not a.is_pin_locked("ipA", now=_PIN_LOCK_SECONDS + 100)


# ── PIN 生成 ─────────────────────────────────────────────────────────────────

def test_generate_pin_is_six_digits():
    for _ in range(50):
        pin = _generate_pin()
        assert len(pin) == 6 and pin.isdigit()


# ── 画像検証 ─────────────────────────────────────────────────────────────────

def _png_bytes(w: int, h: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_valid_small_image_ok():
    data = _png_bytes(64, 64)
    out, err = _validate_and_reencode(data, "a.png", 20, 4096, ("png", "jpg", "jpeg", "webp"))
    assert err == "" and out and out[:8] == b"\x89PNG\r\n\x1a\n"


def test_invalid_extension_rejected():
    data = _png_bytes(64, 64)
    out, err = _validate_and_reencode(data, "a.gif", 20, 4096, ("png", "jpg"))
    assert err == "INVALID_FILE_TYPE" and out == b""


def test_non_image_rejected():
    out, err = _validate_and_reencode(b"not an image", "a.png", 20, 4096, ("png",))
    assert err == "INVALID_FILE_TYPE" and out == b""


def test_oversized_rejected_before_decode(monkeypatch):
    # デコード前の絶対上限を低く差し替え、寸法だけで拒否されることを確認する
    monkeypatch.setattr(upload_validator, "_MAX_DECODE_PIXELS", 100)
    data = _png_bytes(50, 50)  # 2500px > 100px
    out, err = _validate_and_reencode(data, "a.png", 20, 4096, ("png",))
    assert err == "IMAGE_TOO_LARGE" and out == b""


def test_large_within_cap_is_downscaled():
    # 25MPは超えないが max_px を超える画像は縮小して受け入れる
    data = _png_bytes(6000, 100)
    out, err = _validate_and_reencode(data, "a.png", 50, 4096, ("png",))
    assert err == "" and out
    from PIL import Image
    w, h = Image.open(io.BytesIO(out)).size
    assert w <= 4096

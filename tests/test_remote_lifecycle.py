"""RLC-01: Remote/Local Bridge ライフサイクルの残り修正 の単体テスト。

GUI/aiohttp実サーバ/実cloudflaredは使わず、切り出した純関数・小さな単体を検証する。

- TTL要否判定 (ttl_watcher_needed): room_ttl_minutes<=0 は無期限 (=TTL監視タスクを作らない)。
- build_qr_url: QRに載せるURLの組み立て (PIN埋め込みON/OFF)。
- AuthManager のセッション上限 (evict-oldest)。
- Cookie の secure/max_age 判定 (tunnel_enabled / room_ttl_minutes<=0)。
- TunnelManager: URL検出失敗時に自プロセスをself-cleanすること。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.remote_room.auth import AuthManager, _MAX_SESSIONS
from makeaifactory.remote_room.controller import ttl_watcher_needed
from makeaifactory.remote_room.room_config import build_qr_url
from makeaifactory.remote_room.room_server import cookie_max_age, cookie_secure
from makeaifactory.remote_room.tunnel_manager import TunnelManager


# ── TTL要否判定 (RLC-01 #1) ──────────────────────────────────────────────────

def test_ttl_watcher_not_needed_when_unlimited():
    # room_ttl_minutes<=0 は「無期限」。ローカルブリッジ常駐用途でTTL監視タスク自体を作らない。
    assert ttl_watcher_needed(0) is False
    assert ttl_watcher_needed(-1) is False


def test_ttl_watcher_needed_when_positive():
    assert ttl_watcher_needed(180) is True
    assert ttl_watcher_needed(1) is True


# ── build_qr_url (RLC-01 #5) ─────────────────────────────────────────────────

def test_build_qr_url_includes_pin_by_default():
    url = build_qr_url("https://example.trycloudflare.com", "123456", include_pin=True)
    assert url == "https://example.trycloudflare.com?pin=123456"


def test_build_qr_url_excludes_pin_when_disabled():
    url = build_qr_url("https://example.trycloudflare.com", "123456", include_pin=False)
    assert url == "https://example.trycloudflare.com"


def test_build_qr_url_no_pin_returns_url_only():
    # PIN無し設定 (pin空文字) の場合は include_pin=True でもURLのみ
    url = build_qr_url("https://example.trycloudflare.com", "", include_pin=True)
    assert url == "https://example.trycloudflare.com"


# ── AuthManager セッション上限 (RLC-01 #4) ───────────────────────────────────

def test_session_cap_evicts_oldest():
    auth = AuthManager(pin="123456", require_pin=True, ttl_seconds=99999)
    sessions = []
    for i in range(_MAX_SESSIONS + 1):  # 31回発行
        sessions.append(auth.create_session(f"ip{i}"))

    # 総数は上限どおり
    assert len(auth._sessions) == _MAX_SESSIONS
    # 最古(最初に発行した)セッションは消えている
    assert auth.get_session(sessions[0].session_id) is None
    # 最新のセッションは有効
    assert auth.get_session(sessions[-1].session_id) is not None


def test_session_cap_not_triggered_below_limit():
    auth = AuthManager(pin="123456", require_pin=True, ttl_seconds=99999)
    first = auth.create_session("ipA")
    for i in range(5):
        auth.create_session(f"ip{i}")
    # 上限未満なら最初のセッションも残っている
    assert auth.get_session(first.session_id) is not None


# ── Cookie secure / max_age (RLC-01 #1, #3) ─────────────────────────────────

def test_cookie_secure_true_when_tunnel_enabled():
    assert cookie_secure(True) is True


def test_cookie_secure_false_when_local_bridge():
    # ローカルブリッジ(http://127.0.0.1)まで secure=True にするとCookieが
    # 送信されずセッションが機能しなくなるため False。
    assert cookie_secure(False) is False


def test_cookie_max_age_uses_ttl_minutes():
    assert cookie_max_age(180) == 180 * 60


def test_cookie_max_age_falls_back_to_24h_when_unlimited():
    # room_ttl_minutes<=0 (無期限ルーム) はCookie自体は24時間にする
    assert cookie_max_age(0) == 86400
    assert cookie_max_age(-5) == 86400


# ── TunnelManager: URL検出失敗時の自己クリーンアップ (RLC-01 #2) ─────────────

class _FakeProcess:
    """cloudflaredプロセスのフェイク。terminate/kill/waitの呼び出しを記録する。"""

    def __init__(self) -> None:
        self.terminate_called = False
        self.kill_called = False
        self.returncode: int | None = None

    def terminate(self) -> None:
        self.terminate_called = True
        # 実プロセスと同様、terminate後すぐ終了したことにする
        self.returncode = 0

    def kill(self) -> None:
        self.kill_called = True
        self.returncode = 0

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


@pytest.mark.asyncio
async def test_tunnel_self_cleans_on_url_detection_failure(monkeypatch):
    """URL検出 (_read_url) が失敗したら、start() が自ら stop() 相当の後始末をすること。

    呼び出し側 (controller.py) が tunnel.stop() を呼び忘れても cloudflared
    プロセスが残存しないようにする self-clean の検証。
    """
    tunnel = TunnelManager()
    fake_process = _FakeProcess()

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return fake_process

    async def _fail_read_url(self) -> str:
        raise RuntimeError("cloudflared が予期せず終了しました")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(TunnelManager, "_read_url", _fail_read_url)

    with pytest.raises(RuntimeError):
        await tunnel.start(12345, exe_path=Path("dummy_cloudflared.exe"))

    # self-clean により terminate が呼ばれ、内部状態もクリアされていること
    assert fake_process.terminate_called is True
    assert tunnel.is_running is False
    assert tunnel.public_url is None


@pytest.mark.asyncio
async def test_tunnel_self_cleans_on_timeout(monkeypatch):
    """asyncio.wait_for によるタイムアウト (TimeoutError) でも自己クリーンすること。"""
    tunnel = TunnelManager()
    fake_process = _FakeProcess()

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return fake_process

    async def _hang_read_url(self) -> str:
        # 実際には長時間ハングする状況を、短いタイムアウトで即座に再現する
        await asyncio.sleep(3600)
        return ""  # pragma: no cover — タイムアウトで先に打ち切られる

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(TunnelManager, "_read_url", _hang_read_url)
    monkeypatch.setattr("makeaifactory.remote_room.tunnel_manager.TUNNEL_STARTUP_TIMEOUT", 0.05)

    with pytest.raises(asyncio.TimeoutError):
        await tunnel.start(12345, exe_path=Path("dummy_cloudflared.exe"))

    assert fake_process.terminate_called is True
    assert tunnel.is_running is False

"""DSC-01: Discord Bot を fail-closed にするための純ロジックの単体テスト。

discord.py の実接続や GUI (Qt ウィジェットの表示) はここではテストしない。
コントローラ/設定ダイアログから切り出した純関数・RateLimiter の挙動のみを検証する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.core.discord_bot_controller import is_channel_allowed, is_dm
from makeaifactory.gui.discord_settings_dialog import valid_enabled_channels
from makeaifactory.remote_room import rate_limiter as rate_limiter_module
from makeaifactory.remote_room.rate_limiter import RateLimiter


# ── チャンネル許可判定 (fail-closed) ──────────────────────────────────────────

def test_is_channel_allowed_empty_list_denies():
    # allowed が空 = 「未設定=全許可」ではなく「全拒否」(fail-closed)
    assert is_channel_allowed(123, []) is False


def test_is_channel_allowed_included_channel():
    assert is_channel_allowed(123, [111, 123, 999]) is True


def test_is_channel_allowed_excluded_channel():
    assert is_channel_allowed(123, [111, 999]) is False


# ── DM 判定 ──────────────────────────────────────────────────────────────────

def test_is_dm_true_when_guild_is_none():
    assert is_dm(None) is True


def test_is_dm_false_when_guild_present():
    class _FakeGuild:
        pass

    assert is_dm(_FakeGuild()) is False


# ── ユーザー単位クールダウン (RateLimiter を時刻注入で検証) ───────────────────

class _FakeClock:
    # RateLimiter は未記録キーの last を 0.0 として扱うため、0.0 から開始すると
    # 「時刻0.0 - 未記録last(0.0) = 0 < cooldown」という誤検出になる。
    # 十分大きい時刻から始めることで、その境界条件を避ける。
    def __init__(self, start: float = 10_000.0) -> None:
        self._now = start

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def test_rate_limiter_first_call_allowed(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(rate_limiter_module.time, "monotonic", clock.monotonic)
    limiter = RateLimiter(cooldown_seconds=90)
    assert limiter.is_allowed("user-1") is True


def test_rate_limiter_second_call_immediately_denied(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(rate_limiter_module.time, "monotonic", clock.monotonic)
    limiter = RateLimiter(cooldown_seconds=90)
    assert limiter.is_allowed("user-1") is True
    limiter.record_job("user-1")
    # 直後の2回目はクールダウン中のため不許可
    assert limiter.is_allowed("user-1") is False


def test_rate_limiter_allowed_again_after_cooldown_elapsed(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(rate_limiter_module.time, "monotonic", clock.monotonic)
    limiter = RateLimiter(cooldown_seconds=90)
    limiter.record_job("user-1")
    assert limiter.is_allowed("user-1") is False
    clock.advance(91)
    assert limiter.is_allowed("user-1") is True


def test_rate_limiter_cooldown_is_per_user(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(rate_limiter_module.time, "monotonic", clock.monotonic)
    limiter = RateLimiter(cooldown_seconds=90)
    limiter.record_job("user-1")
    assert limiter.is_allowed("user-1") is False
    # 別ユーザーはクールダウンの影響を受けない
    assert limiter.is_allowed("user-2") is True


# ── 設定ダイアログ保存時のチャンネルID検証 (fail-closed) ──────────────────────

def test_valid_enabled_channels_requires_at_least_one_when_enabled():
    assert valid_enabled_channels(True, []) is False


def test_valid_enabled_channels_ok_when_enabled_with_channels():
    assert valid_enabled_channels(True, [123456]) is True


def test_valid_enabled_channels_ok_when_disabled_and_empty():
    # 無効化時はチャンネルID未設定でも保存を許可する
    assert valid_enabled_channels(False, []) is True

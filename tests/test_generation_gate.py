"""SCH-01/PR1: GenerationGate (プロセス内admission guard) の単体テスト。

Qt/asyncioの実イベントループ以外への依存は無い純ロジックのテスト。
bot_state.jsonミラーの検証のみ tmp_path 経由でファイルI/Oを行う。
"""
import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.core.bot_state import read_bot_state
from makeaifactory.core.generation_gate import GateLease, GenerationGate


# ── 相互排他 ──────────────────────────────────────────────────────────────

def test_try_acquire_exclusive_between_owners():
    gate = GenerationGate(None)
    lease = gate.try_acquire("desktop")
    assert lease is not None
    assert gate.try_acquire("remote") is None


def test_try_acquire_available_again_after_release():
    gate = GenerationGate(None)
    lease = gate.try_acquire("desktop")
    gate.release(lease)
    lease2 = gate.try_acquire("remote")
    assert lease2 is not None


# ── token照合 ──────────────────────────────────────────────────────────────

def test_release_with_foreign_token_is_ignored():
    gate = GenerationGate(None)
    lease = gate.try_acquire("desktop")
    assert lease is not None

    fake_lease = GateLease(owner="desktop", token="not-the-real-token")
    gate.release(fake_lease)

    # 偽leaseでのreleaseは無視されるため、本物のholderはまだ保持されたまま
    assert gate.holder == "desktop"
    assert gate.try_acquire("remote") is None


def test_double_release_is_idempotent():
    gate = GenerationGate(None)
    lease = gate.try_acquire("desktop")
    assert lease is not None

    gate.release(lease)
    gate.release(lease)  # 二重release: 例外を出さず無視される

    assert gate.holder is None
    assert gate.try_acquire("remote") is not None


# ── batchポリシー ────────────────────────────────────────────────────────

def test_batch_active_blocks_remote_bridge_desktop():
    gate = GenerationGate(None)
    gate.begin_batch()
    assert gate.try_acquire("remote") is None
    assert gate.try_acquire("bridge") is None
    assert gate.try_acquire("desktop") is None


def test_batch_active_allows_batch_and_discord():
    gate = GenerationGate(None)
    gate.begin_batch()

    lease = gate.try_acquire("batch")
    assert lease is not None
    gate.release(lease)

    lease2 = gate.try_acquire("discord")
    assert lease2 is not None
    gate.release(lease2)


def test_end_batch_allows_everyone_again():
    gate = GenerationGate(None)
    gate.begin_batch()
    gate.end_batch()

    for owner in ("remote", "bridge", "desktop", "discord", "batch"):
        lease = gate.try_acquire(owner)
        assert lease is not None, f"owner={owner} が取得できませんでした"
        gate.release(lease)


# ── wait_acquire (asyncio) ──────────────────────────────────────────────

async def test_wait_acquire_returns_none_on_cancel():
    gate = GenerationGate(None)
    holder_lease = gate.try_acquire("desktop")
    assert holder_lease is not None

    cancelled = {"flag": False}

    async def _set_cancel_soon() -> None:
        await asyncio.sleep(0.05)
        cancelled["flag"] = True

    asyncio.create_task(_set_cancel_soon())
    result = await gate.wait_acquire(
        "remote", cancel_check=lambda: cancelled["flag"], poll_sec=0.01,
    )
    assert result is None

    gate.release(holder_lease)


async def test_wait_acquire_succeeds_after_release():
    gate = GenerationGate(None)
    holder_lease = gate.try_acquire("desktop")
    assert holder_lease is not None

    async def _release_soon() -> None:
        await asyncio.sleep(0.05)
        gate.release(holder_lease)

    asyncio.create_task(_release_soon())
    result = await gate.wait_acquire("remote", poll_sec=0.01)
    assert result is not None
    assert result.owner == "remote"


# ── スレッド並列 ──────────────────────────────────────────────────────────

def test_concurrent_try_acquire_exactly_one_success():
    gate = GenerationGate(None)

    def _attempt(_i: int):
        return gate.try_acquire("desktop")

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(_attempt, range(20)))

    successes = [r for r in results if r is not None]
    assert len(successes) == 1


# ── bot_stateミラー ────────────────────────────────────────────────────────

def test_bot_state_mirror_lifecycle(tmp_path):
    gate = GenerationGate(tmp_path)

    gate.set_comfy_port(12345)
    state, port = read_bot_state(tmp_path)
    assert (state, port) == ("idle", 12345)

    lease = gate.try_acquire("desktop")
    state, port = read_bot_state(tmp_path)
    assert (state, port) == ("single", 12345)

    gate.release(lease)
    state, _port = read_bot_state(tmp_path)
    assert state == "idle"

    gate.begin_batch()
    state, _port = read_bot_state(tmp_path)
    assert state == "batch"

    # バッチ中のアイテム単位acquire/releaseもbot_state上は"batch"のまま
    lease2 = gate.try_acquire("batch")
    state, _port = read_bot_state(tmp_path)
    assert state == "batch"
    gate.release(lease2)
    state, _port = read_bot_state(tmp_path)
    assert state == "batch"

    gate.end_batch()
    state, port = read_bot_state(tmp_path)
    assert (state, port) == ("idle", 12345)


def test_refresh_bot_state_does_not_change_state(tmp_path):
    gate = GenerationGate(tmp_path)
    gate.set_comfy_port(999)
    lease = gate.try_acquire("desktop")

    state_before, port_before = read_bot_state(tmp_path)
    gate.refresh_bot_state()
    state_after, port_after = read_bot_state(tmp_path)

    assert state_before == state_after == "single"
    assert port_before == port_after == 999

    gate.release(lease)

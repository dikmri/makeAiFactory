"""RemoteJob の所有権判定 (job_is_owned) の単体テスト。"""
import types
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.remote_room.room_server import job_is_owned


def _job(session_id: str):
    return types.SimpleNamespace(session_id=session_id)


# ── 通常ジョブ (session_id が非空) ──────────────────────────────────────────

def test_normal_job_matching_session_is_owner():
    job = _job("A")
    assert job_is_owned(job, "A", local_token_ok=False) is True


def test_normal_job_mismatched_session_is_not_owner():
    job = _job("A")
    assert job_is_owned(job, "B", local_token_ok=False) is False


def test_normal_job_no_session_is_not_owner():
    job = _job("A")
    assert job_is_owned(job, None, local_token_ok=False) is False


def test_normal_job_local_token_does_not_grant_ownership():
    # 通常ジョブはローカルトークンでは所有者とみなさない (cookie セッション無効の想定)
    job = _job("A")
    assert job_is_owned(job, None, local_token_ok=True) is False


# ── ローカルブリッジ経由のジョブ (session_id == "") ──────────────────────────

def test_local_job_with_valid_local_token_is_owner():
    job = _job("")
    assert job_is_owned(job, None, local_token_ok=True) is True


def test_local_job_without_valid_local_token_is_not_owner():
    # cookie セッションを持っていても、ローカルジョブの所有権にはならない
    job = _job("")
    assert job_is_owned(job, "X", local_token_ok=False) is False

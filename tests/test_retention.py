"""RET-01: core/retention.py (保持期間管理・不要ファイルのクリーンアップ) の単体テスト。

tmp_path + os.utime で各ファイル/ディレクトリの mtime を任意の過去時刻へ調整し、
`now` 引数へ基準時刻を注入することで、実際の時間経過を待たずに保持期間判定を検証する。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.core.retention import (
    cleanup_clipboard_temps,
    cleanup_old_logs,
    cleanup_remote_jobs,
    run_cleanup,
)

NOW = 1_700_000_000.0  # 基準時刻 (固定値。os.utimeで各ファイルへ相対的に古い時刻を設定する)
HOUR = 3600
DAY = 86400


def _set_mtime(path: Path, ts: float) -> None:
    os.utime(path, (ts, ts))


def _touch(path: Path, ts: float, content: bytes = b"x") -> None:
    path.write_bytes(content)
    _set_mtime(path, ts)


# ── cleanup_remote_jobs ──────────────────────────────────────────────────────

def test_cleanup_remote_jobs_deletes_expired_dir(tmp_path):
    jobs_dir = tmp_path / "jobs"
    old_job = jobs_dir / "old_job"
    old_job.mkdir(parents=True)
    _touch(old_job / "output.mp4", NOW - 30 * HOUR)
    _set_mtime(old_job, NOW - 30 * HOUR)  # mkdir直後の実時刻ではなく、意図した古い時刻にする

    deleted = cleanup_remote_jobs(jobs_dir, retention_hours=24, now=NOW)

    assert deleted == 1
    assert not old_job.exists()


def test_cleanup_remote_jobs_keeps_recent_dir(tmp_path):
    jobs_dir = tmp_path / "jobs"
    recent_job = jobs_dir / "recent_job"
    recent_job.mkdir(parents=True)
    _touch(recent_job / "output.mp4", NOW - 1 * HOUR)

    deleted = cleanup_remote_jobs(jobs_dir, retention_hours=24, now=NOW)

    assert deleted == 0
    assert recent_job.exists()


def test_cleanup_remote_jobs_zero_retention_disables_cleanup(tmp_path):
    jobs_dir = tmp_path / "jobs"
    old_job = jobs_dir / "old_job"
    old_job.mkdir(parents=True)
    _touch(old_job / "output.mp4", NOW - 1000 * HOUR)

    deleted = cleanup_remote_jobs(jobs_dir, retention_hours=0, now=NOW)

    assert deleted == 0
    assert old_job.exists()


def test_cleanup_remote_jobs_negative_retention_disables_cleanup(tmp_path):
    jobs_dir = tmp_path / "jobs"
    old_job = jobs_dir / "old_job"
    old_job.mkdir(parents=True)
    _touch(old_job / "output.mp4", NOW - 1000 * HOUR)

    deleted = cleanup_remote_jobs(jobs_dir, retention_hours=-1, now=NOW)

    assert deleted == 0
    assert old_job.exists()


def test_cleanup_remote_jobs_missing_dir_returns_zero(tmp_path):
    jobs_dir = tmp_path / "does_not_exist"

    deleted = cleanup_remote_jobs(jobs_dir, retention_hours=24, now=NOW)

    assert deleted == 0


def test_cleanup_remote_jobs_uses_dir_mtime_when_empty(tmp_path):
    jobs_dir = tmp_path / "jobs"
    empty_old_job = jobs_dir / "empty_old"
    empty_old_job.mkdir(parents=True)
    _set_mtime(empty_old_job, NOW - 48 * HOUR)

    deleted = cleanup_remote_jobs(jobs_dir, retention_hours=24, now=NOW)

    assert deleted == 1
    assert not empty_old_job.exists()


def test_cleanup_remote_jobs_continues_after_one_failure(tmp_path, monkeypatch):
    """1件の削除(shutil.rmtree)が失敗しても、他のディレクトリの削除は継続する。"""
    import makeaifactory.core.retention as retention_module

    jobs_dir = tmp_path / "jobs"
    job_a = jobs_dir / "job_a"
    job_b = jobs_dir / "job_b"
    job_a.mkdir(parents=True)
    job_b.mkdir(parents=True)
    _touch(job_a / "output.mp4", NOW - 48 * HOUR)
    _touch(job_b / "output.mp4", NOW - 48 * HOUR)
    _set_mtime(job_a, NOW - 48 * HOUR)
    _set_mtime(job_b, NOW - 48 * HOUR)

    real_rmtree = retention_module.shutil.rmtree

    def _flaky_rmtree(path, *args, **kwargs):
        if Path(path).name == "job_a":
            raise OSError("simulated failure")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(retention_module.shutil, "rmtree", _flaky_rmtree)

    deleted = cleanup_remote_jobs(jobs_dir, retention_hours=24, now=NOW)

    assert deleted == 1  # job_bのみ成功
    assert job_a.exists()  # 失敗したjob_aは残る
    assert not job_b.exists()


def test_cleanup_remote_jobs_ignores_files_at_top_level(tmp_path):
    """jobs_dir直下のファイル(ディレクトリでないもの)は対象外。"""
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True)
    stray_file = jobs_dir / "stray.txt"
    _touch(stray_file, NOW - 1000 * HOUR)

    deleted = cleanup_remote_jobs(jobs_dir, retention_hours=24, now=NOW)

    assert deleted == 0
    assert stray_file.exists()


# ── cleanup_old_logs ─────────────────────────────────────────────────────────

def test_cleanup_old_logs_deletes_old_date_log_and_job_log(tmp_path):
    logs_dir = tmp_path / "logs"
    jobs_dir = logs_dir / "jobs"
    jobs_dir.mkdir(parents=True)

    old_date_log = logs_dir / "makeaifactory_20250101.log"
    old_job_log = jobs_dir / "job123.log"
    _touch(old_date_log, NOW - 20 * DAY)
    _touch(old_job_log, NOW - 20 * DAY)

    deleted = cleanup_old_logs(logs_dir, keep_days=14, now=NOW)

    assert deleted == 2
    assert not old_date_log.exists()
    assert not old_job_log.exists()


def test_cleanup_old_logs_keeps_recent_files(tmp_path):
    logs_dir = tmp_path / "logs"
    jobs_dir = logs_dir / "jobs"
    jobs_dir.mkdir(parents=True)

    recent_date_log = logs_dir / "makeaifactory_20250601.log"
    recent_job_log = jobs_dir / "job456.log"
    _touch(recent_date_log, NOW - 1 * DAY)
    _touch(recent_job_log, NOW - 1 * DAY)

    deleted = cleanup_old_logs(logs_dir, keep_days=14, now=NOW)

    assert deleted == 0
    assert recent_date_log.exists()
    assert recent_job_log.exists()


def test_cleanup_old_logs_never_touches_app_log_or_setup_log(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    app_log = logs_dir / "app.log"
    app_log_1 = logs_dir / "app.log.1"
    setup_log = logs_dir / "setup.log"
    _touch(app_log, NOW - 100 * DAY)
    _touch(app_log_1, NOW - 100 * DAY)
    _touch(setup_log, NOW - 100 * DAY)

    deleted = cleanup_old_logs(logs_dir, keep_days=14, now=NOW)

    assert deleted == 0
    assert app_log.exists()
    assert app_log_1.exists()
    assert setup_log.exists()


def test_cleanup_old_logs_missing_dir_returns_zero(tmp_path):
    logs_dir = tmp_path / "does_not_exist"

    deleted = cleanup_old_logs(logs_dir, keep_days=14, now=NOW)

    assert deleted == 0


# ── cleanup_clipboard_temps ──────────────────────────────────────────────────

def test_cleanup_clipboard_temps_deletes_old_prefixed_files(tmp_path):
    old_clip = tmp_path / "maf_clip_abc123.png"
    _touch(old_clip, NOW - 48 * HOUR)

    deleted = cleanup_clipboard_temps(tmp_path, older_than_hours=24, now=NOW)

    assert deleted == 1
    assert not old_clip.exists()


def test_cleanup_clipboard_temps_keeps_recent_files(tmp_path):
    recent_clip = tmp_path / "maf_clip_recent.png"
    _touch(recent_clip, NOW - 1 * HOUR)

    deleted = cleanup_clipboard_temps(tmp_path, older_than_hours=24, now=NOW)

    assert deleted == 0
    assert recent_clip.exists()


def test_cleanup_clipboard_temps_ignores_non_matching_prefix(tmp_path):
    other_file = tmp_path / "other_temp.png"
    _touch(other_file, NOW - 48 * HOUR)

    deleted = cleanup_clipboard_temps(tmp_path, older_than_hours=24, now=NOW)

    assert deleted == 0
    assert other_file.exists()


def test_cleanup_clipboard_temps_missing_dir_returns_zero(tmp_path):
    missing_dir = tmp_path / "does_not_exist"

    deleted = cleanup_clipboard_temps(missing_dir, older_than_hours=24, now=NOW)

    assert deleted == 0


# ── run_cleanup ───────────────────────────────────────────────────────────────

def test_run_cleanup_runs_all_and_returns_dict(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    logs_dir = tmp_path / "logs"
    jobs_dir = runtime_root / "remote_room" / "jobs"
    jobs_dir.mkdir(parents=True)
    logs_dir.mkdir(parents=True)

    old_job = jobs_dir / "old_job"
    old_job.mkdir()
    _touch(old_job / "output.mp4", time.time() - 30 * HOUR)
    _set_mtime(old_job, time.time() - 30 * HOUR)  # mkdir直後の実時刻を古い時刻で上書き

    old_date_log = logs_dir / "makeaifactory_20200101.log"
    _touch(old_date_log, time.time() - 30 * DAY)

    # tempfile.gettempdir() を tmp_path 配下へ差し替えてclipboard項目も検証する
    clip_dir = tmp_path / "cliptemp"
    clip_dir.mkdir()
    old_clip = clip_dir / "maf_clip_x.png"
    _touch(old_clip, time.time() - 48 * HOUR)
    monkeypatch.setattr("makeaifactory.core.retention.tempfile.gettempdir", lambda: str(clip_dir))

    paths = SimpleNamespace(runtime_root=runtime_root, logs_dir=logs_dir)

    result = run_cleanup(paths, retention_hours=24)

    assert result == {"remote_jobs": 1, "logs": 1, "clipboard": 1}
    assert not old_job.exists()
    assert not old_date_log.exists()
    assert not old_clip.exists()


def test_run_cleanup_continues_when_remote_jobs_step_raises(tmp_path, monkeypatch):
    """paths.runtime_root アクセスで例外が起きても、他の項目は実行され dict が返る。"""
    import makeaifactory.core.retention as retention_module

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)
    old_date_log = logs_dir / "makeaifactory_20200101.log"
    _touch(old_date_log, time.time() - 30 * DAY)

    class _BrokenPaths:
        @property
        def runtime_root(self):
            raise RuntimeError("simulated failure")

        @property
        def logs_dir(self):
            return logs_dir

    monkeypatch.setattr(retention_module.tempfile, "gettempdir", lambda: str(tmp_path / "nonexistent_temp"))

    result = run_cleanup(_BrokenPaths(), retention_hours=24)

    assert result["remote_jobs"] == 0
    assert result["logs"] == 1
    assert result["clipboard"] == 0
    assert not old_date_log.exists()


def test_run_cleanup_continues_when_logs_step_raises(tmp_path, monkeypatch):
    """cleanup_old_logs が例外を投げても、他の項目 (remote_jobs) は実行され dict が返る。"""
    import makeaifactory.core.retention as retention_module

    runtime_root = tmp_path / "runtime"
    jobs_dir = runtime_root / "remote_room" / "jobs"
    jobs_dir.mkdir(parents=True)
    old_job = jobs_dir / "old_job"
    old_job.mkdir()
    _touch(old_job / "output.mp4", time.time() - 30 * HOUR)
    _set_mtime(old_job, time.time() - 30 * HOUR)

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated logs failure")

    monkeypatch.setattr(retention_module, "cleanup_old_logs", _raise)
    monkeypatch.setattr(retention_module.tempfile, "gettempdir", lambda: str(tmp_path / "nonexistent_temp"))

    paths = SimpleNamespace(runtime_root=runtime_root, logs_dir=tmp_path / "logs")

    result = run_cleanup(paths, retention_hours=24)

    assert result["remote_jobs"] == 1
    assert not old_job.exists()
    assert result["logs"] == 0
    assert result["clipboard"] == 0

"""RET-01: 保持期間管理・不要ファイルのクリーンアップ。

監査で判明した「削除されずに無限累積するファイル群」への対処をここへ集約する。

- Remote Room jobs (`runtime_root/remote_room/jobs/<job_id>/`): 停止時に入力画像
  (input.png) のみ削除されるが、output.mp4/job.json は保持期間を過ぎても残り続けて
  いた。→ `cleanup_remote_jobs` で settings の `output_retention_hours` に基づき
  ディレクトリごと削除する。
- ログ: 日付付き `makeaifactory_YYYYMMDD.log` (廃止予定。log_manager.py側で発行を
  止めるが、既存の残骸は掃除が必要) と `logs/jobs/<job_id>.log` は無期限に増え続ける。
  → `cleanup_old_logs` で一定日数を超えたものを削除する (app.log*/setup.log* は
  RotatingFileHandlerで世代管理済みのため対象外)。
- clipboard: `gui/main_window.py` の貼り付け機能が %TEMP% に書く `maf_clip_*.png` は
  削除されず残り続ける。→ `cleanup_clipboard_temps` で起動時等にsweepする。

すべて `now: float | None = None` (time.time() 相当) を注入できるようにし、
os.utime() で任意の時刻を作れるテストから検証できるようにしている。
個々のファイル/ディレクトリの削除失敗は例外を握って続行する
(1件の失敗で他の削除やアプリ本体の動作に影響を与えないため)。
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .paths import AppPaths

logger = logging.getLogger(__name__)


def _now(now: float | None) -> float:
    return now if now is not None else time.time()


def _dir_last_activity(path: Path) -> float:
    """ディレクトリ配下の最終更新時刻 (mtimeの最大値) を返す。

    配下にファイルが1つも無い場合はディレクトリ自身のmtimeを使う。
    """
    latest = path.stat().st_mtime
    for child in path.rglob("*"):
        try:
            m = child.stat().st_mtime
        except OSError:
            continue
        if m > latest:
            latest = m
    return latest


def cleanup_remote_jobs(jobs_dir: Path, retention_hours: int, now: float | None = None) -> int:
    """`remote_room/jobs/<job_id>/` のうち保持期間を超えたものを削除する。

    ディレクトリ内の最終更新 (mtimeの最大値。中身が無ければdir自身のmtime) が
    `retention_hours` を超えたものを `shutil.rmtree` で削除する。
    `retention_hours <= 0` の場合は無効設定として何もしない。
    個々の削除に失敗しても警告ログを残して続行し、他のディレクトリの削除は継続する。
    実行中ジョブのディレクトリはジョブ進行に伴いmtimeが更新され続けるため、
    誤って削除されることは通常無い(特別なガードは設けていない)。
    """
    if retention_hours <= 0:
        return 0
    if not jobs_dir.exists():
        return 0

    now_ts = _now(now)
    threshold_sec = retention_hours * 3600
    deleted = 0
    for entry in sorted(jobs_dir.iterdir()):
        if not entry.is_dir():
            continue
        try:
            last_activity = _dir_last_activity(entry)
            if now_ts - last_activity > threshold_sec:
                shutil.rmtree(entry)
                deleted += 1
                logger.info("RET-01: 保持期間切れのRemote Roomジョブを削除しました: %s", entry.name)
        except Exception:
            logger.warning("RET-01: Remote Roomジョブの削除に失敗しました: %s", entry, exc_info=True)
    return deleted


def cleanup_old_logs(logs_dir: Path, keep_days: int = 14, now: float | None = None) -> int:
    """古いログファイルを削除する。

    対象: `makeaifactory_YYYYMMDD.log` (旧仕様の日次ログの残骸) と
    `logs/jobs/*.log`。`app.log*`/`setup.log*` はRotatingFileHandlerで世代管理
    済みのため一切触れない。
    """
    if not logs_dir.exists():
        return 0

    now_ts = _now(now)
    threshold_sec = keep_days * 86400
    candidates: list[Path] = list(logs_dir.glob("makeaifactory_*.log"))
    jobs_dir = logs_dir / "jobs"
    if jobs_dir.exists():
        candidates.extend(jobs_dir.glob("*.log"))

    deleted = 0
    for f in candidates:
        try:
            if now_ts - f.stat().st_mtime > threshold_sec:
                f.unlink()
                deleted += 1
        except Exception:
            logger.warning("RET-01: 古いログの削除に失敗しました: %s", f, exc_info=True)
    return deleted


def cleanup_clipboard_temps(
    temp_dir: Path, prefix: str = "maf_clip_", older_than_hours: int = 24, now: float | None = None
) -> int:
    """`temp_dir` 直下の `<prefix>*.png` のうち古いものを削除する(起動時sweep用)。

    通常はjob終了時 (app.py `_run_job`) に個別削除されるが、異常終了で削除
    できなかった残骸を掃除するための保険。
    """
    if not temp_dir.exists():
        return 0

    now_ts = _now(now)
    threshold_sec = older_than_hours * 3600
    deleted = 0
    for f in temp_dir.glob(f"{prefix}*.png"):
        try:
            if now_ts - f.stat().st_mtime > threshold_sec:
                f.unlink()
                deleted += 1
        except Exception:
            logger.warning("RET-01: クリップボード一時ファイルの削除に失敗しました: %s", f, exc_info=True)
    return deleted


def run_cleanup(paths: "AppPaths", retention_hours: int) -> dict:
    """上記のクリーンアップを一括実行する。

    各項目は個別にtry/exceptで保護されており、いずれかが例外を送出しても
    他の項目の実行やアプリ本体の動作には影響しない。戻り値は各項目の削除件数。
    """
    result = {"remote_jobs": 0, "logs": 0, "clipboard": 0}

    try:
        jobs_dir = paths.runtime_root / "remote_room" / "jobs"
        result["remote_jobs"] = cleanup_remote_jobs(jobs_dir, retention_hours)
    except Exception:
        logger.warning("RET-01: Remote Roomジョブのcleanup中に例外が発生しました", exc_info=True)

    try:
        result["logs"] = cleanup_old_logs(paths.logs_dir)
    except Exception:
        logger.warning("RET-01: ログのcleanup中に例外が発生しました", exc_info=True)

    try:
        result["clipboard"] = cleanup_clipboard_temps(Path(tempfile.gettempdir()))
    except Exception:
        logger.warning("RET-01: クリップボード一時ファイルのcleanup中に例外が発生しました", exc_info=True)

    logger.info(
        "RET-01: cleanup完了 (remote_jobs=%d, logs=%d, clipboard=%d)",
        result["remote_jobs"], result["logs"], result["clipboard"],
    )
    return result

"""GitHub release auto-update."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, NamedTuple

import httpx

from ..constants import APP_VERSION, GITHUB_REPO

logger = logging.getLogger(__name__)

# ── Launch flags ────────────────────────────────────────────────────────────
# DETACHED_PROCESS (0x08) は console アプリ (cmd.exe) に対して「新しいコンソールを
# 割り当てろ」という意味になり、CREATE_NO_WINDOW と競合して黒窓が表示される。
# そのため DETACHED_PROCESS は使わず、代わりに以下を使う:
#   CREATE_NO_WINDOW      : ウィンドウを作らない
#   CREATE_NEW_PROCESS_GROUP : 親の Ctrl+C 等を継承しない
#   CREATE_BREAKAWAY_FROM_JOB: 親の Job Object から離脱 (死活連動を防ぐ)
# さらに STARTUPINFO.wShowWindow = SW_HIDE で二重に非表示化する。
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_CREATE_NEW_PROCESS_GROUP  = 0x00000200


class ReleaseInfo(NamedTuple):
    version: str
    tag: str
    download_url: str
    release_url: str


def _parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.lstrip("v").split("."))


async def check_for_update() -> ReleaseInfo | None:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url, headers={"Accept": "application/vnd.github+json"})
        if resp.status_code != 200:
            logger.debug("GitHub API returned %d", resp.status_code)
            return None
        data = resp.json()
        tag = data.get("tag_name", "")
        latest_ver = tag.lstrip("v")
        if not latest_ver:
            return None
        if _parse_version(latest_ver) <= _parse_version(APP_VERSION):
            logger.debug("Latest v%s <= current v%s. Skip.", latest_ver, APP_VERSION)
            return None
        for asset in data.get("assets", []):
            if asset.get("name", "").endswith("-windows.zip"):
                return ReleaseInfo(
                    version=latest_ver,
                    tag=tag,
                    download_url=asset["browser_download_url"],
                    release_url=data.get("html_url", ""),
                )
    except Exception as e:
        logger.debug("Update check failed: %s", e)
    return None


async def download_update(
    release: ReleaseInfo,
    progress_cb: Callable[[float], None] | None = None,
) -> Path:
    tmp_path = Path(tempfile.mktemp(suffix=".zip", prefix="maf_update_"))
    logger.info("Downloading update: %s -> %s", release.download_url, tmp_path)
    async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
        async with client.stream("GET", release.download_url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(tmp_path, "wb") as f:
                async for chunk in resp.aiter_bytes(65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total:
                        progress_cb(downloaded / total)
    return tmp_path


def apply_update_and_restart(zip_path: Path) -> None:
    """ZIP を展開して BAT スクリプトにファイル差し替え+再起動を委譲する。

    ウィンドウを完全に非表示にするために:
      - DETACHED_PROCESS は使わない (新コンソール割り当てが起きて窓が出る)
      - CREATE_NO_WINDOW + STARTUPINFO.wShowWindow=SW_HIDE で二重に非表示化
      - CREATE_BREAKAWAY_FROM_JOB で親 Job Object から独立

    アプリ起動には start "" ではなく PowerShell Start-Process を使う
    (より確実にプロセスを起動できる)。
    """
    if not getattr(sys, "frozen", False):
        logger.warning("Not frozen — skipping update apply")
        return

    exe     = Path(sys.executable)
    exe_dir = exe.parent
    pid     = os.getpid()

    # ── 1. ZIP 展開 ─────────────────────────────────────────────────────
    extract_dir = exe_dir / "_upd_staging"
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)

    logger.info("Extracting %s -> %s", zip_path, extract_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    entries = list(extract_dir.iterdir())
    actual_src = entries[0] if (len(entries) == 1 and entries[0].is_dir()) else extract_dir
    logger.info("Update source: %s", actual_src)

    # ── 2. BAT ファイル生成 ──────────────────────────────────────────────
    log_path = exe_dir / "_update_debug.log"
    bat_path = exe_dir / "_update.bat"

    # PowerShell の Start-Process で EXE を起動する行
    # BAT 内で %EXE% が展開されてから PS に渡るので、PS は ASCII パスを受け取る
    ps_launch = (
        "powershell -NoProfile -NonInteractive -WindowStyle Hidden "
        '-Command "Start-Process -FilePath \'%EXE%\'"'
    )

    bat_lines = [
        "@echo off",
        f'set "LOG={log_path}"',
        f'set "SRC={actual_src}"',
        f'set "DST={exe_dir}"',
        f'set "EXE={exe}"',
        f'set "PID_VAL={pid}"',
        "",
        # 起動直後にログ書き込み (これが出れば BAT が実行された証拠)
        'echo %TIME% updater bat started > "%LOG%"',
        'echo SRC=%SRC% >> "%LOG%"',
        'echo DST=%DST% >> "%LOG%"',
        'echo EXE=%EXE% >> "%LOG%"',
        'echo PID=%PID_VAL% >> "%LOG%"',
        "",
        "REM Wait for original process to exit (max 60 sec)",
        "set COUNT=0",
        ":wait_loop",
        'tasklist /FI "PID eq %PID_VAL%" /NH 2>nul | find /I ".exe" >nul',
        "if errorlevel 1 goto process_gone",
        "set /A COUNT+=1",
        "if %COUNT% GEQ 60 goto process_gone",
        "timeout /t 1 /nobreak >nul",
        "goto wait_loop",
        "",
        ":process_gone",
        'echo %TIME% process gone after %COUNT%s >> "%LOG%"',
        "timeout /t 2 /nobreak >nul",
        "",
        "REM Verify staging directory",
        'if not exist "%SRC%" (',
        '    echo ERROR: staging dir not found: %SRC% >> "%LOG%"',
        "    exit /b 1",
        ")",
        'echo %TIME% staging ok, starting robocopy >> "%LOG%"',
        "",
        "REM Copy updated files",
        'robocopy "%SRC%" "%DST%" /E /IS /IT /IM /NFL /NDL /NJH >> "%LOG%" 2>&1',
        "set RCEXIT=%ERRORLEVEL%",
        'echo %TIME% robocopy exit=%RCEXIT% >> "%LOG%"',
        "",
        "if %RCEXIT% GEQ 8 (",
        '    echo ERROR: robocopy failed with exit=%RCEXIT% >> "%LOG%"',
        "    exit /b 1",
        ")",
        "",
        "REM Launch updated app via PowerShell Start-Process",
        'if exist "%EXE%" (',
        f'    echo %TIME% launching %EXE% >> "%LOG%"',
        f"    {ps_launch}",
        '    echo %TIME% launch command sent >> "%LOG%"',
        ") else (",
        '    echo ERROR: exe not found after copy: %EXE% >> "%LOG%"',
        ")",
        "",
        "timeout /t 5 /nobreak >nul",
        'rd /s /q "%SRC%" 2>nul',
        'echo %TIME% cleanup done >> "%LOG%"',
        'del "%~f0" 2>nul',
    ]

    bat_content = "\r\n".join(bat_lines) + "\r\n"
    bat_path.write_bytes(bat_content.encode("ascii"))
    logger.info("BAT written: %s", bat_path)

    # ── 3. BAT を完全非表示で起動 ────────────────────────────────────────
    # STARTUPINFO.wShowWindow = SW_HIDE (0) で窓を強制非表示
    si = subprocess.STARTUPINFO()
    si.dwFlags = subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE

    # DETACHED_PROCESS は使わない (コンソール再割り当てで黒窓が出る)
    flags_primary  = subprocess.CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP | _CREATE_BREAKAWAY_FROM_JOB
    flags_fallback = subprocess.CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP

    try:
        proc = subprocess.Popen(
            ["cmd.exe", "/c", str(bat_path)],
            creationflags=flags_primary,
            startupinfo=si,
        )
    except OSError:
        logger.warning("CREATE_BREAKAWAY_FROM_JOB failed, retrying without it")
        proc = subprocess.Popen(
            ["cmd.exe", "/c", str(bat_path)],
            creationflags=flags_fallback,
            startupinfo=si,
        )

    logger.info(
        "Update BAT launched (app_pid=%d bat_pid=%d): %s -> %s",
        pid, proc.pid, actual_src, exe_dir,
    )

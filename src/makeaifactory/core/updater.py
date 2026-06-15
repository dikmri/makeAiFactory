"""GitHub release auto-update."""
from __future__ import annotations

import asyncio
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

# We intentionally use a .bat file instead of a .ps1 file for the update
# script.  On Japanese Windows, PowerShell 5.1 reads .ps1 files as CP932
# unless a UTF-8 BOM is present, which can silently corrupt the script.
# Additionally, security software (SmartScreen / Defender) may block
# unsigned .ps1 files without showing any error.
#
# A plain .bat file avoids both problems:
#   - cmd.exe always reads batch files as the OEM codepage, but our batch
#     content is pure ASCII so there is no encoding mismatch.
#   - Batch files are never subject to PowerShell execution policy.
#   - "start "" "app.exe"" creates a truly independent process that
#     survives the parent cmd.exe exiting.


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
    """Extract zip and hand off file replacement + restart to a .bat script.

    All paths used inside the bat file are guaranteed ASCII (exe_dir is
    validated at install time, _upd_staging is a child of exe_dir).
    The bat file itself is written as pure ASCII bytes, so cmd.exe reads
    it correctly regardless of the system codepage.
    """
    if not getattr(sys, "frozen", False):
        logger.warning("Not frozen - skipping update apply")
        return

    exe     = Path(sys.executable)
    exe_dir = exe.parent
    pid     = os.getpid()

    # ── 1. Extract zip to a staging directory inside exe_dir ────────────
    extract_dir = exe_dir / "_upd_staging"
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)

    logger.info("Extracting zip %s -> %s", zip_path, extract_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)
    logger.info("Extraction complete")

    # If the zip had a single top-level subdirectory, unwrap it so that
    # robocopy copies the actual files, not the subdirectory itself.
    entries = list(extract_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        actual_src = entries[0]
        logger.info("Unwrapping zip subdirectory: %s", actual_src.name)
    else:
        actual_src = extract_dir

    # ── 2. Write the updater batch file ─────────────────────────────────
    log_path = exe_dir / "_update_debug.log"
    bat_path = exe_dir / "_update.bat"

    # All values substituted here are ASCII (exe_dir guaranteed, pid is int).
    bat_lines = [
        "@echo off",
        f'set "LOG={log_path}"',
        f'set "SRC={actual_src}"',
        f'set "DST={exe_dir}"',
        f'set "EXE={exe}"',
        "",
        'echo %TIME% bat started > "%LOG%"',
        "",
        "REM Wait for original process to exit (max 60 sec).",
        "set COUNT=0",
        ":wait_loop",
        f'tasklist /FI "PID eq {pid}" /NH 2>nul | find /I ".exe" >nul',
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
        "REM Verify the staging directory exists.",
        'if not exist "%SRC%" (',
        '    echo ERROR: staging dir not found: %SRC% >> "%LOG%"',
        "    exit /b 1",
        ")",
        'echo %TIME% staging dir ok >> "%LOG%"',
        "",
        "REM Copy updated files over the installation.",
        'echo %TIME% robocopy start >> "%LOG%"',
        'robocopy "%SRC%" "%DST%" /E /IS /IT /IM /NFL /NDL /NJH >> "%LOG%" 2>&1',
        'echo %TIME% robocopy done rc=%ERRORLEVEL% >> "%LOG%"',
        "",
        "REM Launch the updated application.",
        'if exist "%EXE%" (',
        '    echo %TIME% launching %EXE% >> "%LOG%"',
        '    start "" "%EXE%"',
        '    echo %TIME% launched ok >> "%LOG%"',
        ") else (",
        '    echo ERROR: exe not found after copy: %EXE% >> "%LOG%"',
        ")",
        "",
        "REM Cleanup staging directory and this batch file.",
        "timeout /t 5 /nobreak >nul",
        'rd /s /q "%SRC%" 2>nul',
        'del "%~f0" 2>nul',
    ]
    bat_content = "\r\n".join(bat_lines) + "\r\n"

    # Write as raw ASCII bytes - cmd.exe reads batch files as the system
    # OEM codepage, which is a superset of ASCII, so this is always safe.
    bat_path.write_bytes(bat_content.encode("ascii"))
    logger.info("BAT written: %s", bat_path)

    # ── 3. Launch the batch file via cmd.exe ────────────────────────────
    # "cmd /c" exits when the batch finishes, but the "start" inside the
    # batch creates an independent process, so the new app keeps running.
    proc = subprocess.Popen(
        ["cmd.exe", "/c", str(bat_path)],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
    )
    logger.info(
        "Update BAT launched (app_pid=%d bat_pid=%d): %s -> %s",
        pid, proc.pid, actual_src, exe_dir,
    )

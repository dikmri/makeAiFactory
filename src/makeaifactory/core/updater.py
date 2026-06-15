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

# CREATE_BREAKAWAY_FROM_JOB: child process escapes parent Job Object.
# Prevents the OS from killing the updater script when the app exits.
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


class ReleaseInfo(NamedTuple):
    version: str
    tag: str
    download_url: str
    release_url: str


def _parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.lstrip("v").split("."))


def _ps_quote(path: Path) -> str:
    """Escape a path for use inside a PowerShell single-quoted string."""
    return str(path).replace("'", "''")


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
    logger.info("Downloading update zip: %s -> %s", release.download_url, tmp_path)
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
    """Extract zip and hand off file replacement + restart to a PowerShell script.

    The PS1 is written with UTF-8 BOM (utf-8-sig) so that PowerShell 5.1 on
    Japanese Windows reads it correctly instead of misinterpreting it as CP932.
    All PS1 content is ASCII-only to eliminate any encoding ambiguity.
    """
    if not getattr(sys, "frozen", False):
        logger.warning("Not frozen - skipping update apply")
        return

    exe = Path(sys.executable)
    exe_dir = exe.parent
    pid = os.getpid()

    # Extract to exe_dir (guaranteed ASCII path, same drive = fast copy).
    extract_dir = exe_dir / "_upd_staging"
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)

    logger.info("Extracting zip: %s -> %s", zip_path, extract_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    # If the zip contained a single top-level subdirectory, unwrap it.
    entries = list(extract_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        actual_src = entries[0]
        logger.info("Unwrapping subdirectory in zip: %s", actual_src.name)
    else:
        actual_src = extract_dir

    log_path  = exe_dir / "_update_debug.log"
    ps_path   = exe_dir / "_update.ps1"

    # --- PS1 script: ASCII-ONLY content, written with UTF-8 BOM ---
    # PowerShell 5.1 reads .ps1 files as the system codepage (CP932 on Japanese
    # Windows) unless a BOM is present.  utf-8-sig adds the BOM so PS reads
    # the file as UTF-8.  We also keep every character ASCII to be safe.
    ps_lines = [
        f"$pid_val  = {pid}",
        f"$src      = '{_ps_quote(actual_src)}'",
        f"$dst      = '{_ps_quote(exe_dir)}'",
        f"$exe_path = '{_ps_quote(exe)}'",
        f"$zip_path = '{_ps_quote(zip_path)}'",
        f"$log_path = '{_ps_quote(log_path)}'",
        "$myself   = $MyInvocation.MyCommand.Path",
        "",
        "function Log($msg) {",
        "    $line = \"$(Get-Date -Format 'HH:mm:ss') $msg\"",
        "    Write-Output $line | Out-File -Append -Encoding utf8 $log_path",
        "}",
        "",
        "Log 'updater started'",
        "Log \"src=$src dst=$dst pid=$pid_val\"",
        "",
        "# Wait for the original process to exit (max 60 s).",
        "$waited = 0",
        "while ((Get-Process -Id $pid_val -ErrorAction SilentlyContinue) -and ($waited -lt 60000)) {",
        "    Start-Sleep -Milliseconds 500",
        "    $waited += 500",
        "}",
        "Log \"process gone after ${waited}ms\"",
        "Start-Sleep -Seconds 2",
        "",
        "# Verify source exists.",
        "if (-not (Test-Path $src)) {",
        "    Log \"ERROR: src not found: $src\"",
        "    exit 1",
        "}",
        "Log 'source exists - starting robocopy'",
        "",
        "# Copy files (robocopy exit 0-7 = success/partial).",
        "robocopy $src $dst /E /IS /IT /IM /NFL /NDL /NJH",
        "$rc = $LASTEXITCODE",
        "Log \"robocopy exit=$rc\"",
        "if ($rc -ge 8) {",
        "    Log \"ERROR: robocopy failed with exit code $rc\"",
        "    exit 1",
        "}",
        "",
        "# Launch updated app.",
        "if (Test-Path $exe_path) {",
        "    Log 'launching app'",
        "    Start-Process -FilePath $exe_path",
        "    Log 'launched ok'",
        "} else {",
        "    Log \"ERROR: exe not found after copy: $exe_path\"",
        "}",
        "",
        "# Cleanup.",
        "Start-Sleep -Seconds 5",
        "Remove-Item -Recurse -Force $src      -ErrorAction SilentlyContinue",
        "Remove-Item -Force          $zip_path -ErrorAction SilentlyContinue",
        "Log 'cleanup done'",
        "Remove-Item -Force          $myself   -ErrorAction SilentlyContinue",
    ]
    ps_content = "\n".join(ps_lines) + "\n"

    # Write with BOM so PowerShell 5.1 detects UTF-8 correctly.
    ps_path.write_text(ps_content, encoding="utf-8-sig")
    logger.info("PS1 written: %s", ps_path)

    ps_exe = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

    flags = (
        subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NO_WINDOW
        | _CREATE_BREAKAWAY_FROM_JOB
    )
    cmd = [
        ps_exe,
        "-NoProfile", "-NonInteractive",
        "-WindowStyle", "Hidden",
        "-ExecutionPolicy", "Bypass",
        "-File", str(ps_path),
    ]
    try:
        proc = subprocess.Popen(cmd, creationflags=flags)
    except OSError:
        # Job object may not allow breakaway - retry without the flag.
        logger.warning("CREATE_BREAKAWAY_FROM_JOB denied; retrying without it")
        proc = subprocess.Popen(
            cmd,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )

    logger.info(
        "Updater script launched (app_pid=%d updater_pid=%d): %s -> %s",
        pid, proc.pid, actual_src, exe_dir,
    )

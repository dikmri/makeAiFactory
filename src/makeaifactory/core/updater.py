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
# DETACHED_PROCESS は使わない:
#   console アプリ (cmd.exe) に対して「新コンソールを割り当てろ」の意味になり
#   CREATE_NO_WINDOW と競合して黒窓が出る。
# 代わりに以下の組み合わせで確実に非表示にする:
#   CREATE_NO_WINDOW          : ウィンドウを作らない
#   CREATE_NEW_PROCESS_GROUP  : 親の Ctrl+C 等を継承しない
#   CREATE_BREAKAWAY_FROM_JOB : 親の Job Object から離脱 (死活連動を防ぐ)
# + STARTUPINFO.wShowWindow = SW_HIDE で二重に非表示化
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
    """ZIP を展開し BAT にファイル差し替え + 再起動を委譲する。

    frozen モードでは BAT 起動後に os._exit(0) でプロセスを即座に終了する。
    これにより EXE ファイルのロックが即座に解除され、
    BAT が makeAiFactory.exe を確実に上書きコピーできるようになる。

    【再起動の 2 重保険】
    メイン BAT (_update.bat) が robocopy を実行後、sentinel ファイルを書く。
    ランチャー BAT (_launcher.bat) は sentinel 出現を待ってから
    explorer.exe 経由で EXE を起動する。
    メイン BAT の start コマンドが AV にブロックされても、
    ランチャーが確実に再起動を担保する。
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

    # ZIP のトップレベルにサブディレクトリが 1 つだけある場合はそれを使う
    entries = list(extract_dir.iterdir())
    actual_src = entries[0] if (len(entries) == 1 and entries[0].is_dir()) else extract_dir
    logger.info("Update source: %s", actual_src)

    # ── 2. BAT ファイル生成 ──────────────────────────────────────────────
    log_path      = exe_dir / "_update_debug.log"
    launcher_log  = exe_dir / "_launcher_debug.log"
    bat_path      = exe_dir / "_update.bat"
    launcher_path = exe_dir / "_launcher.bat"
    sentinel_path = exe_dir / "_upd_done.sentinel"

    # ── 2a. ランチャー BAT ──────────────────────────────────────────────
    # sentinel ファイル出現後に EXE を起動する独立プロセス。
    # メイン BAT の start コマンドが失敗しても確実に再起動を実行する。
    # explorer.exe を使用: AV が start コマンドをブロックしてもこれは通る。
    launcher_lines = [
        "@echo off",
        f'set "EXE={exe}"',
        f'set "DST={exe_dir}"',
        f'set "SENTINEL={sentinel_path}"',
        f'set "LOG={launcher_log}"',
        "",
        'echo %DATE% %TIME% launcher started > "%LOG%"',
        'echo EXE=%EXE% >> "%LOG%"',
        "set /A WAIT=0",
        "",
        # sentinel ファイルが出るまで最大 90 秒待機
        ":wait_sentinel",
        'if exist "%SENTINEL%" goto do_launch',
        "set /A WAIT+=1",
        "if %WAIT% GEQ 90 goto do_launch",
        "timeout /t 1 /nobreak >nul",
        "goto wait_sentinel",
        "",
        ":do_launch",
        'echo %TIME% sentinel found (waited %WAIT%s) >> "%LOG%"',
        # メイン BAT の start が先に成功している場合はスキップ
        "timeout /t 5 /nobreak >nul",
        'tasklist /FI "IMAGENAME eq makeAiFactory.exe" /NH 2>nul | find /I "makeAiFactory.exe" >nul',
        'if NOT errorlevel 1 (',
        '    echo %TIME% EXE already running, skipping launcher >> "%LOG%"',
        "    goto cleanup",
        ")",
        # EXE が未起動なら explorer.exe 経由で起動
        'if exist "%EXE%" (',
        '    echo %TIME% launching via explorer.exe >> "%LOG%"',
        '    explorer.exe "%EXE%"',
        '    echo %TIME% explorer.exe launch sent >> "%LOG%"',
        ") else (",
        '    echo %TIME% ERROR: EXE not found: %EXE% >> "%LOG%"',
        ")",
        "",
        ":cleanup",
        'if exist "%SENTINEL%" del "%SENTINEL%" >nul 2>&1',
        'echo %TIME% launcher done >> "%LOG%"',
        'del "%~f0" >nul 2>&1',
    ]
    launcher_content = "\r\n".join(launcher_lines) + "\r\n"
    launcher_path.write_bytes(launcher_content.encode("ascii"))
    logger.info("Launcher BAT written: %s", launcher_path)

    # ── 2b. メイン BAT ──────────────────────────────────────────────────
    bat_lines = [
        "@echo off",
        f'set "LOG={log_path}"',
        f'set "SRC={actual_src}"',
        f'set "DST={exe_dir}"',
        f'set "EXE={exe}"',
        f'set "PID_VAL={pid}"',
        f'set "SENTINEL={sentinel_path}"',
        "",
        # BAT 起動確認ログ
        'echo %DATE% %TIME% updater started > "%LOG%"',
        'echo SRC=%SRC% >> "%LOG%"',
        'echo DST=%DST% >> "%LOG%"',
        'echo EXE=%EXE% >> "%LOG%"',
        'echo PID=%PID_VAL% >> "%LOG%"',
        'echo step-1: waiting for PID %PID_VAL% >> "%LOG%"',
        "",
        # プロセス終了待ち
        "set COUNT=0",
        ":wait_loop",
        'tasklist /FI "PID eq %PID_VAL%" /NH 2>nul | find /I ".exe" >nul',
        "if errorlevel 1 goto do_copy",
        "set /A COUNT+=1",
        "if %COUNT% GEQ 10 goto do_copy",
        "timeout /t 1 /nobreak >nul",
        "goto wait_loop",
        "",
        ":do_copy",
        'echo %TIME% step-2: process gone (count=%COUNT%) >> "%LOG%"',
        "timeout /t 1 /nobreak >nul",
        "",
        'if not exist "%SRC%" (',
        '    echo ERROR: staging dir not found: %SRC% >> "%LOG%"',
        "    exit /b 1",
        ")",
        "",
        # robocopy でファイルコピー
        'echo %TIME% step-3: starting robocopy >> "%LOG%"',
        'robocopy "%SRC%" "%DST%" /E /IS /IT /IM /NFL /NDL /NJH /R:3 /W:2 >> "%LOG%" 2>&1',
        "set RC=%ERRORLEVEL%",
        'echo %TIME% step-4: robocopy exit=%RC% >> "%LOG%"',
        "",
        "if %RC% GEQ 8 (",
        '    echo ERROR: robocopy failed (exit=%RC%) >> "%LOG%"',
        "    exit /b 1",
        ")",
        "",
        # sentinel を書いてランチャー BAT に完了を通知
        'echo done > "%SENTINEL%"',
        'echo %TIME% step-5: sentinel written >> "%LOG%"',
        "",
        # メイン BAT からも start で起動を試みる (ランチャーへの保険)
        'echo %TIME% step-6: attempting EXE launch via start >> "%LOG%"',
        'if exist "%EXE%" (',
        '    start "" /D "%DST%" "%EXE%"',
        '    echo %TIME% step-6: start command sent >> "%LOG%"',
        ") else (",
        '    echo %TIME% step-6: WARNING EXE not found: %EXE% >> "%LOG%"',
        ")",
        "",
        # クリーンアップ (ランチャーが sentinel を消すが、残っていれば消す)
        "timeout /t 5 /nobreak >nul",
        'rd /s /q "%SRC%" >nul 2>&1',
        'if exist "%SENTINEL%" del "%SENTINEL%" >nul 2>&1',
        'echo %TIME% step-7: cleanup done >> "%LOG%"',
        'del "%~f0" >nul 2>&1',
    ]
    bat_content = "\r\n".join(bat_lines) + "\r\n"
    bat_path.write_bytes(bat_content.encode("ascii"))
    logger.info("Main BAT written: %s", bat_path)

    # ── 3. 両 BAT を完全非表示で起動 ────────────────────────────────────
    si = subprocess.STARTUPINFO()
    si.dwFlags = subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE

    flags_primary  = subprocess.CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP | _CREATE_BREAKAWAY_FROM_JOB
    flags_fallback = subprocess.CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP

    def _start_bat(bat: Path) -> subprocess.Popen:
        try:
            return subprocess.Popen(
                ["cmd.exe", "/c", str(bat)],
                creationflags=flags_primary,
                startupinfo=si,
            )
        except OSError:
            logger.warning("CREATE_BREAKAWAY_FROM_JOB failed for %s, retrying", bat.name)
            return subprocess.Popen(
                ["cmd.exe", "/c", str(bat)],
                creationflags=flags_fallback,
                startupinfo=si,
            )

    # ランチャーを先に起動して sentinel 待機を開始させる
    proc_launcher = _start_bat(launcher_path)
    logger.info("Launcher BAT started (pid=%d)", proc_launcher.pid)

    # メイン BAT 起動 (robocopy → sentinel 書き込み → start 試行)
    proc_main = _start_bat(bat_path)
    logger.info(
        "Main BAT started (app_pid=%d main_pid=%d): %s -> %s",
        pid, proc_main.pid, actual_src, exe_dir,
    )

    # ── 4. プロセスを即座に終了して EXE ロックを解除 ────────────────────
    logger.info("Calling os._exit(0) to release EXE lock for updater BATs")
    logging.shutdown()
    os._exit(0)

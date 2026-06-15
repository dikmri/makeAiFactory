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

    【なぜ os._exit(0) が必要か】
    signals.app_quit.emit() → app.quit() → app.exec() 返却 という流れで
    Qt イベントループは終了するが、QRunnable ワーカースレッドはコルーチンを
    実行し続けるため Python プロセスが 60 秒以上終了しない。
    その間 EXE ファイルがロックされ、BAT の robocopy が失敗する。
    os._exit(0) は全スレッドを即座に停止するため、1〜2 秒でプロセスが消える。
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
    log_path = exe_dir / "_update_debug.log"
    bat_path = exe_dir / "_update.bat"

    # EXE 起動: PowerShell Start-Process が最も確実
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
        # BAT 起動確認ログ (この行が出れば BAT は実行されている)
        'echo %DATE% %TIME% updater started > "%LOG%"',
        'echo SRC=%SRC% >> "%LOG%"',
        'echo DST=%DST% >> "%LOG%"',
        'echo EXE=%EXE% >> "%LOG%"',
        'echo PID=%PID_VAL% >> "%LOG%"',
        "",
        # プロセス終了待ち (os._exit で即終了するので通常 1-2 秒で抜ける)
        "REM Wait for the old process to exit (os._exit makes this fast)",
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
        'echo %TIME% process check done (count=%COUNT%) >> "%LOG%"',
        "",
        # ファイルハンドル解放を確実にするため 1 秒追加待機
        "timeout /t 1 /nobreak >nul",
        "",
        # ステージングディレクトリ存在確認
        'if not exist "%SRC%" (',
        '    echo ERROR: staging dir not found: %SRC% >> "%LOG%"',
        "    exit /b 1",
        ")",
        "",
        # robocopy でファイルコピー
        # /E  : サブディレクトリも含む (空ディレクトリも含む)
        # /IS : 同じファイルも上書き
        # /IT : タイムスタンプが異なるファイルも上書き
        # /IM : 更新されたファイルを上書き
        # /R:3: 失敗時 3 回リトライ
        # /W:2: リトライ間隔 2 秒 (デフォルト 30 秒を短縮)
        'echo %TIME% starting robocopy >> "%LOG%"',
        'robocopy "%SRC%" "%DST%" /E /IS /IT /IM /NFL /NDL /NJH /R:3 /W:2 >> "%LOG%" 2>&1',
        "set RC=%ERRORLEVEL%",
        'echo %TIME% robocopy exit=%RC% >> "%LOG%"',
        "",
        # robocopy exit code >= 8 はエラー (0-7 は成功/情報)
        "if %RC% GEQ 8 (",
        '    echo ERROR: robocopy failed (exit=%RC%) >> "%LOG%"',
        "    exit /b 1",
        ")",
        "",
        # 新バージョンの EXE 起動
        'if exist "%EXE%" (',
        '    echo %TIME% launching %EXE% >> "%LOG%"',
        f"    {ps_launch}",
        '    echo %TIME% launch command sent >> "%LOG%"',
        ") else (",
        '    echo ERROR: exe not found after copy: %EXE% >> "%LOG%"',
        "    exit /b 1",
        ")",
        "",
        # クリーンアップ
        "timeout /t 3 /nobreak >nul",
        'rd /s /q "%SRC%" 2>nul',
        'echo %TIME% cleanup done >> "%LOG%"',
        'del "%~f0" 2>nul',
    ]

    bat_content = "\r\n".join(bat_lines) + "\r\n"
    bat_path.write_bytes(bat_content.encode("ascii"))
    logger.info("BAT written: %s", bat_path)

    # ── 3. BAT を完全非表示で起動 ────────────────────────────────────────
    si = subprocess.STARTUPINFO()
    si.dwFlags = subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE

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

    # ── 4. プロセスを即座に終了して EXE ロックを解除 ────────────────────
    # os._exit(0) は全スレッドを即座に停止する (Python の通常終了処理をスキップ)。
    # これにより:
    #   - makeAiFactory.exe のファイルロックが即座に解除される
    #   - BAT が 1-2 秒後に "process gone" を検出して robocopy を開始できる
    #   - robocopy が EXE を確実にコピーできる (Error 32 が発生しない)
    #
    # signals.app_quit.emit() は使わない:
    #   app.quit() → app.exec() 返却後も QRunnable ワーカースレッドが
    #   _run_setup() を続行するため、プロセスが 60 秒以上終了しない。
    logger.info("Calling os._exit(0) to release EXE lock for updater BAT")
    logging.shutdown()  # ログをフラッシュしてからプロセス終了
    os._exit(0)

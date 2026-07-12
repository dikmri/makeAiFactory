"""GitHub release auto-update."""
from __future__ import annotations

import ctypes
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
from ..domain.errors import HashMismatchError
from ..runtime.hash_verifier import verify_sha256

logger = logging.getLogger(__name__)

# ── PS1 テンプレート ────────────────────────────────────────────────────────
# %TEMP% に書き出して ShellExecuteW で起動する。
# ShellExecute = Explorer サービス経由 → 親の Job Object を継承しない。
# 15 秒待機: robocopy 直後の Windows Defender スキャン完了を待つ。
# 起動確認はフルパスで行う: プロセス名だけだと別インスタンスを誤検知する。
_PS1_TEMPLATE = r"""
param(
    [int]$ParentPid,
    [string]$SrcDir,
    [string]$DstDir,
    [string]$ExePath
)
$log = Join-Path $DstDir '_update_log.txt'
function Log($m) { "$(Get-Date -f 'HH:mm:ss') $m" | Out-File $log -Append -Encoding UTF8 }
Log "start  pid=$ParentPid"
Log "src=$SrcDir"
Log "dst=$DstDir"
Log "exe=$ExePath"

# 1. 旧プロセス終了待ち (最大 30 秒)
try {
    $p = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
    if ($p) { $null = $p.WaitForExit(30000) }
    Log "process gone"
} catch { Log "wait error: $_" }

# 2. Windows Defender スキャン完了を待つ
Log "sleeping 15s for AV scan"
Start-Sleep -Seconds 15

# 3. robocopy
Log "robocopy start"
$null = & robocopy $SrcDir $DstDir /E /IS /IT /IM /NFL /NDL /NJH /R:3 /W:2
$rc = $LASTEXITCODE
Log "robocopy exit=$rc"
if ($rc -ge 8) { Log "ERROR: robocopy failed exit=$rc"; exit 1 }

# 4. staging ディレクトリ削除
try {
    $stagingParent = Split-Path $SrcDir -Parent
    Remove-Item $stagingParent -Recurse -Force -ErrorAction Stop
    Log "staging removed"
} catch { Log "staging remove warn: $_" }

# 5. 既に正しいパスで起動済みかチェック (フルパス一致)
$already = @(Get-Process -Name 'makeAiFactory' -ErrorAction SilentlyContinue) |
           Where-Object { $_.Path -ieq $ExePath }
if ($already) { Log "already running at $ExePath, done"; exit 0 }

# 6. 起動 (3 段フォールバック)
$launched = $false
try {
    Start-Process -FilePath $ExePath -WorkingDirectory $DstDir -ErrorAction Stop
    Log "launched via Start-Process"
    $launched = $true
} catch { Log "Start-Process failed: $_" }

if (-not $launched) {
    try {
        Invoke-Item $ExePath -ErrorAction Stop
        Log "launched via Invoke-Item"
        $launched = $true
    } catch { Log "Invoke-Item failed: $_" }
}

if (-not $launched) {
    try {
        $sh = New-Object -ComObject Shell.Application -ErrorAction Stop
        $sh.ShellExecute($ExePath, '', $DstDir, 'open', 1)
        Log "launched via Shell.Application"
        $launched = $true
    } catch { Log "Shell.Application failed: $_" }
}

if (-not $launched) {
    Log "all launch methods failed - writing marker"
    "applied" | Out-File (Join-Path $DstDir '_update_applied.txt') -Encoding UTF8
}

Log "done"
Start-Sleep -Seconds 5
Remove-Item $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
"""


class ReleaseInfo(NamedTuple):
    version: str
    tag: str
    download_url: str
    release_url: str
    # release.yml が生成する "<zip名>.sha256" アセットのURL。
    # 旧リリース(このPR以前にビルドされたもの)には存在しないため None になりうる。
    sha256_url: str | None = None


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
        assets = data.get("assets", [])
        zip_asset = next(
            (a for a in assets if a.get("name", "").endswith("-windows.zip")), None
        )
        if zip_asset is not None:
            # 同じリリースに "<zip名>.sha256" が同梱されていれば取得する。
            # 無い場合(このPR以前の旧リリース)は None のままにし、
            # download_update 側でフォールバック(検証スキップ)させる。
            sha256_asset = next(
                (a for a in assets if a.get("name", "") == f"{zip_asset['name']}.sha256"),
                None,
            )
            return ReleaseInfo(
                version=latest_ver,
                tag=tag,
                download_url=zip_asset["browser_download_url"],
                release_url=data.get("html_url", ""),
                sha256_url=sha256_asset["browser_download_url"] if sha256_asset else None,
            )
    except Exception as e:
        logger.debug("Update check failed: %s", e)
    return None


async def _fetch_expected_sha256(sha256_url: str) -> str | None:
    """`<zip名>.sha256` アセットの内容から期待ハッシュ値を取得する。

    取得自体に失敗した場合は None を返し、呼び出し側でフォールバック
    (検証スキップ+警告ログ)させる。ファイル形式は sha256sum 互換
    ("<hash>  <ファイル名>") を想定し、先頭トークンのみを使う。
    """
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(sha256_url)
            resp.raise_for_status()
        text = resp.text.strip()
        return text.split()[0] if text else None
    except Exception as e:
        logger.warning("SHA256チェックサムファイルの取得に失敗しました: %s", e)
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

    # ── UPD-01: 展開(apply_update_and_restart)前にSHA-256検証 ──────────────
    # release.yml が生成する "<zip名>.sha256" アセットが取得できた場合のみ
    # 厳格に検証する。旧リリース(.sha256未同梱)向けのフォールバックとして、
    # アセットが無い/取得失敗の場合は既存挙動を維持し、検証スキップ+警告ログ
    # のみで適用を継続する(自動更新の「適用は維持」方針のため)。
    if release.sha256_url:
        expected = await _fetch_expected_sha256(release.sha256_url)
        if expected:
            try:
                verify_sha256(tmp_path, expected)
                logger.info("更新ZIPのSHA256検証OK: %s", tmp_path.name)
            except HashMismatchError:
                tmp_path.unlink(missing_ok=True)
                logger.error("更新ZIPのSHA256が一致しません。展開・適用を中止します: %s", tmp_path.name)
                raise
        else:
            logger.warning(
                "SHA256チェックサムを取得できなかったため検証をスキップします: %s", tmp_path.name
            )
    else:
        logger.warning(
            "リリースに .sha256 アセットが見つからないため検証をスキップします"
            "(旧リリース向けフォールバック): %s",
            tmp_path.name,
        )
    return tmp_path


def apply_update_and_restart(zip_path: Path) -> None:
    """ZIP を展開し PS1 スクリプトにファイル差し替え + 再起動を委譲する。

    PS1 は ShellExecuteW (Explorer サービス経由) で起動するため
    親の Job Object を継承せず、親プロセス終了後も確実に動作する。
    os._exit(0) で即座に終了し EXE ロックを解除する。
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

    # ── 2. PS1 を %TEMP% に書き出す ─────────────────────────────────────
    ps1_path = Path(tempfile.mktemp(suffix=".ps1", prefix="maf_upd_"))
    # UTF-8 BOM: PowerShell がエンコーディングを確実に認識するため必須
    ps1_path.write_text(_PS1_TEMPLATE, encoding="utf-8-sig")
    logger.info("PS1 written: %s", ps1_path)

    # ── 3. ShellExecuteW で PS1 を起動 (Job Object 非継承) ──────────────
    args = (
        f'-WindowStyle Hidden -NonInteractive -ExecutionPolicy Bypass '
        f'-File "{ps1_path}" '
        f'-ParentPid {pid} '
        f'-SrcDir "{actual_src}" '
        f'-DstDir "{exe_dir}" '
        f'-ExePath "{exe}"'
    )
    logger.info("Launching PS1 via ShellExecuteW")
    ret = ctypes.windll.shell32.ShellExecuteW(
        None, "open", "powershell.exe", args, str(exe_dir), 0
    )
    if ret <= 32:
        # ShellExecute 失敗 (32 以下はエラーコード) → Popen にフォールバック
        logger.warning("ShellExecuteW returned %d, falling back to Popen", ret)
        si = subprocess.STARTUPINFO()
        si.dwFlags = subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        _CREATE_NEW_PROCESS_GROUP  = 0x00000200
        _CREATE_BREAKAWAY_FROM_JOB = 0x01000000
        try:
            subprocess.Popen(
                ["powershell.exe", "-WindowStyle", "Hidden",
                 "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(ps1_path),
                 "-ParentPid", str(pid),
                 "-SrcDir", str(actual_src),
                 "-DstDir", str(exe_dir),
                 "-ExePath", str(exe)],
                creationflags=(subprocess.CREATE_NO_WINDOW
                               | _CREATE_NEW_PROCESS_GROUP
                               | _CREATE_BREAKAWAY_FROM_JOB),
                startupinfo=si,
            )
        except OSError:
            subprocess.Popen(
                ["powershell.exe", "-WindowStyle", "Hidden",
                 "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(ps1_path),
                 "-ParentPid", str(pid),
                 "-SrcDir", str(actual_src),
                 "-DstDir", str(exe_dir),
                 "-ExePath", str(exe)],
                creationflags=(subprocess.CREATE_NO_WINDOW
                               | _CREATE_NEW_PROCESS_GROUP),
                startupinfo=si,
            )

    # ── 4. ダウンロード ZIP を削除 ───────────────────────────────────────
    try:
        zip_path.unlink(missing_ok=True)
    except OSError:
        pass

    # ── 5. プロセスを即座に終了して EXE ロックを解除 ────────────────────
    logger.info("Calling os._exit(0) to release EXE lock for PS1 updater")
    logging.shutdown()
    os._exit(0)

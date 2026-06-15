"""GitHub リリースからの自動アップデート機能"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, NamedTuple

import httpx

from ..constants import APP_VERSION, GITHUB_REPO

logger = logging.getLogger(__name__)


class ReleaseInfo(NamedTuple):
    version: str       # "0.3.0"
    tag: str           # "v0.3.0"
    download_url: str
    release_url: str


def _parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.lstrip("v").split("."))


def _ps_quote(path: Path) -> str:
    """PowerShell シングルクォート文字列用にパスをエスケープする ('→'')。"""
    return str(path).replace("'", "''")


async def check_for_update() -> ReleaseInfo | None:
    """GitHub Releases API で最新版を確認する。新しいバージョンがあれば ReleaseInfo を返す。"""
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
            logger.debug("最新版 v%s は現在版 v%s 以下。スキップ。", latest_ver, APP_VERSION)
            return None
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if name.endswith("-windows.zip"):
                return ReleaseInfo(
                    version=latest_ver,
                    tag=tag,
                    download_url=asset["browser_download_url"],
                    release_url=data.get("html_url", ""),
                )
    except Exception as e:
        logger.debug("アップデート確認失敗: %s", e)
    return None


async def download_update(
    release: ReleaseInfo,
    progress_cb: Callable[[float], None] | None = None,
) -> Path:
    """リリース zip をダウンロードして一時ファイルに保存し、そのパスを返す。"""
    tmp_path = Path(tempfile.mktemp(suffix=".zip", prefix="maf_update_"))
    logger.info("アップデート zip をダウンロード: %s → %s", release.download_url, tmp_path)
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
    """zip を展開し、PowerShell スクリプトでファイルを置き換えてから再起動する。

    Windows では実行中の EXE を直接上書きできないため、デタッチされた
    PowerShell スクリプト (_update.ps1) にファイル置き換えと再起動を委ねる。
    スクリプトは exe_dir (ASCII 保証) に書き出し、-File で起動する。
    """
    if not getattr(sys, "frozen", False):
        logger.warning("開発環境ではアップデートを適用できません")
        return

    exe = Path(sys.executable)
    exe_dir = exe.parent
    pid = os.getpid()

    # 展開先: exe_dir 直下に置く（インストール時に ASCII 検証済みのため安全）
    import shutil as _shutil
    extract_dir = exe_dir / "_upd_staging"
    if extract_dir.exists():
        _shutil.rmtree(extract_dir, ignore_errors=True)

    logger.info("zip を展開: %s → %s", zip_path, extract_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    # PowerShell スクリプトを exe_dir に書き出す
    # シングルクォートで囲み、バックスラッシュや日本語の問題を回避する
    ps_path = exe_dir / "_update.ps1"
    ps_content = "\n".join([
        f"$pid_val    = {pid}",
        f"$src        = '{_ps_quote(extract_dir)}'",
        f"$dst        = '{_ps_quote(exe_dir)}'",
        f"$exe_path   = '{_ps_quote(exe)}'",
        f"$zip_path   = '{_ps_quote(zip_path)}'",
        "$myself     = $MyInvocation.MyCommand.Path",
        "",
        "# 元プロセスが終了するまで待つ",
        "while (Get-Process -Id $pid_val -ErrorAction SilentlyContinue) {",
        "    Start-Sleep -Milliseconds 500",
        "}",
        "Start-Sleep -Seconds 2",
        "",
        "# ファイルを上書きコピー",
        "robocopy $src $dst /E /IS /IT /IM /NFL /NDL /NJH | Out-Null",
        "",
        "# 再起動",
        "Start-Process -FilePath $exe_path",
        "",
        "# 後片付け (少し待ってから削除)",
        "Start-Sleep -Seconds 5",
        "Remove-Item -Recurse -Force $src      -ErrorAction SilentlyContinue",
        "Remove-Item -Force          $zip_path -ErrorAction SilentlyContinue",
        "Remove-Item -Force          $myself   -ErrorAction SilentlyContinue",
    ]) + "\n"

    ps_path.write_text(ps_content, encoding="utf-8")

    # PowerShell 5.1 絶対パス（Windows 10/11 に常駐）
    ps_exe = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

    proc = subprocess.Popen(
        [
            ps_exe,
            "-NoProfile", "-NonInteractive",
            "-WindowStyle", "Hidden",
            "-ExecutionPolicy", "Bypass",
            "-File", str(ps_path),
        ],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
    )
    logger.info(
        "アップデートスクリプト起動 (PID=%d, updater_PID=%d): %s → %s",
        pid, proc.pid, extract_dir, exe_dir,
    )

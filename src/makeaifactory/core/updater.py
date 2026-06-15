"""GitHub リリースからの自動アップデート機能"""
from __future__ import annotations

import asyncio
import base64
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
    version: str       # "0.2.0"
    tag: str           # "v0.2.0"
    download_url: str
    release_url: str


def _parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.lstrip("v").split("."))


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
    """zip を展開し、PowerShell で現在のファイルを置き換えてから再起動する。

    Windows では実行中の EXE を直接上書きできないため、
    デタッチされた PowerShell スクリプトに処理を委ねてからアプリを終了する。
    """
    if not getattr(sys, "frozen", False):
        logger.warning("開発環境ではアップデートを適用できません")
        return

    exe = Path(sys.executable)
    exe_dir = exe.parent

    # zip を一時ディレクトリに展開
    extract_dir = Path(tempfile.mkdtemp(prefix="maf_upd_"))
    logger.info("zip を展開: %s → %s", zip_path, extract_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    pid = os.getpid()

    # PowerShell スクリプト: 元プロセス終了待ち → robocopy → 再起動 → 後片付け
    # extract_dir 内のパスが Unicode でも -EncodedCommand (UTF-16 LE) で正しく渡せる
    ps_script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$pid_val = {pid}
while (Get-Process -Id $pid_val -ErrorAction SilentlyContinue) {{
    Start-Sleep -Milliseconds 300
}}
Start-Sleep -Seconds 1
robocopy "{extract_dir}" "{exe_dir}" /E /IS /IT /IM /NFL /NDL /NJH | Out-Null
Start-Process "{exe}"
Remove-Item -Recurse -Force "{extract_dir}" -ErrorAction SilentlyContinue
Remove-Item -Force "{zip_path}" -ErrorAction SilentlyContinue
"""
    encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")

    subprocess.Popen(
        [
            "powershell", "-NoProfile", "-NonInteractive",
            "-WindowStyle", "Hidden",
            "-EncodedCommand", encoded,
        ],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        close_fds=True,
    )
    logger.info("アップデートスクリプトを起動しました (PID=%d 終了後に適用)。", pid)

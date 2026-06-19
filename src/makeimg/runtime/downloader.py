from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

import httpx

from ..domain.errors import DownloadError
from .hash_verifier import verify_sha256

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]


async def download_file(
    url: str,
    dest: Path,
    sha256: str = "",
    expected_size: int = 0,
    progress_cb: ProgressCallback | None = None,
    timeout: int = 3600,
) -> Path:
    """レジューム対応ダウンロード。.part ファイルへ保存後、検証してrenameする。"""
    part = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)

    existing_size = part.stat().st_size if part.exists() else 0
    headers = {}
    if existing_size > 0:
        headers["Range"] = f"bytes={existing_size}-"
        logger.info("レジューム: %s (%d bytes済み)", dest.name, existing_size)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code == 416:
                    logger.info("既存partファイルがサーバと一致、再検証します: %s", dest.name)
                elif resp.status_code not in (200, 206):
                    raise DownloadError(f"DL失敗 ({resp.status_code}): {url}")

                total = int(resp.headers.get("content-length", 0))
                if resp.status_code == 206:
                    total += existing_size

                mode = "ab" if resp.status_code == 206 else "wb"
                downloaded = existing_size
                with part.open(mode, buffering=0) as f:
                    async for chunk in resp.aiter_bytes(chunk_size=8 * 1024 * 1024):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb and total > 0:
                            progress_cb(downloaded, total)

    except httpx.HTTPError as e:
        raise DownloadError(f"ネットワークエラー: {e}") from e

    if sha256:
        verify_sha256(part, sha256)

    if expected_size > 0:
        actual_size = part.stat().st_size
        tolerance = max(expected_size * 0.01, 1024)
        if abs(actual_size - expected_size) > tolerance:
            part.unlink(missing_ok=True)
            raise DownloadError(
                f"サイズ不一致: {dest.name} (actual={actual_size}, expected={expected_size})"
            )

    part.replace(dest)
    logger.info("DL完了: %s", dest.name)
    return dest

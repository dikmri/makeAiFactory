from __future__ import annotations

import logging
from pathlib import Path

import httpx

from ..domain.errors import DownloadError

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 8 * 1024 * 1024  # 8MB


async def download_file(
    url: str,
    target_path: Path,
    progress_cb: callable | None = None,
    expected_size: int = 0,
) -> Path:
    """ファイルを直接ダウンロードする（ブラウザ不要）。

    Args:
        url: ダウンロードURL
        target_path: 保存先パス
        progress_cb: 進捗コールバック(downloaded_bytes, total_bytes)
        expected_size: 期待ファイルサイズ（0=検証なし）
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=3600.0, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            downloaded = 0

            with target_path.open("wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=_CHUNK_SIZE):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(downloaded, total)

    if expected_size > 0:
        actual_size = target_path.stat().st_size
        tolerance = max(expected_size * 0.01, 1024)
        if abs(actual_size - expected_size) > tolerance:
            target_path.unlink(missing_ok=True)
            raise DownloadError(
                f"サイズ不一致: {target_path.name} (actual={actual_size}, expected={expected_size})"
            )

    logger.info("ダウンロード完了: %s → %s (%.1f MB)", url, target_path, downloaded / (1024 * 1024))
    return target_path

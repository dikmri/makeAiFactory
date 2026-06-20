"""cloudflared.exe の自動ダウンロード・キャッシュ管理。

初回使用時に GitHub Releases から最新バイナリを取得し
runtime/cloudflared/cloudflared.exe にキャッシュする。
以降はキャッシュを再利用するためダウンロードは1回のみ。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from ..i18n import tr

logger = logging.getLogger(__name__)

CLOUDFLARED_DOWNLOAD_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-windows-amd64.exe"
)
_MIN_SIZE_BYTES = 10 * 1024 * 1024  # 実際は ~35 MB。それ未満は不完全とみなす。


def _cache_path(runtime_root: Path) -> Path:
    return runtime_root / "cloudflared" / "cloudflared.exe"


def _is_valid_exe(path: Path) -> bool:
    """Windows PE ヘッダー (MZ) を確認する。"""
    try:
        with path.open("rb") as f:
            return f.read(2) == b"MZ"
    except OSError:
        return False


def get_cached_cloudflared(runtime_root: Path) -> Path | None:
    """キャッシュ済みの cloudflared.exe が有効であればそのパスを返す。"""
    path = _cache_path(runtime_root)
    if path.exists() and path.stat().st_size >= _MIN_SIZE_BYTES and _is_valid_exe(path):
        return path
    return None


async def download_cloudflared(
    runtime_root: Path,
    on_progress: Callable[[str, float], None] | None = None,
) -> Path:
    """
    cloudflared.exe を GitHub Releases からダウンロードしてキャッシュする。
    on_progress(message, percent_0_100) で進捗を通知する。
    """
    import httpx

    dest = _cache_path(runtime_root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")

    def _notify(msg: str, pct: float) -> None:
        logger.debug("cloudflared DL: %s (%.0f%%)", msg, pct)
        if on_progress:
            on_progress(msg, pct)

    _notify(tr("cloudflared をダウンロード中..."), 0.0)
    logger.info("cloudflared ダウンロード開始: %s", CLOUDFLARED_DOWNLOAD_URL)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=180) as client:
            async with client.stream("GET", CLOUDFLARED_DOWNLOAD_URL) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length", 0))
                downloaded = 0

                with tmp.open("wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = downloaded / total * 100
                            mb_done = downloaded / 1024 / 1024
                            mb_total = total / 1024 / 1024
                            _notify(
                                tr("cloudflared をダウンロード中... {done:.1f}/{total:.1f} MB").format(
                                    done=mb_done, total=mb_total),
                                pct,
                            )
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            tr("cloudflared のダウンロードに失敗しました:\n{e}\n\n"
               "ネットワーク接続を確認してください。").format(e=e)
        ) from e

    # 検証
    size = tmp.stat().st_size
    if size < _MIN_SIZE_BYTES:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            tr("ダウンロードしたファイルが小さすぎます ({size:.1f} MB)。"
               "ネットワーク接続を確認してください。").format(size=size / 1024 / 1024)
        )
    if not _is_valid_exe(tmp):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(tr("ダウンロードしたファイルが有効な実行ファイルではありません。"))

    tmp.replace(dest)
    logger.info("cloudflared ダウンロード完了: %s (%.1f MB)", dest, dest.stat().st_size / 1024 / 1024)
    _notify(tr("cloudflared のダウンロードが完了しました ✓"), 100.0)
    return dest


async def ensure_cloudflared(
    runtime_root: Path,
    on_progress: Callable[[str, float], None] | None = None,
) -> Path:
    """
    cloudflared.exe のパスを返す。存在しなければ自動ダウンロードする。

    検索順:
    1. アプリ同梱バイナリ (PyInstaller _MEIPASS / resources/)
    2. PATH 上の cloudflared
    3. runtime/cloudflared/ キャッシュ
    4. GitHub Releases から自動ダウンロード → キャッシュ
    """
    from .tunnel_manager import find_cloudflared

    found = find_cloudflared()
    if found:
        return found

    cached = get_cached_cloudflared(runtime_root)
    if cached:
        logger.info("キャッシュ済み cloudflared を使用: %s", cached)
        return cached

    return await download_cloudflared(runtime_root, on_progress)

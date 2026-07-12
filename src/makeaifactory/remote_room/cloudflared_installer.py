"""cloudflared.exe の自動ダウンロード・キャッシュ管理。

初回使用時に GitHub Releases から既知バージョンのバイナリを取得し
runtime/cloudflared/cloudflared.exe にキャッシュする。
以降はキャッシュを再利用するためダウンロードは1回のみ。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from ..domain.errors import HashMismatchError
from ..i18n import tr
from ..runtime.hash_verifier import verify_sha256

logger = logging.getLogger(__name__)

# UPD-01対応: 以前は releases/latest を追跡していたため、アップストリームで
# バイナリが差し替えられても検知できなかった(サプライチェーン攻撃・
# ダウングレード・改竄への耐性が無かった)。既知バージョン + 期待SHA-256に
# 固定し、ダウンロード後に検証してから使用する。
#
# バージョンを更新する際は CLOUDFLARED_VERSION と CLOUDFLARED_SHA256 を
# 必ず両方更新すること。SHA-256は GitHub Releases API の該当アセットの
# digest フィールド(sha256:...)、または実ファイルを自前でハッシュ化して
# 取得する。
CLOUDFLARED_VERSION = "2026.7.1"
CLOUDFLARED_DOWNLOAD_URL = (
    f"https://github.com/cloudflare/cloudflared/releases/download/"
    f"{CLOUDFLARED_VERSION}/cloudflared-windows-amd64.exe"
)
# 上記バージョンの cloudflared-windows-amd64.exe の期待SHA-256。
# GitHub Releases API の digest フィールドから取得し、実ダウンロード結果の
# 自前ハッシュ計算とも一致することを確認済み。
# 値が空文字の場合は verify_sha256() が検証をスキップし警告ログのみ出す
# (フォールバック)。TODO: CLOUDFLARED_VERSION を更新した際はここも
# 必ず新しいdigestへ更新すること。
CLOUDFLARED_SHA256 = "ccb0756de288d3c2c076d19764ca53e0849a10f2dd9c23f8656ac42bdeb45001"
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

    # UPD-01: 使用前にSHA-256を検証する。CLOUDFLARED_SHA256が空の場合は
    # verify_sha256() 側で警告ログのみ出して検証をスキップする(フォールバック)。
    try:
        verify_sha256(tmp, CLOUDFLARED_SHA256)
    except HashMismatchError as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            tr("ダウンロードした cloudflared のSHA256が一致しません。"
               "ファイルが破損しているか改ざんされている可能性があります。")
        ) from e

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

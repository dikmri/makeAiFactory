from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

import httpx

from ..domain.errors import DownloadError, HashMismatchError
from ..i18n import tr
from .hash_verifier import verify_sha256

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]

# レジューム失敗時に「0から取り直し」を試みる最大試行回数(初回+再試行1回)
_MAX_ATTEMPTS = 2


def _parse_content_range_start(content_range: str | None) -> int | None:
    """Content-Rangeヘッダ(例: 'bytes 100-999/1000')から開始バイト位置を取得する。

    'bytes */1000' や未設定・不正な形式の場合は None を返す(=開始位置が特定できない)。
    """
    if not content_range:
        return None
    parts = content_range.strip().split(None, 1)
    if len(parts) != 2 or parts[0] != "bytes":
        return None
    range_part = parts[1].split("/", 1)[0]
    if range_part == "*" or "-" not in range_part:
        return None
    start_str, _, _end_str = range_part.partition("-")
    try:
        return int(start_str)
    except ValueError:
        return None


def _resume_plan(status_code: int, existing_size: int, content_range: str | None) -> tuple[str, bool]:
    """応答ステータスとContent-Rangeから、.partファイルへの書き込み方針を決定する(純関数)。

    戻り値は (書き込みmode, 0から取り直すか)。
      - mode == "wb":      応答本文を新規書き込みする(200: 全体をそのまま取得)
      - mode == "ab":      応答本文を既存.partへ追記する(206: レジューム継続)
      - mode == "verify":  応答本文は書き込まない。既存.partをそのまま検証する(416用)
      - mode == "restart": 応答本文は書き込まない。既存.partを破棄し、Range無しの
                           0からの取り直しにフォールバックする(206だが開始位置不一致)

    2番目の bool は「このmodeで得られる結果が既存.partと整合しない(=0から
    やり直す前提である)か」を表す。ab の場合のみ False。
    """
    if status_code == 416:
        # サーバは要求したRangeが既存リソースの範囲外だと回答した。
        # これは「既存.partが実は完全体である」可能性を示すため、
        # 応答ボディ(エラー内容)は書き込まず、既存.partをそのまま検証する。
        return "verify", True

    if status_code == 206:
        start = _parse_content_range_start(content_range)
        if start == existing_size:
            # サーバが期待通りの位置から返した → 安全に追記継続できる
            return "ab", False
        # サーバが期待と異なる位置から返した。応答本文は期待とずれたオフセットの
        # 断片である可能性があり、これを部分ファイルとして書き込むと(sha256未指定
        # なら)無検証の破損ファイルが残りかねない。既存.partを破棄して0からやり直す。
        return "restart", True

    # 200(またはその他の成功扱いの応答): 全体を新規取得
    return "wb", True


def _verify_existing_part(part: Path, sha256: str) -> bool:
    """既存.partファイルが指定sha256と一致するか検証する(副作用なし)。

    sha256が未指定の場合は検証不能として False を返す(=安全側で不一致扱い)。
    """
    if not sha256:
        return False
    try:
        verify_sha256(part, sha256)
        return True
    except HashMismatchError:
        return False


async def download_file(
    url: str,
    dest: Path,
    sha256: str = "",
    progress_cb: ProgressCallback | None = None,
    timeout: int = 3600,
) -> Path:
    """レジューム対応ダウンロード。.part ファイルへ保存後、検証してrenameする。"""
    part = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            # 416で既存.partの検証に失敗した場合のみ、Rangeヘッダ無しで1回だけ取り直す。
            for attempt in range(_MAX_ATTEMPTS):
                existing_size = part.stat().st_size if part.exists() else 0
                headers = {}
                if existing_size > 0:
                    headers["Range"] = f"bytes={existing_size}-"
                    logger.info("レジューム: %s (%d bytes済み)", dest.name, existing_size)

                async with client.stream("GET", url, headers=headers) as resp:
                    if resp.status_code not in (200, 206, 416):
                        raise DownloadError(
                            tr("DL失敗 ({status}): {url}").format(status=resp.status_code, url=url)
                        )

                    content_range = resp.headers.get("content-range")
                    mode, _needs_restart = _resume_plan(resp.status_code, existing_size, content_range)

                    if mode == "verify":
                        # 416: 既存.partは一切書き換えず、そのまま検証する。
                        logger.info("既存partファイルの完全性を検証します: %s", dest.name)
                        if _verify_existing_part(part, sha256):
                            part.replace(dest)
                            logger.info("DL完了(既存part再検証): %s", dest.name)
                            return dest
                        # 不一致、またはsha256未指定で検証不能 → 破棄して0から再取得
                        logger.warning("既存partの検証に失敗、0から再取得します: %s", dest.name)
                        part.unlink(missing_ok=True)
                        continue

                    if mode == "restart":
                        # 206だが応答開始位置が既存.partと不一致。応答本文は期待とずれた
                        # 断片の可能性があり信用できないため、既存.partを破棄して
                        # Rangeヘッダ無しの0からの取り直しにフォールバックする。
                        logger.warning(
                            "サーバ応答がレジューム前提と不一致のため0から取り直します: %s", dest.name
                        )
                        part.unlink(missing_ok=True)
                        continue

                    total = int(resp.headers.get("content-length", 0))
                    downloaded = existing_size if mode == "ab" else 0
                    if mode == "ab":
                        total += existing_size

                    with part.open(mode, buffering=0) as f:
                        async for chunk in resp.aiter_bytes(chunk_size=8 * 1024 * 1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_cb and total > 0:
                                progress_cb(downloaded, total)
                    break
            else:
                # 検証・再取得を試みても既存partを確定できなかった
                raise DownloadError(
                    tr("DL失敗: 既存partを検証・再取得できませんでした ({url})").format(url=url)
                )

    except httpx.HTTPError as e:
        raise DownloadError(tr("ネットワークエラー: {e}").format(e=e)) from e

    if sha256:
        try:
            verify_sha256(part, sha256)
        except HashMismatchError:
            # 壊れたpartを残さない
            part.unlink(missing_ok=True)
            raise

    part.replace(dest)
    logger.info("DL完了: %s", dest.name)
    return dest

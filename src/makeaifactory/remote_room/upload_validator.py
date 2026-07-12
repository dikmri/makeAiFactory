from __future__ import annotations

import asyncio
import io
import warnings

_MAX_TOTAL_PIXELS = 25_000_000    # 25 MP: これを超えたら縮小して受け入れる
_MAX_DECODE_PIXELS = 100_000_000  # 100 MP: これを超える画像はデコードせず拒否(圧縮爆弾対策)


def _validate_and_reencode(
    data: bytes,
    filename: str,
    max_mb: int,
    max_px: int,
    allowed_extensions: tuple[str, ...],
) -> tuple[bytes, str]:
    """アップロード画像を検証・再エンコードする(同期・重い処理)。

    成功時: (PNG バイト列, "")
    失敗時: (b"", ERROR_CODE)

    巨大画像や圧縮爆弾でメモリを枯渇させないため、Pillow のデコード前に
    宣言された寸法から総画素の絶対上限(_MAX_DECODE_PIXELS)を確認し、
    超えるものはデコードせずに拒否する。25MP〜上限の範囲は従来どおり縮小する。
    """
    if len(data) > max_mb * 1024 * 1024:
        return b"", "FILE_TOO_LARGE"

    from pathlib import Path
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in allowed_extensions:
        return b"", "INVALID_FILE_TYPE"

    from PIL import Image

    # Pillow 自体の decompression bomb ガードも有効化する
    prev_max = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = _MAX_DECODE_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            try:
                img = Image.open(io.BytesIO(data))
                w, h = img.size
                # デコード前に総画素の絶対上限を確認する
                if w * h > _MAX_DECODE_PIXELS:
                    return b"", "IMAGE_TOO_LARGE"
                img.verify()
                img = Image.open(io.BytesIO(data))
                img.load()
            except (Image.DecompressionBombError, Image.DecompressionBombWarning):
                return b"", "IMAGE_TOO_LARGE"
            except Exception:
                return b"", "INVALID_FILE_TYPE"

        w, h = img.size
        # max_px / 25MP を超える場合は縮小して受け入れる
        scale = min(max_px / w, max_px / h, (_MAX_TOTAL_PIXELS / (w * h)) ** 0.5, 1.0)
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

        output = io.BytesIO()
        img.convert("RGB").save(output, format="PNG")
        return output.getvalue(), ""
    finally:
        Image.MAX_IMAGE_PIXELS = prev_max


async def validate_upload(
    data: bytes,
    filename: str,
    max_mb: int,
    max_px: int,
    allowed_extensions: tuple[str, ...],
) -> tuple[bytes, str]:
    """アップロード画像を検証・サニタイズする。

    安価なサイズ確認は即時に行い、Pillow による decode / 再エンコードなどの
    重い処理は `asyncio.to_thread` へ逃がして aiohttp のイベントループを
    ブロックしないようにする。
    """
    if len(data) > max_mb * 1024 * 1024:
        return b"", "FILE_TOO_LARGE"
    return await asyncio.to_thread(
        _validate_and_reencode, data, filename, max_mb, max_px, allowed_extensions
    )

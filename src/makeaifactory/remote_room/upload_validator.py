from __future__ import annotations

import io

_MAX_TOTAL_PIXELS = 25_000_000  # 25 MP


async def validate_upload(
    data: bytes,
    filename: str,
    max_mb: int,
    max_px: int,
    allowed_extensions: tuple[str, ...],
) -> tuple[bytes, str]:
    """
    アップロード画像を検証・サニタイズする。
    成功時: (PNG バイト列, "")
    失敗時: (b"", ERROR_CODE)
    """
    if len(data) > max_mb * 1024 * 1024:
        return b"", "FILE_TOO_LARGE"

    from pathlib import Path
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in allowed_extensions:
        return b"", "INVALID_FILE_TYPE"

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return b"", "INVALID_FILE_TYPE"

    w, h = img.size
    # 大きすぎる画像はエラーにせず、制限に収まるよう縮小する
    # (ブラウザ連携などで高解像度画像をそのまま受け取れるようにするため)
    from PIL import Image as _Image
    scale = min(max_px / w, max_px / h, (_MAX_TOTAL_PIXELS / (w * h)) ** 0.5, 1.0)
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), _Image.LANCZOS)

    output = io.BytesIO()
    clean = img.convert("RGB")
    clean.save(output, format="PNG")
    return output.getvalue(), ""

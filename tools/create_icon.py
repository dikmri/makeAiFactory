"""makeAiFactory アイコン生成スクリプト"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZES = [16, 32, 48, 64, 128, 256]
OUT = Path(__file__).parent.parent / "assets" / "icon.ico"
OUT.parent.mkdir(parents=True, exist_ok=True)

BG      = (15,  15,  26)   # #0f0f1a
BLUE1   = (21,  101, 192)  # #1565c0
BLUE2   = (25,  118, 210)  # #1976d2
CYAN    = (79,  195, 247)  # #4fc3f7
PURPLE  = (123, 31,  162)  # #7b1fa2
WHITE   = (238, 238, 238)


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size

    # ── 背景: 丸みのある正方形 ──────────────────────
    r = s // 6
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=BG)

    # ── フィルムフレーム外枠 ─────────────────────────
    pad = s * 0.08
    fw = s - pad * 2
    fh = fw * 0.65
    fx = pad
    fy = (s - fh) / 2

    d.rounded_rectangle(
        [fx, fy, fx + fw, fy + fh],
        radius=max(2, s // 20),
        fill=None,
        outline=BLUE2,
        width=max(1, s // 28),
    )

    # ── フィルムスプロケット穴（左右）──────────────
    hole_r = max(1, s // 24)
    hole_pad = s * 0.13
    for row in (0.28, 0.5, 0.72):
        cy = fy + fh * row
        for cx in (hole_pad, s - hole_pad):
            d.ellipse(
                [cx - hole_r, cy - hole_r, cx + hole_r, cy + hole_r],
                fill=BLUE2,
            )

    # ── 中央: 三角形（再生ボタン / AI spark）─────────
    cx, cy = s / 2, s / 2
    tri_h = fh * 0.48
    tri_w = tri_h * 0.85
    pts = [
        (cx - tri_w / 2, cy - tri_h / 2),
        (cx - tri_w / 2, cy + tri_h / 2),
        (cx + tri_w / 2, cy),
    ]
    d.polygon(pts, fill=CYAN)

    # ── 三角形の上に小さなスパーク（AI感） ──────────
    if size >= 48:
        spark_r = max(1, s // 20)
        d.ellipse(
            [cx + tri_w / 2 - spark_r, cy - tri_h / 2 - spark_r,
             cx + tri_w / 2 + spark_r, cy - tri_h / 2 + spark_r],
            fill=PURPLE,
        )

    # ── グロー効果（大サイズのみ）───────────────────
    if size >= 64:
        glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.polygon(pts, fill=(*CYAN, 60))
        glow = glow.filter(ImageFilter.GaussianBlur(radius=s // 12))
        img = Image.alpha_composite(img, glow)

    return img


# 各サイズを個別に PNG として保存し、struct で ICO フォーマットに結合
import io
import struct

def make_ico(images: list[Image.Image]) -> bytes:
    """PNG エントリの ICO バイナリを生成する（Vista+ 対応）"""
    pngs: list[bytes] = []
    for img in images:
        buf = io.BytesIO()
        img.convert("RGBA").save(buf, format="PNG")
        pngs.append(buf.getvalue())

    n = len(pngs)
    # ICONDIR header (6 bytes) + ICONDIRENTRY × n (16 bytes each)
    data_offset = 6 + 16 * n
    header = struct.pack("<HHH", 0, 1, n)
    entries = b""
    chunks = b""
    for png, img in zip(pngs, images):
        w, h = img.size
        bw = w if w < 256 else 0   # 256 → 0 in ICO spec
        bh = h if h < 256 else 0
        entry = struct.pack("<BBBBHHII",
                            bw, bh,   # width, height
                            0, 0,     # color count, reserved
                            1, 32,    # planes, bit count
                            len(png), data_offset)
        entries += entry
        data_offset += len(png)
        chunks += png
    return header + entries + chunks

big = draw_icon(256)
images = [big.resize((sz, sz), Image.LANCZOS) for sz in SIZES]
ico_bytes = make_ico(images)
OUT.write_bytes(ico_bytes)
print(f"Icon generated: {OUT}  ({len(ico_bytes)/1024:.1f} KB)")

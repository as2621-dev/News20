"""Build a single side-by-side montage PNG: Pro | Flash 3.1 | 2.5 Flash, 5 rows.

    .venv/bin/python scripts/build_compare_montage.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
M0 = ROOT / "assets" / "m0"
CMP = M0 / "flash-compare"

DIGESTS = ["digest-1", "digest-2", "digest-3", "digest-4", "digest-5"]
COLS = [
    ("Nano Banana PRO\n(gemini-3-pro-image)", lambda d: M0 / d / "poster.png"),
    ("FLASH 3.1\n(gemini-3.1-flash-image)", lambda d: CMP / f"{d}.png"),
    ("2.5 FLASH\n(gemini-2.5-flash-image)", lambda d: CMP / "cheap" / f"{d}.png"),
]

CELL_W = 380
CELL_H = round(CELL_W * 16 / 9)
PAD = 14
HEADER_H = 70
ROWLABEL_W = 92
BG = (11, 11, 13)
FG = (231, 231, 234)
MUTED = (150, 160, 170)


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
        "/Library/Fonts/Arial.ttf",
    ):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def main() -> None:
    ncols = len(COLS)
    grid_w = ROWLABEL_W + ncols * CELL_W + (ncols + 1) * PAD
    grid_h = HEADER_H + len(DIGESTS) * CELL_H + (len(DIGESTS) + 1) * PAD
    canvas = Image.new("RGB", (grid_w, grid_h), BG)
    draw = ImageDraw.Draw(canvas)
    hfont = _font(17)
    sfont = _font(13)
    rfont = _font(15)

    # column headers
    for ci, (label, _) in enumerate(COLS):
        x = ROWLABEL_W + PAD + ci * (CELL_W + PAD)
        for li, line in enumerate(label.split("\n")):
            f = hfont if li == 0 else sfont
            col = FG if li == 0 else MUTED
            draw.text((x + 6, 10 + li * 22), line, fill=col, font=f)

    for ri, d in enumerate(DIGESTS):
        y = HEADER_H + PAD + ri * (CELL_H + PAD)
        draw.text((8, y + CELL_H // 2 - 8), d.replace("digest-", "#"), fill=MUTED, font=rfont)
        for ci, (_, pathfn) in enumerate(COLS):
            x = ROWLABEL_W + PAD + ci * (CELL_W + PAD)
            p = pathfn(d)
            if p.exists():
                img = Image.open(p).convert("RGB").resize((CELL_W, CELL_H))
                canvas.paste(img, (x, y))
            else:
                draw.rectangle([x, y, x + CELL_W, y + CELL_H], outline=(80, 60, 60))
                draw.text((x + 20, y + CELL_H // 2), "missing", fill=(248, 113, 113), font=sfont)

    out = CMP / "montage_3way.png"
    canvas.save(out, format="PNG")
    print(f"WROTE {out}  ({grid_w}x{grid_h})")


if __name__ == "__main__":
    main()

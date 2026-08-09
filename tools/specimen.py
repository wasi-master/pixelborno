#!/usr/bin/env python3
"""Render the specimen sheets in specimens/, for the README and for eyeballing.

Every sheet is drawn at a whole multiple of the face's own pixel size and then
scaled up with nearest-neighbour, so what you see is the grid the font is drawn
on rather than a resampling of it. Rendering at an in-between size is the one
sure way to make a pixel font look like a blurry ordinary font.

    python3 tools/specimen.py dist/Pixelborno-Regular.ttf
"""

from __future__ import annotations

import argparse
import os
import sys

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Bengali is checked here the way a Bengali reader would check it: the alphabet
# in its traditional order, every vowel sign on the same consonant, the
# conjuncts that a naive font gets wrong, and a sentence that has to look like a
# sentence. The Banglish line matters because a Bengali keyboard types it all
# day and a font that only covers one script splits every such line in two.
SHEETS = {
    "alphabet": [
        "অ আ ই ঈ উ ঊ ঋ এ ঐ ও ঔ",
        "ক খ গ ঘ ঙ  চ ছ জ ঝ ঞ",
        "ট ঠ ড ঢ ণ  ত থ দ ধ ন",
        "প ফ ব ভ ম  য র ল শ ষ স হ",
        "ড় ঢ় য় ৎ ং ঃ ঁ ঽ ্",
        "০ ১ ২ ৩ ৪ ৫ ৬ ৭ ৮ ৯  ৳ ।",
    ],
    "shaping": [
        "কা কি কী কু কূ কৃ কে কৈ কো কৌ",
        "ক্ষ  জ্ঞ  ঞ্চ  ঞ্জ  ন্ত  ন্ধ  ন্ন",
        "স্ত  স্থ  ষ্ট  ষ্ঠ  ম্ব  ল্ল  হ্ম",
        "ত্র  ক্র  গ্র  প্র  ব্র  ভ্র  শ্র",
        "র্ক  র্ম  র্থ  ক্য  ব্য  স্ব  দ্ব",
    ],
    "text": [
        "আমি বাংলায় গান গাই।",
        "শুভ জন্মদিন, বন্ধু!",
        "বাংলা আমার মাতৃভাষা।",
        "দাম ৳৫০০ — ছাড় ২৫%",
    ],
    "latin": [
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "abcdefghijklmnopqrstuvwxyz",
        "0123456789 !?.,:;'\"()[]{}",
        "Banglish: ami bhalo achi.",
        "Mixed: আমি Wasi, বয়স 25.",
    ],
}


def pixel_size(path: str) -> int:
    """The face's em in pixels, read off the grid its outlines sit on.

    Every coordinate in the font is a multiple of one pixel, so the greatest
    common divisor of the outlines is that pixel — no need to be told.
    """
    from math import gcd
    font = TTFont(path)
    upem = font["head"].unitsPerEm
    glyf = font["glyf"]
    divisor = 0
    for name in list(font.getGlyphOrder())[:400]:
        glyph = glyf[name]
        if glyph.numberOfContours <= 0:
            continue
        for x, y in glyph.coordinates:
            if x:
                divisor = gcd(divisor, abs(int(x)))
            if y:
                divisor = gcd(divisor, abs(int(y)))
    return upem // divisor if divisor else 20


def sheet(path: str, lines: list[str], out: str, em_px: int, scale: int,
          title: str) -> None:
    face = ImageFont.truetype(path, em_px)
    label = ImageFont.load_default(size=11)
    line_h = int(em_px * 1.6)
    width = int(max(face.getlength(line) for line in lines)) + 16
    img = Image.new("RGB", (width, 20 + len(lines) * line_h + 6),
                    (0xF6, 0xF7, 0xF4))
    draw = ImageDraw.Draw(img)
    draw.text((8, 5), title, font=label, fill=(0x8A, 0x8F, 0x88))
    y = 20
    for line in lines:
        draw.text((8, y), line, font=face, fill=(0x1A, 0x1C, 0x19))
        y += line_h
    img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    img.save(out)
    print(f"  {out}  {img.width}x{img.height}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("font", nargs="?",
                    default=os.path.join(ROOT, "dist", "Pixelborno-Regular.ttf"))
    ap.add_argument("--out", default=os.path.join(ROOT, "specimens"))
    ap.add_argument("--scale", type=int, default=3)
    args = ap.parse_args()

    if not os.path.exists(args.font):
        print(f"build it first: {args.font}", file=sys.stderr)
        return 2
    os.makedirs(args.out, exist_ok=True)
    em_px = pixel_size(args.font)
    print(f"{args.font}: {em_px}px em, drawn at x{args.scale}")
    for name, lines in SHEETS.items():
        sheet(args.font, lines, os.path.join(args.out, f"{name}.png"),
              em_px, args.scale, f"Pixelborno · {name} · {em_px}px em")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

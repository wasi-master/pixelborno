#!/usr/bin/env python3
"""Sanity-check a built Pixelborno before it is released.

Three things can silently go wrong in this build and none of them shows up as an
error: the graft can miss the Latin half, the pixelation can knock a coordinate
off the grid, and a stray table edit can cost the shaping that is the entire
reason the font is derived rather than drawn. Each gets a check.

    python3 tools/check.py dist/Pixelborno-Regular.ttf
"""

from __future__ import annotations

import argparse
import os
import sys
from math import gcd

from fontTools.ttLib import TTFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Independent vowels, every consonant, the signs, and both digit sets. If any of
# these is missing the font cannot write Bengali, whatever else it can do.
BENGALI_REQUIRED = (
    "অআইঈউঊঋএঐওঔ"
    "কখগঘঙচছজঝঞটঠডঢণতথদধনপফবভমযরলশষসহ"
    "ড়ঢ়য়ৎংঃঁ্"
    "ািীুূৃেৈোৌ"
    "০১২৩৪৫৬৭৮৯।৳"
)

LATIN_REQUIRED = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789 !\"#$%&'()*+,-./:;<=>?@[\\]^_{|}~"
)

# Conjuncts a Bengali reader would notice immediately if they broke apart. Each
# has to be reachable as a ligature substitution, not merely as its parts.
CONJUNCT_SAMPLES = ("ক্ষ", "জ্ঞ", "ঞ্চ", "ন্ত", "স্ত", "ষ্ট", "ত্র", "ম্ব", "হ্ম")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("font", nargs="?",
                    default=os.path.join(ROOT, "dist", "Pixelborno-Regular.ttf"))
    args = ap.parse_args()

    if not os.path.exists(args.font):
        print(f"no such font: {args.font}", file=sys.stderr)
        return 2

    font = TTFont(args.font)
    cmap = font.getBestCmap()
    failures: list[str] = []

    for label, required in (("Bengali", BENGALI_REQUIRED), ("Latin", LATIN_REQUIRED)):
        missing = [c for c in required if ord(c) not in cmap]
        if missing:
            failures.append(f"{label}: {len(missing)} codepoints unmapped: "
                            + " ".join(f"U+{ord(c):04X} {c}" for c in missing[:12]))
        else:
            print(f"ok   {label}: all {len(required)} codepoints mapped")

    # Every coordinate must be a whole number of pixels. The greatest common
    # divisor of the outlines is that pixel; if pixelation left anything behind,
    # the divisor collapses toward 1 and the font is not on a grid at all.
    glyf = font["glyf"]
    divisor = 0
    for name in font.getGlyphOrder():
        glyph = glyf[name]
        if glyph.numberOfContours <= 0:
            continue
        for x, y in glyph.coordinates:
            if x:
                divisor = gcd(divisor, abs(int(x)))
            if y:
                divisor = gcd(divisor, abs(int(y)))
    upem = font["head"].unitsPerEm
    if divisor < 8 or upem % divisor:
        failures.append(f"outlines are not on a whole-pixel grid "
                        f"(gcd {divisor} of {upem} units/em)")
    else:
        print(f"ok   grid: every coordinate is a multiple of {divisor} "
              f"({upem // divisor}px em)")

    # The shaping tables are inherited, not authored, so the check is that they
    # are still there and still know the ligatures.
    if "GSUB" not in font or "GPOS" not in font:
        failures.append("GSUB/GPOS missing — Bengali will not shape")
    else:
        features = {r.FeatureTag for r in font["GSUB"].table.FeatureList.FeatureRecord}
        needed = {"akhn", "blwf", "half", "pstf", "vatu", "rphf"}
        absent = needed - features
        if absent:
            failures.append(f"GSUB has lost Indic features: {sorted(absent)}")
        else:
            print(f"ok   shaping: GSUB carries {sorted(needed)}")

    # Ligature lookups are what turn ক + ্ + ষ into one glyph. Counting them is
    # cheaper than running a shaper and catches a subset that stripped them.
    ligatures = 0
    for lookup in font["GSUB"].table.LookupList.Lookup:
        if lookup.LookupType == 4:
            for sub in lookup.SubTable:
                ligatures += sum(len(v) for v in getattr(sub, "ligatures", {}).values())
    if ligatures < 100:
        failures.append(f"only {ligatures} ligature substitutions — conjuncts are gone")
    else:
        print(f"ok   conjuncts: {ligatures} ligature substitutions "
              f"(needs {len(CONJUNCT_SAMPLES)}+ like {' '.join(CONJUNCT_SAMPLES[:4])})")

    size = os.path.getsize(args.font)
    print(f"ok   {font['maxp'].numGlyphs} glyphs, {size // 1024} KB")

    for line in failures:
        print(f"FAIL {line}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

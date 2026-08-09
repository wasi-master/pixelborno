#!/usr/bin/env python3
"""Build Pixelborno — a pixel-grid Bengali + Latin face, from two OFL sources.

Bengali had no pixel font anyone could ship. The ones that exist are freeware
with no redistribution grant, and drawing one is not a matter of drawing fifty
letters: Bengali fuses consonants into hundreds of conjuncts, hangs vowel signs
on all four sides of a cluster, and lifts র to the top of the syllable it began.
That knowledge lives in a font's GSUB and GPOS tables and is a typographer's
year of work.

So Pixelborno is not drawn. It is two fonts that already know all of it, with
every outline replaced by its own pixelated silhouette on one shared grid:

  * Bengali, digits and দাঁড়ি   — Noto Sans Bengali (OFL-1.1, Google)
  * Latin, ASCII punctuation     — Inter (OFL-1.1, Rasmus Andersson)

Both are redrawn by tools/pixelate_font.py at the same grid, anchored at the
font origin, so a line mixing Banglish and Bengali sits on one lattice. The
Bengali font keeps its shaping tables untouched, which is the whole trick: the
result renders ক্ষ and র্ক correctly and merely looks like it was built out of
blocks.

The source weight is 500 rather than 400 on purpose, and not 600 either. At
twenty pixels to the em a 400-weight stem covers about 1.2 pixels, so it rounds
to one pixel here and two there and the letters come out visibly ragged; 500
clears 1.5 nearly everywhere and lands on two, which is what makes the result
read as drawn on a grid rather than as a photograph of a font. 600 goes one step
too far and closes the counters of ঘ, ভ, ম and ধ, which at this size is the
difference between a letter and a blob.

    python3 tools/build.py --out dist/Pixelborno-Regular.ttf

Both sources are vendored in sources/ rather than downloaded. The OFL exists to
permit exactly that, and a build that fetches its inputs is a build that stops
reproducing the day an upstream tag moves. Pass --bengali/--latin to point
somewhere else.
"""

from __future__ import annotations

import argparse
import os
import sys

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.ttLib.scaleUpem import scale_upem
from fontTools.varLib import instancer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pixelate_font import STALE_TABLES, pixelate_glyph, rename, snap, snap_gpos  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(ROOT, "sources")
DEFAULT_BENGALI = os.path.join(SOURCES, "NotoSansBengali-VF.ttf")
DEFAULT_LATIN = os.path.join(SOURCES, "Inter-VF.ttf")

# What we take from the Latin side. Basic Latin covers the keyboard's own labels
# and Banglish; the Latin-1 letters cost almost nothing and stop a European name
# in a Bengali sentence from turning into boxes. The typographic quotes and
# dashes are here because the keyboard's own punctuation key produces them.
LATIN_CODEPOINTS = (
    list(range(0x0020, 0x007F))
    + list(range(0x00A0, 0x0100))
    + [0x2010, 0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2026,
       0x20B9]
)

# 1000 units/em with a 50-unit pixel is twenty pixels to the em: crisp at 20, 40
# and 60 px, and the coarsest grid on which a Bengali conjunct still reads. 40
# (25 px/em) is legible but stops looking deliberate; 84 (12 px/em) closes the
# counters of ঘ and ভ.
DEFAULT_PIXEL = 50
DEFAULT_WEIGHT = 500


def static(path: str, weight: int) -> TTFont:
    """One weight of a variable font, or the font itself if it has no axes."""
    font = TTFont(path)
    if "fvar" not in font:
        return font
    axes = {a.axisTag: a for a in font["fvar"].axes}
    location = {}
    if "wght" in axes:
        a = axes["wght"]
        location["wght"] = min(max(weight, a.minValue), a.maxValue)
    # Inter's optical-size axis: the small end is the one drawn for text, and it
    # is the one whose proportions survive a coarse grid.
    if "opsz" in axes:
        location["opsz"] = axes["opsz"].minValue
    instancer.instantiateVariableFont(font, location, inplace=True,
                                      updateFontNames=False)
    return font


def pixelate(font: TTFont, pixel: float) -> TTFont:
    """Redraw every glyph on the grid, in place. See tools/pixelate_font.py."""
    glyph_set = font.getGlyphSet()
    glyf = font["glyf"]
    hmtx = font["hmtx"]
    for name in font.getGlyphOrder():
        glyph = pixelate_glyph(glyph_set, name, pixel, glyf).glyph()
        glyph.recalcBounds(glyf)
        glyf[name] = glyph
        lsb = glyph.xMin if glyph.numberOfContours else 0
        width, _ = hmtx[name]
        hmtx[name] = (max(int(pixel), snap(width, pixel)) if width else 0, lsb)
    snap_gpos(font, pixel)
    return font


def graft(base: TTFont, donor: TTFont, codepoints) -> int:
    """Copy the donor's glyphs for [codepoints] the base cannot draw.

    Only outlines, widths and cmap entries move. The base's GSUB and GPOS
    describe Bengali and have nothing to say about Latin, and the donor's
    describe a font whose glyph names are about to change — carrying either
    across would be carrying a rulebook for the wrong glyphs.
    """
    base_cmap = base.getBestCmap()
    donor_cmap = donor.getBestCmap()
    base_glyf, base_hmtx = base["glyf"], base["hmtx"]
    donor_glyf, donor_hmtx = donor["glyf"], donor["hmtx"]
    taken = set(base.getGlyphOrder())
    added: dict[int, str] = {}

    for cp in codepoints:
        if cp in base_cmap or cp not in donor_cmap:
            continue
        source = donor_cmap[cp]
        # A donor name may already exist in the base under a different meaning;
        # the base's tables refer to it by name, so the newcomer is the one that
        # has to move.
        name = source if source not in taken else f"latin.{source}"
        while name in taken:
            name += "_"
        # Assigning into glyf appends to its glyph order for us — appending by
        # hand as well would list every newcomer twice, since the font and the
        # table share one list.
        base_glyf[name] = donor_glyf[source]
        base_hmtx[name] = donor_hmtx[source]
        taken.add(name)
        added[cp] = name

    if not added:
        return 0
    base.setGlyphOrder(base_glyf.glyphOrder)
    base["maxp"].numGlyphs = len(base_glyf.glyphOrder)
    for table in base["cmap"].tables:
        # Only the Unicode subtables; a legacy Mac table cannot hold these and
        # writing into it would corrupt the mapping it does hold.
        if table.isUnicode():
            table.cmap.update(added)
    # Unclassified glyphs make GDEF's mark filtering guess. Latin letters are
    # base glyphs, and saying so keeps Bengali's mark attachment from ever
    # considering them.
    gdef = base.get("GDEF")
    classes = getattr(getattr(gdef, "table", None), "GlyphClassDef", None)
    if classes is not None:
        for name in added.values():
            classes.classDefs[name] = 1
    return len(added)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bengali", default=DEFAULT_BENGALI)
    ap.add_argument("--latin", default=DEFAULT_LATIN)
    ap.add_argument("--out", default=os.path.join(ROOT, "dist",
                                                  "Pixelborno-Regular.ttf"))
    ap.add_argument("--pixel", type=int, default=DEFAULT_PIXEL)
    ap.add_argument("--weight", type=int, default=DEFAULT_WEIGHT)
    ap.add_argument("--family", default="Pixelborno")
    ap.add_argument("--version", default="1.000")
    args = ap.parse_args()

    for path in (args.bengali, args.latin):
        if not os.path.exists(path):
            print(f"missing source font: {path}", file=sys.stderr)
            return 2

    pixel = float(args.pixel)

    print(f"bengali  {args.bengali}")
    base = static(args.bengali, args.weight)
    upem = base["head"].unitsPerEm
    pixelate(base, pixel)
    print(f"  {base['maxp'].numGlyphs} glyphs on a {upem // args.pixel}px em")

    print(f"latin    {args.latin}")
    donor = static(args.latin, args.weight)
    options = subset.Options()
    options.glyph_names = True
    options.layout_features = []
    options.notdef_outline = True
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=LATIN_CODEPOINTS)
    subsetter.subset(donor)
    if donor["head"].unitsPerEm != upem:
        # Both sides have to measure a pixel the same way before either is drawn
        # on the grid.
        scale_upem(donor, upem)
    pixelate(donor, pixel)
    grafted = graft(base, donor, LATIN_CODEPOINTS)
    print(f"  {grafted} Latin glyphs grafted on")

    rename(base, args.family, args.version, "https://openfontlicense.org")
    base["name"].setName(
        "Pixelborno is a pixel-grid redrawing of Noto Sans Bengali (Google) and "
        "Inter (Rasmus Andersson), both under the SIL Open Font License 1.1. "
        "Every outline is replaced; the shaping tables are the originals'.",
        10, 3, 1, 0x409)
    base["name"].setName("https://github.com/wasi-master/pixelborno", 11, 3, 1, 0x409)
    os2 = base["OS/2"]
    # Rounding every stem up to a whole pixel adds weight that the axis value
    # no longer describes: a pixelated 500 sits where a bold does.
    os2.usWeightClass = 700
    for tag in STALE_TABLES:
        if tag in base:
            del base[tag]
    base["head"].flags &= ~0x0008

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    base.save(args.out)
    size = os.path.getsize(args.out)
    print(f"wrote {args.out}  ({size // 1024} KB, {base['maxp'].numGlyphs} glyphs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

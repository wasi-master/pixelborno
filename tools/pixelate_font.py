#!/usr/bin/env python3
"""Redraw a font's glyphs on a pixel grid, keeping everything else it knows.

Bengali is why this exists. A pixel display face for the keyboard has to draw
কখগ as well as ABC, and the pixel faces that do exist are freeware with no
redistribution grant. Drawing 400 Bengali glyphs by hand is not the problem
either — the conjuncts are. Bengali joins consonants into hundreds of ligatures,
hangs vowel signs on four sides of a cluster, and moves র to the top of the
syllable it started after. All of that lives in a font's GSUB and GPOS tables,
and getting it right is a typographer's year, not an afternoon.

So this doesn't draw a font. It takes one that already knows all of that — Noto
Sans Bengali, under the OFL, which permits exactly this — and replaces every
glyph *outline* with a pixelated copy of itself. The cmap, the ligature
substitutions, the mark positioning and the glyph order all survive untouched,
so the result shapes Bengali correctly and merely looks like it was drawn in
Minecraft. Latin, digits and punctuation come along the same way.

The grid is anchored at the font's own origin rather than each glyph's bounding
box, so every glyph lands on the same lattice and a line of text reads as one
pixel image instead of a row of independently-rounded ones. Advances, mark
anchors and kerning are snapped to that lattice for the same reason.

Usage:
    python3 tools/pixelate_font.py IN.ttf OUT.ttf --pixel 40 \\
        --family "Pixelborno" --version 1.000

--pixel is in font units. At the usual 1000 units/em, 40 gives a 25-pixel em,
which is about the coarsest grid Bengali conjuncts survive; 50 (20 px/em) is
blockier and starts losing the difference between ঘ and য.
"""

from __future__ import annotations

import argparse
import math
import sys
from typing import Iterable

import numpy as np
from fontTools.pens.basePen import BasePen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

# Tables that describe the outlines we are about to throw away. Hinting a
# pixel grid is meaningless, and a stale hdmx makes the rasteriser trust widths
# that no longer exist.
STALE_TABLES = ("fpgm", "prep", "cvt ", "hdmx", "LTSH", "VDMX", "gasp", "TSI0",
                "TSI1", "TSI2", "TSI3", "TSI5", "cvar", "MVAR", "HVAR", "VVAR",
                "gvar", "avar", "fvar", "STAT")

# Samples per pixel per axis when measuring how much of a cell the outline
# covers. 4 means 16 samples a cell, which is enough to make the on/off call
# stable without turning a 3000-glyph font into a coffee break.
SUPERSAMPLE = 4

# How much of a cell has to be inside the outline for the pixel to be on.
# Half is the honest reading of "is this pixel part of the letter", and it is
# what keeps stroke weights even: a stem that covers 60% of two adjacent
# columns should be two pixels wide, not four at 30% or none at all.
COVERAGE_ON = 0.5

# Curve flattening. Bengali is nearly all curves and the grid is coarse, so
# the segment count only has to beat the pixel size, not the eye.
CURVE_STEPS = 12


class FlattenPen(BasePen):
    """Collects a glyph as closed polygons, every curve reduced to segments."""

    def __init__(self, glyphSet):
        super().__init__(glyphSet)
        self.contours: list[list[tuple[float, float]]] = []
        self._current: list[tuple[float, float]] = []

    def _moveTo(self, pt):
        self._flushContour()
        self._current = [pt]

    def _lineTo(self, pt):
        self._current.append(pt)

    def _curveToOne(self, p1, p2, p3):
        p0 = self._current[-1] if self._current else p1
        for i in range(1, CURVE_STEPS + 1):
            t = i / CURVE_STEPS
            u = 1.0 - t
            x = (u * u * u * p0[0] + 3 * u * u * t * p1[0]
                 + 3 * u * t * t * p2[0] + t * t * t * p3[0])
            y = (u * u * u * p0[1] + 3 * u * u * t * p1[1]
                 + 3 * u * t * t * p2[1] + t * t * t * p3[1])
            self._current.append((x, y))

    def _closePath(self):
        self._flushContour()

    def _endPath(self):
        self._flushContour()

    def _flushContour(self):
        if len(self._current) >= 3:
            self.contours.append(self._current)
        self._current = []

    def done(self) -> list[list[tuple[float, float]]]:
        self._flushContour()
        return self.contours


def edges_of(contours: Iterable[Iterable[tuple[float, float]]]) -> np.ndarray:
    """Every polygon edge as (x0, y0, x1, y1), horizontals dropped.

    A horizontal edge can never be crossed by a horizontal scan line, and
    keeping it only invites division by zero.
    """
    rows: list[tuple[float, float, float, float]] = []
    for contour in contours:
        pts = list(contour)
        for i in range(len(pts)):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % len(pts)]
            if y0 != y1:
                rows.append((x0, y0, x1, y1))
    if not rows:
        return np.zeros((0, 4), dtype=np.float64)
    return np.asarray(rows, dtype=np.float64)


def rasterise(contours, x0: float, y0: float, cols: int, rows: int,
              pixel: float) -> np.ndarray:
    """Coverage per cell of a cols×rows grid whose corner sits at (x0, y0).

    Non-zero winding, sampled on a SUPERSAMPLE×SUPERSAMPLE lattice inside each
    cell. Returns a boolean array indexed [row, col] with row 0 at the bottom,
    which is the direction font coordinates already run in.
    """
    edges = edges_of(contours)
    if len(edges) == 0:
        return np.zeros((rows, cols), dtype=bool)

    ex0, ey0, ex1, ey1 = edges[:, 0], edges[:, 1], edges[:, 2], edges[:, 3]
    direction = np.where(ey1 > ey0, 1.0, -1.0)
    ylo = np.minimum(ey0, ey1)
    yhi = np.maximum(ey0, ey1)
    slope = (ex1 - ex0) / (ey1 - ey0)

    step = pixel / SUPERSAMPLE
    half = step / 2.0
    # Sample x positions for the whole row at once: one column of the grid is
    # SUPERSAMPLE samples wide, so this is the full scan line.
    sx = x0 + half + step * np.arange(cols * SUPERSAMPLE, dtype=np.float64)

    hits = np.zeros((rows, cols), dtype=np.int32)
    for r in range(rows):
        for s in range(SUPERSAMPLE):
            sy = y0 + r * pixel + half + s * step
            # Half-open in y so a vertex shared by two edges is counted once.
            live = (ylo <= sy) & (yhi > sy)
            if not live.any():
                continue
            xs = ex0[live] + (sy - ey0[live]) * slope[live]
            ds = direction[live]
            order = np.argsort(xs, kind="stable")
            xs, ds = xs[order], ds[order]
            winding = np.cumsum(ds)
            inside = winding != 0
            if not inside.any():
                continue
            # Between crossing i and i+1 the winding is winding[i]; a sample is
            # painted when it falls in a span whose winding is non-zero.
            starts = xs[:-1][inside[:-1]]
            ends = xs[1:][inside[:-1]]
            if len(starts) == 0:
                continue
            painted = np.zeros(len(sx), dtype=bool)
            lo = np.searchsorted(sx, starts, side="left")
            hi = np.searchsorted(sx, ends, side="left")
            for a, b in zip(lo, hi):
                if b > a:
                    painted[a:b] = True
            if painted.any():
                hits[r] += painted.reshape(cols, SUPERSAMPLE).sum(axis=1)

    return hits >= (SUPERSAMPLE * SUPERSAMPLE * COVERAGE_ON)


def rectangles(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Cover the on-pixels with non-overlapping rectangles.

    Runs are merged along the row first, then a run is grown downward for as
    long as the row below holds exactly the same run. A letter's stems and bars
    collapse into a handful of rectangles this way, which keeps the outline
    small enough that a 3000-glyph font still loads like a font.
    """
    rows, cols = mask.shape
    used = np.zeros_like(mask, dtype=bool)
    out: list[tuple[int, int, int, int]] = []
    for r in range(rows):
        c = 0
        while c < cols:
            if not mask[r, c] or used[r, c]:
                c += 1
                continue
            end = c
            while end < cols and mask[r, end] and not used[r, end]:
                end += 1
            height = 1
            while r + height < rows:
                row = mask[r + height, c:end]
                spent = used[r + height, c:end]
                if not row.all() or spent.any():
                    break
                # A wider run below would be split by taking only part of it,
                # which is fine — the leftover is picked up on its own row.
                height += 1
            used[r:r + height, c:end] = True
            out.append((c, r, end, r + height))
            c = end
    return out


def snap(value: float, pixel: float) -> int:
    return int(round(value / pixel)) * int(pixel)


def pixelate_glyph(glyph_set, name: str, pixel: float, pen_glyph_set):
    """A TTGlyphPen holding the pixelated form of one glyph, or None if blank."""
    recorder = DecomposingRecordingPen(glyph_set)
    glyph_set[name].draw(recorder)
    flat = FlattenPen(glyph_set)
    recorder.replay(flat)
    contours = flat.done()

    pen = TTGlyphPen(pen_glyph_set)
    if not contours:
        return pen

    xs = [p[0] for c in contours for p in c]
    ys = [p[1] for c in contours for p in c]
    # The grid is anchored at the font origin, not the glyph, so every glyph in
    # the font lands on one shared lattice.
    col0 = math.floor(min(xs) / pixel)
    row0 = math.floor(min(ys) / pixel)
    cols = math.ceil(max(xs) / pixel) - col0 + 1
    rows = math.ceil(max(ys) / pixel) - row0 + 1
    if cols <= 0 or rows <= 0:
        return pen

    mask = rasterise(contours, col0 * pixel, row0 * pixel, cols, rows, pixel)
    p = int(pixel)
    for cx0, ry0, cx1, ry1 in rectangles(mask):
        x_a = (col0 + cx0) * p
        x_b = (col0 + cx1) * p
        y_a = (row0 + ry0) * p
        y_b = (row0 + ry1) * p
        # Clockwise in font space, so every rectangle shares one winding and
        # abutting ones merge instead of cancelling.
        pen.moveTo((x_a, y_a))
        pen.lineTo((x_a, y_b))
        pen.lineTo((x_b, y_b))
        pen.lineTo((x_b, y_a))
        pen.closePath()
    return pen


def snap_gpos(font: TTFont, pixel: float) -> None:
    """Round every placement GPOS makes to the pixel grid.

    A vowel sign positioned to a third of a pixel is a vowel sign that lands
    half a pixel off the letter it belongs to, and on a coarse grid that reads
    as a typo rather than as anti-aliasing.
    """
    if "GPOS" not in font:
        return
    seen: set[int] = set()

    def walk(obj):
        if id(obj) in seen:
            return
        seen.add(id(obj))
        cls = obj.__class__.__name__
        if cls == "Anchor":
            for attr in ("XCoordinate", "YCoordinate"):
                v = getattr(obj, attr, None)
                if v is not None:
                    setattr(obj, attr, snap(v, pixel))
            return
        if cls == "ValueRecord":
            for attr in ("XPlacement", "YPlacement", "XAdvance", "YAdvance"):
                v = getattr(obj, attr, None)
                if v is not None:
                    setattr(obj, attr, snap(v, pixel))
            return
        if hasattr(obj, "__dict__"):
            for value in list(vars(obj).values()):
                walk(value)
        elif isinstance(obj, (list, tuple)):
            for value in obj:
                walk(value)

    for value in vars(font["GPOS"].table).values():
        walk(value)


def rename(font: TTFont, family: str, version: str, license_url: str) -> None:
    """Rewrite the name table for the derived font.

    The OFL asks a modified version to carry its own name and to keep the
    licence with it; both are here rather than left to the packager.
    """
    style = "Regular"
    full = f"{family} {style}"
    ps = full.replace(" ", "")
    ofl = ("This Font Software is licensed under the SIL Open Font License, "
           "Version 1.1. This licence is available with a FAQ at "
           "https://openfontlicense.org")
    values = {
        1: family,
        2: style,
        3: f"{family}:{version}",
        4: full,
        5: f"Version {version}",
        6: ps,
        13: ofl,
        14: license_url,
        16: family,
        17: style,
    }
    name = font["name"]
    for nid, value in values.items():
        name.setName(value, nid, 3, 1, 0x409)
        name.setName(value, nid, 1, 0, 0)
    # Anything left over describes the font this one was derived from.
    for record in list(name.names):
        if record.nameID in (11, 12, 18, 19, 20, 21, 22, 25):
            name.removeNames(record.nameID)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("output")
    ap.add_argument("--pixel", type=int, default=40,
                    help="grid size in font units (default 40)")
    ap.add_argument("--family", default="Pixelborno")
    ap.add_argument("--version", default="1.000")
    ap.add_argument("--license-url", default="https://openfontlicense.org")
    ap.add_argument("--limit", type=int, default=0,
                    help="only convert the first N glyphs, for a quick look")
    args = ap.parse_args()

    font = TTFont(args.source)
    if "glyf" not in font:
        print("only TrueType outlines are supported", file=sys.stderr)
        return 2

    pixel = float(args.pixel)
    glyph_set = font.getGlyphSet()
    glyf = font["glyf"]
    hmtx = font["hmtx"]
    order = font.getGlyphOrder()
    if args.limit:
        order = order[:args.limit]

    for i, name in enumerate(order):
        pen = pixelate_glyph(glyph_set, name, pixel, glyf)
        glyph = pen.glyph()
        glyph.recalcBounds(glyf)
        glyf[name] = glyph
        lsb = glyph.xMin if glyph.numberOfContours else 0
        width, _ = hmtx[name]
        # A zero-width glyph is a combining mark and must stay zero-width.
        # Everything else moves to the grid, but never all the way to nothing.
        hmtx[name] = (max(int(pixel), snap(width, pixel)) if width else 0, lsb)
        if (i + 1) % 250 == 0:
            print(f"  {i + 1}/{len(order)} glyphs", file=sys.stderr)

    snap_gpos(font, pixel)
    rename(font, args.family, args.version, args.license_url)
    for tag in STALE_TABLES:
        if tag in font:
            del font[tag]
    font["head"].flags &= ~0x0008  # outlines are no longer integer-ppem safe
    font.save(args.output)
    print(f"wrote {args.output}: {len(order)} glyphs at {args.pixel}/em-unit grid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Letters for small capitals that a PDF draws but gives no text.

A browser printing a page set in small capitals (Standard Ebooks' chapter
titles, for one) draws the font's own small-capital glyphs and maps each of
them to nothing, so "In Santa Croce with No Baedeker" reads as "I S C N B".
When the font program is embedded, the letters can be told again: a font
keeps its small capitals together in alphabet order, and a few of them have
exactly the shape of their capital (E, H, I, L, T, X), which fixes where the
alphabet starts. Nothing is guessed unless several such letters agree. A
glyph that the font's own character map names simply gets that character.
"""

from __future__ import annotations

import io
import logging
import string
from collections import Counter

from pdfminer.pdftypes import PDFObjRef, PDFStream, resolve1
from pdfminer.psparser import PSLiteral

log = logging.getLogger(__name__)

SAME_SHAPE = 0.06  # mean distance of the outline's points, as a share of its size, to count as the capital's shape
MIN_AGREEING = 3  # small capitals that must agree on where the alphabet starts
_BLANK = {"\x00", ""}


def restore_small_capitals(pdf, pages) -> int:
    """Give the fonts on these pages the letters of their textless small capitals; returns how many were given."""
    restored, seen = 0, set()
    for page in pages:
        fonts = resolve1(page.page_obj.resources.get("Font")) if page.page_obj.resources else None
        for spec in (fonts or {}).values():
            objid = spec.objid if isinstance(spec, PDFObjRef) else None
            if objid is None or objid in seen:
                continue  # an inline font is made again on every page; only shared ones can be taught
            seen.add(objid)
            try:
                restored += _restore(pdf.rsrcmgr.get_font(objid, resolve1(spec)), resolve1(spec))
            except Exception:  # a damaged font only loses its small capitals, as before
                log.debug("could not read font %s for its small capitals", objid, exc_info=True)
    return restored


def _restore(font, spec: dict) -> int:
    table = getattr(getattr(font, "unicode_map", None), "cid2unichr", None)
    blank = sorted(cid for cid, text in (table or {}).items() if text in _BLANK)
    if not blank:
        return 0
    program = truetype_program(spec)
    if program is None:
        return 0
    letters = small_capital_letters(program, blank)
    table.update(letters)
    return len(letters)


def truetype_program(spec: dict) -> bytes | None:
    """The embedded TrueType program of a composite font whose glyph numbers are its character codes."""
    if _name(spec.get("Subtype")) != "Type0":
        return None
    descendants = resolve1(spec.get("DescendantFonts"))
    cid_font = resolve1(descendants[0]) if descendants else None
    if not isinstance(cid_font, dict) or _name(cid_font.get("Subtype")) != "CIDFontType2":
        return None
    if _name(resolve1(cid_font.get("CIDToGIDMap", PSLiteral("Identity")))) != "Identity":
        return None
    descriptor = resolve1(cid_font.get("FontDescriptor"))
    program = resolve1(descriptor.get("FontFile2")) if isinstance(descriptor, dict) else None
    return program.get_data() if isinstance(program, PDFStream) else None


def _name(value) -> str | None:
    return value.name if isinstance(value, PSLiteral) else None


def small_capital_letters(program: bytes, glyphs: list[int]) -> dict[int, str]:
    """The letters of these glyphs: as the font's own character map has them, or as small capitals (lowercase)."""
    from fontTools.ttLib import TTFont  # only needed for the rare PDF with textless glyphs

    font = TTFont(io.BytesIO(program), lazy=True)
    if "glyf" not in font or "cmap" not in font:
        return {}
    glyf, order = font["glyf"], font.getGlyphOrder()
    cmap = font["cmap"].getBestCmap() or {}
    named = {}  # the font's own text for a glyph, which the PDF's text map left out
    for code, name in sorted(cmap.items(), reverse=True):
        named[name] = chr(code)
    letters = {glyph: named[order[glyph]] for glyph in glyphs if glyph < len(order) and order[glyph] in named}
    glyphs = [glyph for glyph in glyphs if glyph not in letters]
    capitals = {}
    for letter in string.ascii_uppercase:
        outline = _outline(glyf, cmap.get(ord(letter)))
        if outline:
            capitals.setdefault(outline[0], []).append((letter, outline[1]))

    starts = Counter()  # where the alphabet starts, as each recognisable small capital tells it
    for glyph in glyphs:
        outline = _outline(glyf, order[glyph]) if glyph < len(order) else None
        if not outline:
            continue
        for letter, points in capitals.get(outline[0], []):
            if _distance(outline[1], points) < SAME_SHAPE:
                starts[glyph - string.ascii_uppercase.index(letter)] += 1
    if starts:
        (start, agreeing), *others = starts.most_common()
        if agreeing >= MIN_AGREEING and all(count * 2 < agreeing for _, count in others):
            run = [glyph for glyph in glyphs if 0 <= glyph - start < 26]
            letters.update({glyph: string.ascii_lowercase[glyph - start] for glyph in run})
    return letters


def _outline(glyf, name: str | None):
    """A simple glyph's shape: the points in each contour, and every point scaled into a unit square."""
    if name is None or name not in glyf:
        return None
    glyph = glyf[name]
    if glyph.isComposite() or glyph.numberOfContours <= 0:
        return None
    coordinates, ends, _ = glyph.getCoordinates(glyf)
    xs, ys = [x for x, _ in coordinates], [y for _, y in coordinates]
    left, bottom = min(xs), min(ys)
    width, height = (max(xs) - left) or 1, (max(ys) - bottom) or 1
    contours = tuple(end - start for start, end in zip((-1, *ends), ends, strict=False))
    return contours, [((x - left) / width, (y - bottom) / height) for x, y in coordinates]


def _distance(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    return sum(abs(x1 - x2) + abs(y1 - y2) for (x1, y1), (x2, y2) in zip(a, b, strict=True)) / len(a)

"""Small capitals a PDF draws without text get their letters back from the font's shapes."""

import io
import random
import string

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from pdfminer.pdftypes import PDFStream
from pdfminer.psparser import LIT

from imagine_reader.ingestion import glyphs

SAME_AS_CAPITAL = "EHILTX"  # small capitals drawn as the capital, smaller: the ones that tell where the alphabet starts


def polygon(points, scale=1.0):
    pen = TTGlyphPen(None)
    pen.moveTo((round(points[0][0] * scale), round(points[0][1] * scale)))
    for x, y in points[1:]:
        pen.lineTo((round(x * scale), round(y * scale)))
    pen.closePath()
    return pen.glyph()


def capital(index: int):
    """A made-up outline for each capital, different for every letter."""
    points = random.Random(index)
    return [(points.randrange(1000), points.randrange(1000)) for _ in range(4 + index % 4)]


def font_program(same_as_capital: str = SAME_AS_CAPITAL, start: int = 40) -> bytes:
    """A TrueType font with capitals A-Z and, from glyph ``start``, a run of small capitals a-z."""
    order = [".notdef", *string.ascii_uppercase]
    order += [f"filler{n}" for n in range(start - len(order))]
    order += [f"{letter}.sc" for letter in string.ascii_lowercase]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({ord(letter): letter for letter in string.ascii_uppercase})
    shapes = {name: polygon([(0, 0), (500, 0), (250, 700)]) for name in order}
    for index, letter in enumerate(string.ascii_uppercase):
        shapes[letter] = polygon(capital(index))
        if letter in same_as_capital:
            shapes[f"{letter.lower()}.sc"] = polygon(capital(index), scale=0.8)
        else:  # drawn afresh: another number of points, so never mistaken for a capital
            shapes[f"{letter.lower()}.sc"] = polygon([*capital(index), (900, 333)], scale=0.8)
    builder.setupGlyf(shapes)
    builder.setupHorizontalMetrics({name: (600, 0) for name in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Test", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    out = io.BytesIO()
    builder.save(out)
    return out.getvalue()


def test_the_small_capitals_get_their_letters_from_the_ones_shaped_like_capitals():
    run = list(range(40, 66))
    used = run[:20] + [run[24]]  # a PDF lists the glyphs it used, not always every letter
    letters = glyphs.small_capital_letters(font_program(), used)
    assert letters == {glyph: string.ascii_lowercase[glyph - 40] for glyph in used}


def test_nothing_is_guessed_without_enough_letters_that_agree():
    assert glyphs.small_capital_letters(font_program(same_as_capital="EH"), list(range(40, 66))) == {}
    assert glyphs.small_capital_letters(font_program(), list(range(27, 40))) == {}  # no letter's shape at all


def test_a_glyph_the_font_itself_names_gets_that_character():
    assert glyphs.small_capital_letters(font_program(same_as_capital=""), [3, 4, 5]) == {3: "C", 4: "D", 5: "E"}


class PdfFont:
    """A font as pdfminer reads it: its text map from the PDF, small capitals mapped to nothing."""

    def __init__(self):
        self.unicode_map = type("UnicodeMap", (), {})()
        self.unicode_map.cid2unichr = {3: " ", 9: "I", **{glyph: "\x00" for glyph in range(40, 66)}}


def test_a_pdf_font_is_taught_the_letters_its_text_map_left_out():
    descriptor = {"FontFile2": PDFStream({}, font_program())}
    cid_font = {"Subtype": LIT("CIDFontType2"), "CIDToGIDMap": LIT("Identity"), "FontDescriptor": descriptor}
    spec = {"Subtype": LIT("Type0"), "DescendantFonts": [cid_font]}
    font = PdfFont()
    assert glyphs._restore(font, spec) == 26
    assert font.unicode_map.cid2unichr[40 + 13] == "n" and font.unicode_map.cid2unichr[9] == "I"
    spec["Subtype"] = LIT("TrueType")  # a simple font: left as it is
    font = PdfFont()
    assert glyphs._restore(font, spec) == 0 and font.unicode_map.cid2unichr[53] == "\x00"

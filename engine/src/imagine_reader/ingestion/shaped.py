"""Bangla that a PDF draws in shaped glyphs but labels out of order: read back from the font.

Google Docs and Chrome print Bangla with the font's shaped glyphs and label
each glyph only with the letter the font's character map names. A vowel sign
drawn before its consonant comes first, conjuncts and the parts of a letter
drawn in pieces get no letter at all, and a piece can carry another letter
(the body of ট is ঢ's glyph), so "ট্রেন" reads as "্রন".

When the font program is embedded with its substitution rules, the words can
be read back exactly. Syllables are shaped with the same font (HarfBuzz), and
each word's glyphs are split into syllables that make exactly those glyphs.
The embedded subset lacks letters it only draws inside conjuncts, so a full
copy of the font that ships with the reader stands in when its outlines match.
A word no syllables explain keeps the PDF's own letters, put in typing order.
"""

from __future__ import annotations

import bisect
import collections
import functools
import hashlib
import io
import logging
import statistics
import unicodedata
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont
from pdfminer.cmapdb import FileUnicodeMap
from pdfminer.pdftypes import PDFObjRef, resolve1

from .glyphs import truetype_program

log = logging.getLogger(__name__)

PUA = 0xF0000  # while words are read, glyph g of the k-th font is the character PUA + k * FONT_RANGE + g
FONT_RANGE = 0x1000
MAX_FONTS = (0x10FFFD - PUA) // FONT_RANGE
REFERENCES = (Path(__file__).with_name("fonts") / "NotoSansBengali[wdth,wght].ttf",)  # Google Docs' Bangla font
SAME_FONT = 0.95  # share of the PDF's drawn glyphs a full font must have the same outlines for
TRUSTED = 0.5  # share of words syllables must explain before the PDF's own letters are set aside
LONGEST = 8  # glyphs in one syllable

CONSONANTS = "কখগঘঙচছজঝঞটঠডঢণতথদধনপফবভমযরলশষসহ\u09dc\u09dd\u09df"  # with ড়, ঢ়, য় as one character each
VOWELS = "অআইঈউঊঋএঐওঔৎ"
SIGNS = ("", "া", "ি", "ী", "ু", "ূ", "ৃ", "ে", "ৈ", "ো", "ৌ")
MARKS = ("", "ঁ", "ং", "ঃ")
THIRDS = "রযবমনলণ"  # consonants that end a conjunct of three
PROBES = ("", "ি", "ু", "ে", "া")  # signs that change a conjunct's glyphs the most
REPH = "র্"
BEFORE = set("িেৈ")  # vowel signs drawn before their consonant
AFTER = set("াীুূৃৄৗ")


def _bangla(text: str) -> bool:
    return any("\u0980" <= ch <= "\u09ff" for ch in text)


class ShapedFont:
    """An embedded font whose glyphs are read back into words by shaping syllables with it."""

    def __init__(self, base: int, program: bytes, labels: dict[int, str], stand_in: tuple | None):
        self.base = base
        self.labels = labels  # the PDF's own letters for each glyph
        self.syllables: dict[int, dict[tuple[int, ...], str]] = collections.defaultdict(dict)
        self.lengths: dict[int, set[int]] = collections.defaultdict(set)
        self.trusted = False  # its labels are known to be off: syllables explain most of its words
        self.read_back: dict[tuple[int, ...], str] = {}
        if stand_in is None:  # the subset's own rules, for the letters it has
            subset = TTFont(io.BytesIO(program), lazy=True)
            self.font, self.to_pdf, self.letters = hb.Font(hb.Face(program)), None, set(subset.getBestCmap())
        else:
            self.font, self.to_pdf, self.letters = stand_in
        self.lead = next((c for c in CONSONANTS if ord(c) in self.letters), "")  # a letter before a syllable

    def glyphs(self, text: str, inside: bool) -> tuple[int, ...]:
        """The PDF's glyph numbers for text, at a word's start or inside one (a first vowel sign has its own form)."""
        buffer = hb.Buffer()
        buffer.add_str(self.lead + text if inside else text)
        buffer.guess_segment_properties()
        hb.shape(self.font, buffer, {})
        drawn = [info.codepoint for info in buffer.glyph_infos if not inside or info.cluster >= len(self.lead)]
        if self.to_pdf is not None:
            drawn = [self.to_pdf.get(glyph, -1) for glyph in drawn]
        return tuple(drawn)

    def learn(self, words: set[tuple[int, ...]]) -> None:
        """Shape the syllables these words can be made of (only those whose glyphs the PDF draws)."""
        drawn = {w[i:j] for w in words for i in range(len(w)) for j in range(i + 1, min(len(w), i + LONGEST) + 1)}

        def add(text: str) -> bool:
            found = False
            for inside in (False, True):
                glyphs = self.glyphs(text, inside)
                if glyphs in drawn:
                    found = True
                    known = self.syllables[glyphs[0]]
                    if glyphs not in known or len(text) < len(known[glyphs]):
                        known[glyphs] = text
                        self.lengths[glyphs[0]].add(len(glyphs))
            return found

        for ch in sorted(map(chr, self.letters)):
            if unicodedata.category(ch) not in ("Mn", "Mc"):
                add(ch)  # every letter, digit, and sign that stands alone
        for vowel in VOWELS:
            for mark in MARKS[1:]:
                add(vowel + mark)
        consonants = [c for c in CONSONANTS if ord(c[0]) in self.letters]
        two = [c for c in (a + "্" + b for a in consonants for b in consonants) if any(add(c + s) for s in PROBES)]
        three = [c + "্" + t for c in two for t in THIRDS if any(add(c + "্" + t + s) for s in PROBES)]
        for cluster in consonants + two + three:
            add(cluster + "্")
            for reph in ("", REPH):
                if reph and not any(add(reph + cluster + s) for s in PROBES):
                    continue
                for sign in SIGNS:
                    for mark in MARKS:
                        add(reph + cluster + sign + mark)

    def read(self, glyphs: tuple[int, ...]) -> str | None:
        """The word whose syllables make exactly these glyphs (the fewest syllables), or None."""
        n = len(glyphs)
        best: list[tuple[int, str] | None] = [None] * n + [(0, "")]
        for i in range(n - 1, -1, -1):
            known = self.syllables.get(glyphs[i])
            for size in self.lengths.get(glyphs[i], ()):
                rest = best[i + size] if i + size <= n else None
                text = known.get(glyphs[i : i + size]) if rest is not None else None
                if text is not None and (best[i] is None or rest[0] + 1 < best[i][0]):
                    best[i] = (rest[0] + 1, text + rest[1])
        return best[0][1] if best[0] else None

    def labelled(self, glyphs: tuple[int, ...]) -> str:
        """The PDF's own letters for these glyphs: put in typing order once this font's labels are known to be off."""
        letters = [self.labels.get(glyph, "").replace("\x00", "") for glyph in glyphs]
        if not self.trusted:
            return "".join(letters)
        return unicodedata.normalize("NFC", typing_order([letter for letter in letters if letter]))


def typing_order(drawn: list[str]) -> str:
    """Letters in the order they are drawn, in the order they are typed: vowel signs after their consonants."""
    out, i, n = [], 0, len(drawn)

    def cluster(i: int) -> tuple[str, str, int]:
        parts, reph = [drawn[i]], ""
        i += 1
        while i < n:
            if (drawn[i][0] in CONSONANTS and parts[-1].endswith("্")) or drawn[i][0] in "্\u09bc":
                parts.append(drawn[i])
            elif drawn[i] == REPH and not reph:
                reph = drawn[i]  # drawn on top, after the letters it sits on
            else:
                break
            i += 1
        return "".join(parts), reph, i

    while i < n:
        letter = drawn[i]
        if letter in BEFORE and i + 1 < n and drawn[i + 1][0] in CONSONANTS:
            body, reph, i = cluster(i + 1)
            if letter == "ে" and i < n and drawn[i] in ("া", "ৗ"):
                letter, i = ("ো" if drawn[i] == "া" else "ৌ"), i + 1
            out.append(reph + body + letter)
        elif letter[0] in CONSONANTS:
            body, reph, i = cluster(i)
            out.append(reph + body)
        elif letter == "ঁ" and i + 1 < n and drawn[i + 1] in AFTER:
            out.append(drawn[i + 1] + letter)  # drawn over the consonant, typed after the vowel sign
            i += 2
        else:
            out.append(letter)
            i += 1
    return "".join(out)


def take_over(pdf, pages) -> dict[int, ShapedFont]:
    """Find the composite Bangla fonts with shaping rules; their glyphs come out as glyph numbers from now on."""
    found: dict[int, ShapedFont] = {}
    for page in pages:
        fonts = resolve1(page.page_obj.resources.get("Font")) if page.page_obj.resources else None
        for spec in (fonts or {}).values():
            objid = spec.objid if isinstance(spec, PDFObjRef) else None
            if objid is None or objid in found or len(found) >= MAX_FONTS:
                continue  # an inline font is made again on every page; only shared ones can be taught
            try:
                shaped = _take(pdf, objid, resolve1(spec), PUA + len(found) * FONT_RANGE)
            except Exception:  # a damaged font keeps its own letters
                log.debug("could not read font %s for shaping", objid, exc_info=True)
                continue
            if shaped is not None:
                found[objid] = shaped
    return found


def _take(pdf, objid: int, spec: dict, base: int) -> ShapedFont | None:
    program = truetype_program(spec)
    if program is None:
        return None
    subset = TTFont(io.BytesIO(program))
    count = subset["maxp"].numGlyphs
    if count > FONT_RANGE or not _bangla("".join(map(chr, subset.getBestCmap()))):
        return None
    stand_in = _stand_in(subset)
    if stand_in is None and "GSUB" not in subset:
        return None  # no rules to shape with (Chrome's subsets drop them; Google Docs' keep them)
    font = pdf.rsrcmgr.get_font(objid, spec)
    if font.unicode_map is None:
        font.unicode_map = FileUnicodeMap()
    table = font.unicode_map.cid2unichr
    shaped = ShapedFont(base, program, dict(table), stand_in)
    table.clear()
    table.update({glyph: chr(base + glyph) for glyph in range(count)})
    return shaped


def page_words(page, fonts: dict[int, ShapedFont]) -> list[dict] | None:
    """The page's words in drawing order, as pdfplumber gives them, if it draws shaped glyphs (else None).

    Shaped glyphs are drawn in shaping order; sorting them left to right, as
    pdfplumber does, would split a word at a mark set over its consonant.
    """
    chars = page.chars
    if not fonts or not any(_font_of(char["text"], fonts) for char in chars):
        return None
    words, run, right = [], [], 0.0
    for char in chars:
        text, size = char["text"], char["size"] or 1
        space = text.isspace() or _label(text, fonts).isspace()
        shaped_pair = run and _font_of(text, fonts) and _font_of(run[-1]["text"], fonts)
        if run and (
            space
            or abs(char["top"] - run[-1]["top"]) > size / 2  # a new line
            or (not shaped_pair and char["x0"] > right + size / 6)  # a gap as wide as a space, where none is drawn
        ):  # shaped glyphs are spaced by the font's own rules, and their words end at a drawn space
            words.append(_word(run))
            run, right = [], 0.0
        if not space:
            run.append(char)
            right = max(right, char["x1"])
    if run:
        words.append(_word(run))
    return words


def _word(chars: list[dict]) -> dict:
    return {
        "text": "".join(char["text"] for char in chars),
        "x0": min(char["x0"] for char in chars),
        "x1": max(char["x1"] for char in chars),
        "top": min(char["top"] for char in chars),
        "bottom": max(char["bottom"] for char in chars),
        "size": statistics.mode(char["size"] for char in chars),
    }


def _font_of(ch: str, fonts: dict[int, ShapedFont]) -> ShapedFont | None:
    if len(ch) != 1 or ord(ch) < PUA:
        return None
    base = PUA + (ord(ch) - PUA) // FONT_RANGE * FONT_RANGE
    return next((font for font in fonts.values() if font.base == base), None)


def _label(ch: str, fonts: dict[int, ShapedFont]) -> str:
    font = _font_of(ch, fonts)
    return font.labels.get(ord(ch) - font.base, "") if font else ""


def read_words(texts: list[str], fonts: dict[int, ShapedFont]) -> list[str]:
    """Each word's text with its shaped glyphs read back into letters."""
    parts = [_runs(text, fonts) for text in texts]
    for font in fonts.values():
        runs = collections.Counter(glyphs for word in parts for owner, glyphs in word if owner is font)
        if not runs:
            continue
        font.learn(set(runs))
        read = {glyphs: font.read(glyphs) for glyphs in runs}
        explained = sum(count for glyphs, count in runs.items() if read[glyphs] is not None)
        font.trusted = explained >= TRUSTED * sum(runs.values())  # else the shaping is not this PDF's: keep its letters
        if font.trusted:  # NFC, as typed text is stored: ড় as ড and its dot, ো as one sign
            font.read_back = {g: unicodedata.normalize("NFC", text) for g, text in read.items() if text is not None}
    return [
        "".join(
            glyphs if owner is None else owner.read_back.get(glyphs) or owner.labelled(glyphs) for owner, glyphs in word
        )
        for word in parts
    ]


def _runs(text: str, fonts: dict[int, ShapedFont]) -> list[tuple]:
    """A word as runs of plain text (owner None) and of one shaped font's glyph numbers."""
    runs: list[tuple] = []
    for ch in text:
        font = _font_of(ch, fonts)
        if font is None:
            if runs and runs[-1][0] is None:
                runs[-1] = (None, runs[-1][1] + ch)
            else:
                runs.append((None, ch))
        elif runs and runs[-1][0] is font:
            runs[-1] = (font, (*runs[-1][1], ord(ch) - font.base))
        else:
            runs.append((font, (ord(ch) - font.base,)))
    return runs


def _stand_in(subset: TTFont):
    """A full font with the subset's outlines: (its HarfBuzz font, its glyphs' numbers in the PDF, its letters)."""
    keys, drawn = _outlines(subset)
    shown = [glyph for glyph in range(len(keys)) if drawn[glyph]]  # a subset blanks the glyphs it leaves out
    for path in REFERENCES:
        if not path.exists():
            continue
        full, numbers = _reference(path)
        to_full = match_glyphs(keys, numbers)
        if shown and sum(glyph in to_full for glyph in shown) >= SAME_FONT * len(shown):
            to_pdf = {}
            for glyph, number in sorted(to_full.items()):
                to_pdf.setdefault(number, glyph)
            return full, to_pdf, set(TTFont(path, lazy=True).getBestCmap())
    return None


def match_glyphs(keys: list[str], numbers: dict[str, list[int]]) -> dict[int, int]:
    """Each glyph's number in the full font: its own shape where that is unique, else the twin its neighbors point to.

    A subset may keep the font's numbering only up to an offset (a font build
    with a glyph or two more), and some pieces have exact twins; the offset of
    the nearest glyph with a unique shape tells which twin is meant.
    """
    to_full = {glyph: numbers[key][0] for glyph, key in enumerate(keys) if len(numbers.get(key, ())) == 1}
    anchors = sorted(to_full)
    for glyph, key in enumerate(keys):  # blank ones too: a font adds an empty glyph to space some syllables
        candidates = numbers.get(key, ())
        if len(candidates) > 1:
            at = bisect.bisect(anchors, glyph)
            near = min(anchors[max(at - 1, 0) : at + 1], key=lambda a: abs(a - glyph), default=glyph)
            expected = glyph + to_full.get(near, near) - near
            to_full[glyph] = min(candidates, key=lambda c: abs(c - expected))
    return to_full


@functools.cache
def _reference(path: Path) -> tuple[hb.Font, dict[str, list[int]]]:
    numbers = collections.defaultdict(list)
    for glyph, key in enumerate(_outlines(TTFont(path))[0]):
        numbers[key].append(glyph)
    return hb.Font(hb.Face(path.read_bytes())), dict(numbers)


def _outlines(font: TTFont) -> tuple[list[str], list[bool]]:
    """A fingerprint of each glyph's outline (components drawn in) and advance, and whether it draws anything."""
    shapes, keys, drawn = font.getGlyphSet(), [], []
    for name in font.getGlyphOrder():
        pen = DecomposingRecordingPen(shapes)
        shapes[name].draw(pen)
        keys.append(hashlib.blake2b(repr((pen.value, shapes[name].width)).encode(), digest_size=12).hexdigest())
        drawn.append(bool(pen.value))
    return keys, drawn

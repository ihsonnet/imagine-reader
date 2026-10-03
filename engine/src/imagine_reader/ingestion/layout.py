"""Turn positioned words into lines, reading order, and paragraphs.

Geometry here is in PDF points on the visible page, origin at the top-left.
The thresholds are relative to font size, so they hold across page sizes.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .models import TextEdit

SEGMENT_GAP_EM = 1.5  # a wider horizontal gap inside a row separates columns
TOUCHING_EM = 0.08  # pieces closer than this are one word split by a font change (small caps)
COLUMN_GAP_EM = 1.5  # minimum gutter width for a vertical (column) cut
MARGIN_ZONE = 0.2  # top and bottom fraction of the page searched for headers and footers
MARGIN_GAP = 0.6  # headers and footers stand apart from the text by this many line heights
MARGIN_MAX_SIZE = 1.1  # and are no larger than body text, unlike chapter headings
DROP_CAP_SCALE = 1.8  # a lone letter this many times taller than the text is a drop cap
INDENT_EM = 0.5  # first-line indent that starts a new paragraph
CENTERED_EM = 2.0  # space on both sides that marks a centered line (a heading)
PARAGRAPH_GAP = 1.6  # line pitch multiple that marks a paragraph gap
JUSTIFIED_SHARE = 0.45  # share of lines reaching their column's right edge that marks justified text
SHORT_LINE_EM = 2.0  # in justified text, a line ending this far short of the right edge ends its paragraph
SIZE_CHANGE = 0.15  # relative font-size change that starts a new paragraph
HEADING_SCALE = 1.2  # a short paragraph set this much larger than body text is a heading
CENTERED_HEADING_SCALE = 1.1  # and a single centered line set this much larger (a chapter title in a Bangla book)
HEADING_MAX_LINES = 3
HEADING_MAX_CHARS = 120

_PAGE_NUMBER = re.compile(
    r"^(?:page\s+)?"
    r"(?:\d{1,4}|(?=[ivxlcdm]+$)m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3}))"
    r"(?:\s*(?:of|/)\s*\d{1,4})?$",
    re.IGNORECASE,
)
_LINE_END_HYPHENS = "-­"  # hyphen, soft hyphen
_DROP_CAP = re.compile(r"^(?:[\"'“‘][A-Za-z]?|[A-Za-z])$")  # a letter, an opening quote, or both
_ONE_LETTER_WORDS = {"A", "I", "O"}


@dataclass
class RawWord:
    page: int
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    size: float
    id: str = ""
    drop_cap: bool = False


@dataclass
class RawLine:
    page: int
    words: list[RawWord]
    role: str = "body"
    column: tuple[int, float, float] = (0, 0.0, 0.0)  # (page, left, right) of the text column
    id: str = ""
    beside_drop_cap: bool = False  # indented only to make room for a drop cap

    @property
    def _text_words(self) -> list[RawWord]:
        """Words that set the line's height and size: everything except a drop cap."""
        return [word for word in self.words if not word.drop_cap] or self.words

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def x0(self) -> float:
        return min(word.x0 for word in self.words)

    @property
    def x1(self) -> float:
        return max(word.x1 for word in self.words)

    @property
    def top(self) -> float:
        return min(word.top for word in self._text_words)

    @property
    def bottom(self) -> float:
        return max(word.bottom for word in self._text_words)

    @property
    def size(self) -> float:
        return statistics.median(word.size for word in self._text_words)


@dataclass
class ComposedParagraph:
    lines: list[RawLine]
    text: str
    spans: list[tuple[RawWord, int, int]]  # each word's character range in ``text``
    edits: list[TextEdit] = field(default_factory=list)


def _find_drop_caps(words: list[RawWord]) -> list[tuple[RawWord, RawWord]]:
    """Find large initial letters, each paired with the first word printed beside it."""
    if len(words) < 2:
        return []
    height = statistics.median(word.bottom - word.top for word in words)
    found = []
    for cap in words:
        cap_height = cap.bottom - cap.top
        if cap_height < DROP_CAP_SCALE * height or not _DROP_CAP.match(cap.text):
            continue
        beside = [
            word
            for word in words
            if word is not cap
            and cap.x1 - 1 <= word.x0 <= cap.x1 + 2 * height
            and cap.top - height / 2 <= (word.top + word.bottom) / 2 <= cap.top + cap_height / 2
        ]
        if not beside:
            continue
        first = min(beside, key=lambda word: (word.top, word.x0))
        # A drop cap reaches down past the line beside it; a small-caps initial shares its baseline.
        if cap.bottom >= first.bottom + 0.6 * height:
            found.append((cap, first))
    return found


def build_lines(words: list[RawWord]) -> list[RawLine]:
    """Group one page's words into rows, then split rows at column gutters.

    A drop cap is kept out of the rows (it spans several) and then attached to
    the start of the first line printed beside it.
    """
    drop_caps = _find_drop_caps(words)
    cap_ids = {id(cap) for cap, _ in drop_caps}
    rows: list[list[RawWord]] = []
    for word in sorted((w for w in words if id(w) not in cap_ids), key=lambda w: ((w.top + w.bottom) / 2, w.x0)):
        middle = (word.top + word.bottom) / 2
        if rows:
            row = rows[-1]
            row_middle = statistics.fmean((w.top + w.bottom) / 2 for w in row)
            tolerance = 0.5 * min(min(w.bottom - w.top for w in row), word.bottom - word.top)
            if abs(middle - row_middle) <= tolerance:
                row.append(word)
                continue
        rows.append([word])

    lines: list[RawLine] = []
    absorbed: dict[int, RawWord] = {}
    for row in rows:
        row.sort(key=lambda w: w.x0)
        row = _join_touching(row, absorbed)
        current = [row[0]]
        for previous, word in zip(row, row[1:], strict=False):
            if word.x0 - previous.x1 > SEGMENT_GAP_EM * max(previous.size, word.size):
                lines.append(RawLine(word.page, current))
                current = []
            current.append(word)
        lines.append(RawLine(row[0].page, current))

    for cap, first_word in drop_caps:
        cap.drop_cap = True
        first_word = absorbed.get(id(first_word), first_word)
        target = next(line for line in lines if any(word is first_word for word in line.words))
        target.words.insert(0, cap)
        for line in lines:
            if line is not target and line.x0 >= cap.x1 - 1 and cap.top < (line.top + line.bottom) / 2 < cap.bottom:
                line.beside_drop_cap = True
    return lines


def _join_touching(row: list[RawWord], absorbed: dict[int, RawWord]) -> list[RawWord]:
    """Rejoin pieces of one word that the PDF split at a font-size change.

    Small capitals ("C" + "HAPTER") and mixed sizes inside a word arrive as
    separate words that touch; real words are separated by a space. The first
    piece grows in place, and ``absorbed`` maps each later piece to it.
    """
    joined = [row[0]]
    for word in row[1:]:
        previous = joined[-1]
        if word.x0 - previous.x1 < TOUCHING_EM * min(previous.size, word.size):
            previous.text += word.text
            previous.x1 = word.x1
            previous.top = min(previous.top, word.top)
            previous.bottom = max(previous.bottom, word.bottom)
            previous.size = max(previous.size, word.size)
            absorbed[id(word)] = previous
        else:
            joined.append(word)
    return joined


def is_page_number(text: str) -> bool:
    return bool(_PAGE_NUMBER.match(text.strip().strip("-–—·•()[]. ")))


def classify_margins(lines: list[RawLine], page_heights: dict[int, float]) -> None:
    """Mark page numbers, and running headers and footers that repeat across pages.

    A candidate sits near the top or bottom edge, is set apart from the text
    block by a gap, and is no larger than body text. Lines are only flagged,
    never deleted, so the original text stays auditable. Chapter headings
    (larger) and short dialogue lines (no gap) stay body text.
    """
    if not lines:
        return
    body_size = statistics.median(line.size for line in lines)
    by_page: defaultdict[int, list[RawLine]] = defaultdict(list)
    for line in lines:
        by_page[line.page].append(line)

    candidates: list[tuple[tuple, RawLine]] = []
    for line in lines:
        height = page_heights[line.page]
        if line.size > MARGIN_MAX_SIZE * body_size:
            continue
        others = by_page[line.page]
        if line.bottom <= MARGIN_ZONE * height:
            zone = "top"
            gaps = [other.top - line.bottom for other in others if other.top >= line.bottom - 0.5]
        elif line.top >= (1 - MARGIN_ZONE) * height:
            zone = "bottom"
            gaps = [line.top - other.bottom for other in others if other.bottom <= line.top + 0.5]
        else:
            continue
        if gaps and min(gaps) < MARGIN_GAP * (line.bottom - line.top):
            continue
        if is_page_number(line.text):
            line.role = "page_number"
            continue
        repeat_key = re.sub(r"\d+", "#", " ".join(line.text.casefold().split()))
        candidates.append(((zone, round(line.size), repeat_key), line))

    pages_by_key: defaultdict[tuple, set[int]] = defaultdict(set)
    for key, line in candidates:
        pages_by_key[key].add(line.page)
    for key, line in candidates:
        if len(pages_by_key[key]) >= 2:
            line.role = "header" if key[0] == "top" else "footer"


def _widest_gap(intervals: list[tuple[float, float]], minimum: float) -> float | None:
    """Midpoint of the widest empty stretch between intervals, if one is at least ``minimum`` wide."""
    ordered = sorted(intervals)
    best: tuple[float, float] | None = None
    reach = ordered[0][1]
    for start, end in ordered[1:]:
        if start - reach >= minimum and (best is None or start - reach > best[1] - best[0]):
            best = (reach, start)
        reach = max(reach, end)
    return None if best is None else (best[0] + best[1]) / 2


def order_page(lines: list[RawLine]) -> list[RawLine]:
    """Put one page's body lines in reading order with a recursive XY-cut.

    Column gutters are cut first, so two columns read left then right; a heading
    spanning both columns is separated from them by a horizontal cut. Each line
    records the text column it belongs to, which paragraph detection uses.
    """
    if not lines:
        return []
    min_gutter = COLUMN_GAP_EM * statistics.median(line.size for line in lines)
    ordered: list[RawLine] = []

    def column_of(items: list[RawLine]) -> tuple[int, float, float]:
        return (items[0].page, min(line.x0 for line in items), max(line.x1 for line in items))

    def visit(items: list[RawLine], column: tuple[int, float, float]) -> None:
        if len(items) > 1:
            cut = _widest_gap([(line.x0, line.x1) for line in items], min_gutter)
            if cut is not None:
                for side in ([line for line in items if line.x1 <= cut], [line for line in items if line.x0 >= cut]):
                    visit(side, column_of(side))
                return
            cut = _widest_gap([(line.top, line.bottom) for line in items], 0.5)
            if cut is not None:
                visit([line for line in items if line.bottom <= cut], column)
                visit([line for line in items if line.top >= cut], column)
                return
        for line in sorted(items, key=lambda line: (line.top, line.x0)):
            line.column = column
            ordered.append(line)

    visit(lines, column_of(lines))
    return ordered


def _snap_small_columns(lines: list[RawLine]) -> None:
    """Give columns with very few lines the left edge of a matching larger column.

    A column's left edge is the leftmost line start in it. If every line in a
    tiny column is indented, that would hide the indent, so such columns borrow
    the edge of a well-populated column that starts slightly to their left.
    """
    sizes = Counter(line.column for line in lines)
    reliable = sorted({column[1] for column, count in sizes.items() if count >= 4})
    for line in lines:
        if sizes[line.column] >= 4:
            continue
        page, left, right = line.column
        near = [edge for edge in reliable if left - 4 * line.size <= edge <= left]
        if near:
            line.column = (page, max(near), right)


def build_paragraphs(lines: list[RawLine], pages_with_text: set[int]) -> list[list[RawLine]]:
    """Group reading-ordered body lines from the whole document into paragraphs."""
    _snap_small_columns(lines)
    pitches = [
        line.top - previous.top
        for previous, line in zip(lines, lines[1:], strict=False)
        if line.column == previous.column and line.top > previous.top
    ]
    pitch = statistics.median(pitches) if pitches else None
    body_size = statistics.median(line.size for line in lines) if lines else 0.0
    full = sum(line.column[2] - line.x1 < 0.5 * line.size for line in lines)
    justified = bool(lines) and full >= JUSTIFIED_SHARE * len(lines)

    def starts_paragraph(previous: RawLine, line: RawLine) -> bool:
        if any(page not in pages_with_text for page in range(previous.page + 1, line.page)):
            return True  # never join text across a page that could not be read
        if line.beside_drop_cap:
            return False
        if abs(line.size - previous.size) > SIZE_CHANGE * max(line.size, previous.size):
            return True
        heading_sized = min(line.size, previous.size) >= HEADING_SCALE * body_size
        same_size = abs(line.size - previous.size) <= SIZE_CHANGE * max(line.size, previous.size)
        if heading_sized and same_size and line.page == previous.page and line.top - previous.bottom < line.size:
            return False  # a heading that wraps onto a second line
        if is_centered(previous) or is_centered(line):
            return True
        if justified and previous.column[2] - previous.x1 > SHORT_LINE_EM * previous.size:
            return True  # a line that stops short ends its paragraph, where paragraphs are neither indented nor spaced
        if line.x0 - line.column[1] > INDENT_EM * line.size:
            return True
        same_column = line.column == previous.column
        return bool(same_column and pitch and line.top - previous.top > PARAGRAPH_GAP * pitch)

    paragraphs: list[list[RawLine]] = []
    for index, line in enumerate(lines):
        if index == 0 or starts_paragraph(lines[index - 1], line):
            paragraphs.append([line])
        else:
            paragraphs[-1].append(line)
    return paragraphs


def is_centered(line: RawLine) -> bool:
    _, left, right = line.column
    margin = CENTERED_EM * line.size
    return line.x0 - left > margin and right - line.x1 > margin


def is_heading(lines: list[RawLine], text: str, body_size: float) -> bool:
    if len(lines) > HEADING_MAX_LINES or len(text) > HEADING_MAX_CHARS:
        return False
    if all(line.size >= HEADING_SCALE * body_size for line in lines):
        return True
    return len(lines) == 1 and is_centered(lines[0]) and lines[0].size >= CENTERED_HEADING_SCALE * body_size


def bare(text: str) -> str:
    return text.strip("\"'“”‘’()[]{}.,;:!?…—–").casefold()


def _ends_with_split(word: RawWord) -> bool:
    return len(word.text) >= 2 and word.text[-1] in _LINE_END_HYPHENS and word.text[-2].isalpha()


def build_vocabulary(lines: list[RawLine]) -> Counter[str]:
    """Count the book's whole words, leaving out the halves of words split across lines and drop caps."""
    vocabulary: Counter[str] = Counter()
    previous: RawLine | None = None
    for line in lines:
        for index, word in enumerate(line.words):
            continues_split = index == 0 and previous is not None and _ends_with_split(previous.words[-1])
            if not (word.drop_cap or continues_split or (word is line.words[-1] and _ends_with_split(word))):
                vocabulary[bare(word.text)] += 1
        previous = line
    return vocabulary


def compose(lines: list[RawLine], vocabulary: Counter[str]) -> ComposedParagraph:
    """Build a paragraph's reading text, repairing layout splits inside words.

    A hyphen at the end of a line followed by a lowercase word is usually a
    typesetting split ("light-" / "house"), but sometimes a real compound
    ("seventy-" / "fifth"). The form seen more often elsewhere in the book wins.
    If neither form appears, the hyphen stays when both halves are words
    ("nutmeg-grater"), which a narrator also reads more safely; otherwise the
    word is joined. Before a capital ("Jean-" / "Paul") the hyphen is kept.

    A drop cap joins the word beside it ("M" + "y" -> "My") unless it is a word
    of its own ("I" + "fully" -> "I fully"). Every repair is recorded as an edit.
    """
    pieces: list[list] = []  # [word, separator, reading text]
    edits: list[TextEdit] = []
    for line in lines:
        for index, word in enumerate(line.words):
            separator = " " if pieces else ""
            if pieces and pieces[-1][0].drop_cap and not word.drop_cap:
                letter = pieces[-1][0].text
                if vocabulary[bare(letter + word.text)] or letter.lstrip("\"'“‘").upper() not in _ONE_LETTER_WORDS:
                    separator = ""
                    edits.append(
                        TextEdit(
                            kind="drop_cap_joined",
                            word_ids=[pieces[-1][0].id, word.id],
                            printed=f"{letter} {word.text}",
                            reading=letter + word.text,
                        )
                    )
            elif index == 0 and pieces:
                previous = pieces[-1]
                head = previous[0].text
                if _ends_with_split(previous[0]) and word.text[:1].isalpha():
                    stem = head[:-1]
                    hyphenated = vocabulary[bare(f"{stem}-{word.text}")]
                    joined = vocabulary[bare(stem + word.text)]
                    if word.text[0].isupper():
                        keep = True
                    elif hyphenated != joined:
                        keep = hyphenated > joined
                    else:
                        halves = (bare(stem), bare(word.text))
                        keep = all(len(half) >= 3 and vocabulary[half] for half in halves)
                    previous[2] = f"{stem}-" if keep else stem
                    separator = ""
                    edits.append(
                        TextEdit(
                            kind="hyphen_kept" if keep else "hyphen_joined",
                            word_ids=[previous[0].id, word.id],
                            printed=f"{head} {word.text}",
                            reading=previous[2] + word.text,
                        )
                    )
            pieces.append([word, separator, word.text])

    text_parts: list[str] = []
    spans: list[tuple[RawWord, int, int]] = []
    offset = 0
    for word, separator, reading in pieces:
        offset += len(separator)
        text_parts.append(separator + reading)
        spans.append((word, offset, offset + len(reading)))
        offset += len(reading)
    return ComposedParagraph(lines=lines, text="".join(text_parts), spans=spans, edits=edits)

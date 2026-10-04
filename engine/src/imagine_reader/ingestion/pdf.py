"""Extract source-linked reading text from a PDF's native text layer.

Pages without a usable text layer are marked ``needs_ocr`` and reported in
``warnings``; recognizing them is the job of a separate OCR stage. Until there
is one, a PDF where most pages need it is refused as scanned.
"""

from __future__ import annotations

import hashlib
import io
import statistics
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect
from pdfplumber.utils.exceptions import PdfminerException

from .. import __version__
from . import glyphs, layout, shaped
from .errors import DamagedPdfError, EncryptedPdfError, LimitExceededError, NotAPdfError, ScannedPdfError
from .models import Box, ExtractedDocument, Line, Page, Paragraph, Section, Sentence, Word
from .sentences import sentence_spans

EXTRACTOR_VERSION = f"{__version__}+pdfplumber-{pdfplumber.__version__}"

_LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"})
# Zero-width space, word joiner, BOM, and C0 control codes some fonts emit for unmapped glyphs
# (page numbers, ornaments). Zero-width (non-)joiners stay: Bangla and other scripts need them.
_INVISIBLE = dict.fromkeys(
    [*map(ord, "\u200b\u2060\ufeff"), *range(0x00, 0x20), 0x7F],
    None,
)
_UNMAPPED_GLYPHS = 0.1  # share of "(cid:N)" words that makes a text layer unusable
_SCAN_PROBE_PAGES = 10  # pages of a book over the page limit read first, to tell a scan from a long book


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 20 * 1024 * 1024
    max_pages: int = 100
    max_words: int = 30_000


def ingest_pdf(source: bytes | str | Path, limits: Limits | None = None, trim: bool = False) -> ExtractedDocument:
    """Read a PDF into source-linked text.

    With ``trim``, a PDF longer than the page or word limit is read only up
    to the limit (whole pages), with a warning, instead of being refused. A
    scanned PDF is refused as scanned before it is refused as too long.
    """
    limits = limits or Limits()
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    _check_limit("bytes", len(data), limits.max_bytes, "The file is larger than the upload limit.")
    if b"%PDF-" not in data[:1024]:
        raise NotAPdfError("The file is not a PDF.")

    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except PdfminerException as error:
        cause = error.args[0] if error.args else None
        if isinstance(cause, PDFPasswordIncorrect | PDFEncryptionError):
            raise EncryptedPdfError("The PDF is password-protected or uses unsupported encryption.") from error
        raise DamagedPdfError("The PDF is damaged or malformed and could not be opened.") from error

    with pdf:
        total = len(pdf.pages)
        too_long = total > limits.max_pages and not trim
        read = pdf.pages[: min(limits.max_pages, _SCAN_PROBE_PAGES) if too_long else limits.max_pages]
        pages, words, warnings = [], [], []
        shaped_fonts = shaped.take_over(pdf, read)
        restored = glyphs.restore_small_capitals(pdf, read)
        for page in read:
            try:
                page_info, page_words, page_warnings = _read_page(page, shaped_fonts)
            except Exception as error:  # pdfminer raises many low-level types on bad content streams
                raise DamagedPdfError(f"Page {page.page_number} is damaged and could not be read.") from error
            finally:
                page.close()  # release the parsed page; long books otherwise hold every page in memory
            if trim and len(words) + len(page_words) > limits.max_words:
                break  # the next page would pass the word limit
            pages.append(page_info)
            words.extend(page_words)
            warnings.extend(page_warnings)
        if _scanned(pages):
            raise ScannedPdfError("This PDF is a scan: its pages are pictures of text, with no text in them to read.")
        if too_long:
            _check_limit("pages", total, limits.max_pages, "The PDF has more pages than the limit.")
        if shaped_fonts:
            words = _read_shaped(words, shaped_fonts)
        if restored:
            warnings.append(
                "Some small capitals, such as chapter titles, had no text in this PDF; their letters were read from "
                "the shapes in its font."
            )
        if len(pages) < total:
            warnings.insert(
                0, f"Only the first {len(pages)} of {total} pages were read, to stay within the reader's limits."
            )

    _check_limit("words", len(words), limits.max_words, "The PDF has more words than the limit.")
    if len(pages) > 1 and pages[0].method == "needs_ocr" and all(page.method != "needs_ocr" for page in pages[1:]):
        warnings = [w for w in warnings if not w.startswith("Page 1 has no text layer")]  # a cover picture
    document = _assemble(pages, words, hashlib.sha256(data).hexdigest(), warnings)
    if not document.sentences:
        document.warnings.append("No readable text was found in this PDF.")
    return document


def _scanned(pages: list[Page]) -> bool:
    """Whether more than half of the pages with anything on them are pictures with no text to read."""
    shown = [page for page in pages if page.method != "empty"]
    return sum(page.method == "needs_ocr" for page in shown) * 2 > len(shown)


def _check_limit(name: str, value: int, maximum: int, message: str) -> None:
    if value > maximum:
        raise LimitExceededError(f"{message} ({value:,} > {maximum:,} {name})", name, value, maximum)


def _clean(text: str) -> str:
    text = text.translate(_LIGATURES).translate(_INVISIBLE)
    if "­" in text[:-1]:  # soft hyphens inside a word are invisible; one at the end marks a line break
        text = text[:-1].replace("­", "") + text[-1]
    return text


def _read_shaped(words: list[layout.RawWord], fonts: dict) -> list[layout.RawWord]:
    """Words with their shaped glyphs read back into letters; any left with no letters are dropped."""
    for word, text in zip(words, shaped.read_words([word.text for word in words], fonts), strict=True):
        word.text = _clean(text)
    return [word for word in words if word.text]


def _read_page(page, shaped_fonts: dict | None = None) -> tuple[Page, list[layout.RawWord], list[str]]:
    """Read one page's words in visible-page coordinates (points, origin top-left)."""
    crop_x0, crop_top, crop_x1, crop_bottom = page.cropbox
    width, height = crop_x1 - crop_x0, crop_bottom - crop_top
    words = []
    raw_words = shaped.page_words(page, shaped_fonts) if shaped_fonts else None
    for raw in raw_words or page.extract_words(extra_attrs=["size"]):
        x0, x1 = raw["x0"] - crop_x0, raw["x1"] - crop_x0
        top, bottom = raw["top"] - crop_top, raw["bottom"] - crop_top
        if not (0 <= (x0 + x1) / 2 <= width and 0 <= (top + bottom) / 2 <= height):
            continue  # outside the visible area
        text = _clean(raw["text"])
        if text:
            words.append(layout.RawWord(page.page_number, text, x0, top, x1, bottom, float(raw["size"])))

    warnings = []
    unmapped = sum("(cid:" in word.text for word in words)
    if words and unmapped / len(words) > _UNMAPPED_GLYPHS:
        method = "needs_ocr"
        words = []
        warnings.append(f"Page {page.page_number}'s text layer is unreadable (unmapped font glyphs) and needs OCR.")
    elif words:
        method = "native"
    elif page.images:
        method = "needs_ocr"
        warnings.append(f"Page {page.page_number} has no text layer and needs OCR.")
    else:
        method = "empty"
    info = Page(
        number=page.page_number,
        width=round(width, 2),
        height=round(height, 2),
        rotation=page.rotation,
        method=method,
        word_count=len(words),
    )
    return info, words, warnings


def _assemble(pages: list[Page], words: list[layout.RawWord], sha256: str, warnings: list[str]) -> ExtractedDocument:
    sizes = {page.number: (page.width, page.height) for page in pages}

    def box(page: int, x0: float, top: float, x1: float, bottom: float) -> Box:
        width, height = sizes[page]
        return Box(
            page=page,
            x0=round(max(0.0, x0 / width), 5),
            top=round(max(0.0, top / height), 5),
            x1=round(min(1.0, x1 / width), 5),
            bottom=round(min(1.0, bottom / height), 5),
        )

    words_by_page: dict[int, list[layout.RawWord]] = {page.number: [] for page in pages}
    for word in words:
        words_by_page[word.page].append(word)
    raw_lines = [line for page in pages for line in layout.build_lines(words_by_page[page.number])]
    layout.classify_margins(raw_lines, {number: size[1] for number, size in sizes.items()})

    # Order each page: top margin lines, body in reading order, bottom margin lines.
    body: list[layout.RawLine] = []
    all_lines: list[layout.RawLine] = []
    for page in pages:
        on_page = [line for line in raw_lines if line.page == page.number]
        margin = sorted((line for line in on_page if line.role != "body"), key=lambda line: (line.top, line.x0))
        ordered_body = layout.order_page([line for line in on_page if line.role == "body"])
        middle = sizes[page.number][1] / 2
        page_lines = [line for line in margin if line.top < middle] + ordered_body
        page_lines += [line for line in margin if line.top >= middle]
        for line_number, line in enumerate(page_lines, start=1):
            line.id = f"p{page.number:03d}-l{line_number:03d}"
        word_number = 0
        for line in page_lines:
            for word in line.words:
                word_number += 1
                word.id = f"p{page.number:03d}-w{word_number:04d}"
        body.extend(ordered_body)
        all_lines.extend(page_lines)

    vocabulary = layout.build_vocabulary(body)
    pages_with_text = {page.number for page in pages if page.method == "native"}
    body_size = statistics.median(line.size for line in body) if body else 0.0
    paragraphs: list[Paragraph] = []
    sentences: list[Sentence] = []
    paragraph_pages: list[int] = []
    for paragraph_lines in layout.build_paragraphs(body, pages_with_text):
        composed = layout.compose(paragraph_lines, vocabulary)
        paragraph_id = f"para-{len(paragraphs) + 1:04d}"
        paragraphs.append(
            Paragraph(
                id=paragraph_id,
                order=len(paragraphs),
                line_ids=[line.id for line in paragraph_lines],
                text=composed.text,
                edits=composed.edits,
                heading=layout.is_heading(paragraph_lines, composed.text, body_size),
            )
        )
        paragraph_pages.append(paragraph_lines[0].page)
        line_of = {id(word): line for line in paragraph_lines for word in line.words}
        for start, end in sentence_spans(composed.text):
            members = [word for word, word_start, word_end in composed.spans if word_start < end and word_end > start]
            boxes: list[Box] = []
            for line in paragraph_lines:
                on_line = [word for word in members if line_of[id(word)] is line]
                if on_line:
                    boxes.append(
                        box(
                            line.page,
                            min(w.x0 for w in on_line),
                            min(w.top for w in on_line),
                            max(w.x1 for w in on_line),
                            max(w.bottom for w in on_line),
                        )
                    )
            sentences.append(
                Sentence(
                    id=f"s-{len(sentences) + 1:05d}",
                    order=len(sentences),
                    paragraph_id=paragraph_id,
                    narration_text=composed.text[start:end],
                    printed_text=" ".join(word.text for word in members),
                    word_ids=[word.id for word in members],
                    boxes=boxes,
                )
            )

    return ExtractedDocument(
        extractor_version=EXTRACTOR_VERSION,
        source_sha256=sha256,
        pages=pages,
        lines=[
            Line(
                id=line.id,
                page=line.page,
                role=line.role,
                text=line.text,
                font_size=round(line.size, 2),
                box=box(line.page, line.x0, line.top, line.x1, line.bottom),
                word_ids=[word.id for word in line.words],
            )
            for line in all_lines
        ],
        words=[
            Word(
                id=word.id, line_id=line.id, text=word.text, box=box(word.page, word.x0, word.top, word.x1, word.bottom)
            )
            for line in all_lines
            for word in line.words
        ],
        paragraphs=paragraphs,
        sentences=sentences,
        sections=_sections(paragraphs, paragraph_pages),
        warnings=warnings,
    )


def _sections(paragraphs: list[Paragraph], pages: list[int]) -> list[Section]:
    """Split the book at its headings. Headings in a row ("Part One", "Chapter 1") open one section."""
    starts: list[tuple[int, str]] = []
    previous_heading = -2
    for paragraph in paragraphs:
        if not paragraph.heading:
            continue
        if paragraph.order == previous_heading + 1 and starts:
            first, title = starts[-1]
            starts[-1] = (first, f"{title} · {paragraph.text}")
        else:
            starts.append((paragraph.order, paragraph.text))
        previous_heading = paragraph.order
    if paragraphs and (not starts or starts[0][0] > 0):
        starts.insert(0, (0, "Beginning"))
    return [
        Section(
            id=f"sec-{index + 1:03d}",
            title=title,
            first_paragraph=first,
            end_paragraph=starts[index + 1][0] if index + 1 < len(starts) else len(paragraphs),
            page=pages[first],
        )
        for index, (first, title) in enumerate(starts)
    ]

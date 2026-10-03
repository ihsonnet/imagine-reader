#!/usr/bin/env python3
"""Generate test PDFs from a sample story, each with a ground-truth JSON file.

The generator does its own line layout, so it knows exactly which text was
printed on which line and where. The truth file records every printed line
(text, page, position on the visible page) and every paragraph's expected
reading text. Extraction tests compare against these answers.

Usage (from the repository root):

    .venv/bin/python fixtures/generate_pdfs.py
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import pypdfium2 as pdfium
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent
STORY = ROOT / "stories" / "the-lantern-keeper.txt"
OUT = ROOT / "pdfs"

BODY_FONT = "Times-Roman"
TITLE_FONT = "Times-Bold"
HEADER_FONT = "Times-Italic"
TITLE_SIZE = 18
HEADER_SIZE = 9
TITLE_GAP = 24  # space between the title baseline and the first body line's top

# Only plain words are split across lines; words that already contain a
# hyphen are moved whole to the next line, as most typesetters do.
HYPHENATABLE = re.compile(r"^([A-Za-z]{7,})([.,;:!?”’]*)$")


@dataclass(frozen=True)
class Variant:
    name: str
    visible_size: tuple[float, float]  # width, height of the page as displayed
    columns: int
    font_size: float
    leading: float
    margin: float
    gutter: float = 0
    indent: float = 18
    rotate: int = 0  # the page's /Rotate value
    crop_inset: float = 0  # CropBox inset from the MediaBox on every side
    header_on_first_page: bool = False
    # Paperback style: a drop cap, a deep bottom margin, and alternating footers
    # (page number + title on even pages, site name + page number on odd ones).
    book_style: bool = False
    bottom_margin: float | None = None


VARIANTS = [
    # A running header from page 2 on, as in most books; four pages so it repeats.
    Variant("single-column", (612, 792), columns=1, font_size=14, leading=22, margin=90),
    Variant(
        "two-column",
        (612, 792),
        columns=2,
        font_size=12,
        leading=16,
        margin=72,
        gutter=24,
        header_on_first_page=True,
    ),
    Variant(
        "rotated-landscape",
        (792, 612),
        columns=2,
        font_size=12,
        leading=16,
        margin=72,
        gutter=30,
        rotate=90,
        header_on_first_page=True,
    ),
    Variant(
        "cropped",
        (540, 720),
        columns=1,
        font_size=13,
        leading=18,
        margin=54,
        crop_inset=36,
        header_on_first_page=True,
    ),
    Variant(
        "book-page",
        (323, 484),
        columns=1,
        font_size=11,
        leading=14,
        margin=52,
        indent=12,
        book_style=True,
        bottom_margin=96,
    ),
]

BOOK_FOOTER = "Imagine Reader sample stories"
FOOTER_SIZE = 7


@dataclass
class PlacedLine:
    page: int
    x: float  # points from the left edge of the visible page
    baseline: float  # points from the bottom edge of the visible page
    text: str
    font: str
    size: float
    role: str  # title | body | drop_cap | header | footer | page_number
    paragraph: int | None = None


def read_story(path: Path) -> tuple[str, list[str]]:
    blocks = [" ".join(block.split()) for block in path.read_text(encoding="utf-8").split("\n\n")]
    blocks = [block for block in blocks if block]
    return blocks[0], blocks[1:]


def break_lines(text: str, first_widths: list[float], width: float, font: str, size: float) -> list[str]:
    """Greedy line breaking with simple hyphenation of long plain words.

    ``first_widths`` gives the widths of the opening lines (an indent, or room for a drop cap).
    """

    def fits(parts: list[str], available: float) -> bool:
        return stringWidth(" ".join(parts), font, size) <= available

    lines: list[str] = []
    current: list[str] = []
    queue = text.split(" ")
    while queue:
        word = queue.pop(0)
        available = first_widths[len(lines)] if len(lines) < len(first_widths) else width
        if fits([*current, word], available) or not current:
            current.append(word)
            continue
        match = HYPHENATABLE.match(word)
        if match:
            letters, punct = match.groups()
            for split in range(len(letters) - 3, 2, -1):
                head = letters[:split] + "-"
                if fits([*current, head], available):
                    lines.append(" ".join([*current, head]))
                    current = []
                    queue.insert(0, letters[split:] + punct)
                    break
            else:
                lines.append(" ".join(current))
                current = []
                queue.insert(0, word)
            continue
        lines.append(" ".join(current))
        current = []
        queue.insert(0, word)
    if current:
        lines.append(" ".join(current))
    return lines


def layout(variant: Variant, title: str, paragraphs: list[str]) -> tuple[list[PlacedLine], int]:
    width, height = variant.visible_size
    margin, size = variant.margin, variant.font_size
    column_width = (width - 2 * margin - (variant.columns - 1) * variant.gutter) / variant.columns

    title_baseline = height - margin - TITLE_SIZE
    title_width = stringWidth(title, TITLE_FONT, TITLE_SIZE)
    placed = [PlacedLine(1, (width - title_width) / 2, title_baseline, title, TITLE_FONT, TITLE_SIZE, "title", 0)]

    page, column = 1, 0
    bottom = variant.bottom_margin or margin
    frame_top = title_baseline - TITLE_GAP
    baseline = frame_top - size
    for number, text in enumerate(paragraphs, start=1):
        offsets = [variant.indent] if number > 1 else [0]  # book style: no indent after a heading
        if number == 1 and variant.book_style:
            # A two-line drop cap: the first letter, set large, with two narrowed lines beside it.
            cap_size = 2.8 * size
            cap_room = stringWidth(text[0], BODY_FONT, cap_size) + 2
            placed.append(
                PlacedLine(page, margin, baseline - variant.leading, text[0], BODY_FONT, cap_size, "drop_cap", number)
            )
            offsets, text = [cap_room, cap_room], text[1:]
        widths = [column_width - offset for offset in offsets]
        for index, line in enumerate(break_lines(text, widths, column_width, BODY_FONT, size)):
            if baseline < bottom:
                column += 1
                if column == variant.columns:
                    column, page = 0, page + 1
                    frame_top = height - margin
                baseline = frame_top - size
            offset = offsets[index] if index < len(offsets) else 0
            x = margin + column * (column_width + variant.gutter) + offset
            placed.append(PlacedLine(page, x, baseline, line, BODY_FONT, size, "body", number))
            baseline -= variant.leading

    for number in range(1, page + 1):
        if variant.book_style:
            footer_baseline = bottom - 30
            label = str(number)
            if number % 2 == 0:
                pieces = [(margin, label, "page_number"), (None, title, "footer")]
            else:
                pieces = [(margin, BOOK_FOOTER, "footer"), (None, label, "page_number")]
            for x, text, role in pieces:
                text_width = stringWidth(text, BODY_FONT, FOOTER_SIZE)
                x = width - margin - text_width if x is None else x
                placed.append(PlacedLine(number, x, footer_baseline, text, BODY_FONT, FOOTER_SIZE, role))
            continue
        if number > 1 or variant.header_on_first_page:
            header_width = stringWidth(title, HEADER_FONT, HEADER_SIZE)
            placed.append(
                PlacedLine(
                    number, (width - header_width) / 2, height - margin / 2, title, HEADER_FONT, HEADER_SIZE, "header"
                )
            )
        label = str(number)
        label_width = stringWidth(label, BODY_FONT, HEADER_SIZE)
        placed.append(
            PlacedLine(
                number,
                (width - label_width) / 2,
                margin / 2 - HEADER_SIZE,
                label,
                BODY_FONT,
                HEADER_SIZE,
                "page_number",
            )
        )
    return placed, page


def media_size(variant: Variant) -> tuple[float, float]:
    width, height = variant.visible_size
    if variant.rotate in (90, 270):
        width, height = height, width
    return width + 2 * variant.crop_inset, height + 2 * variant.crop_inset


def render(variant: Variant, placed: list[PlacedLine], pages: int, path: Path) -> None:
    media_width, media_height = media_size(variant)
    inset = variant.crop_inset
    # reportlab swaps the MediaBox sides itself for rotated pages, so it takes the displayed size.
    pagesize = (media_height, media_width) if variant.rotate in (90, 270) else (media_width, media_height)
    pdf = canvas.Canvas(str(path), pagesize=pagesize, invariant=1)
    pdf.setTitle(f"The Lantern Keeper ({variant.name})")
    pdf.setAuthor("Imagine Reader test fixtures")
    for number in range(1, pages + 1):
        if variant.rotate:
            pdf.setPageRotation(variant.rotate)
        if inset:
            pdf.setCropBox((inset, inset, media_width - inset, media_height - inset))
        pdf.saveState()
        if variant.rotate == 90:
            # Draw in display coordinates: displayed x is user y, displayed y is -user x.
            pdf.transform(0, 1, -1, 0, media_width, 0)
        elif variant.rotate:
            raise ValueError("only /Rotate 90 is generated")
        pdf.translate(inset, inset)
        for line in placed:
            if line.page == number:
                pdf.setFont(line.font, line.size)
                pdf.drawString(line.x, line.baseline, line.text)
        pdf.restoreState()
        pdf.showPage()
    pdf.save()


def truth(variant: Variant, title: str, paragraphs: list[str], placed: list[PlacedLine], pages: int) -> dict:
    width, height = variant.visible_size
    lines = []
    drop_cap = None
    for line in placed:
        if line.role == "drop_cap":
            drop_cap = line  # printed as the first word of the next body line
            continue
        text, x = line.text, line.x
        if drop_cap and line.role == "body":
            text, x, drop_cap = f"{drop_cap.text} {text}", drop_cap.x, None
        text_width = stringWidth(line.text, line.font, line.size)
        lines.append(
            {
                "page": line.page,
                "role": line.role,
                "paragraph": line.paragraph,
                "text": text,
                # Normalized to the visible page, origin at the top-left corner.
                "x0": round(x / width, 5),
                "x1": round((line.x + text_width) / width, 5),
                "baseline": round((height - line.baseline) / height, 5),
                "size": round(line.size / height, 5),
            }
        )
    return {
        "variant": asdict(variant),
        "story": str(STORY.relative_to(ROOT)),
        "page_count": pages,
        "paragraphs": [title, *paragraphs],
        "lines": lines,
    }


def scanned_copy(source: Path, path: Path, dpi: int = 150) -> None:
    """Write a one-page PDF whose only content is a picture of source page 1."""
    document = pdfium.PdfDocument(str(source))
    page = document[0]
    width, height = page.get_size()
    image = page.render(scale=dpi / 72).to_pil().convert("L")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    pdf = canvas.Canvas(str(path), pagesize=(width, height), invariant=1)
    pdf.setTitle("The Lantern Keeper (scanned page)")
    pdf.drawImage(ImageReader(buffer), 0, 0, width=width, height=height)
    pdf.showPage()
    pdf.save()
    document.close()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    title, paragraphs = read_story(STORY)
    for variant in VARIANTS:
        placed, pages = layout(variant, title, paragraphs)
        pdf_path = OUT / f"{variant.name}.pdf"
        render(variant, placed, pages, pdf_path)
        truth_path = OUT / f"{variant.name}.truth.json"
        truth_path.write_text(
            json.dumps(truth(variant, title, paragraphs, placed, pages), indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        hyphenated = sum(1 for line in placed if line.role == "body" and line.text.endswith("-"))
        print(f"{pdf_path.name}: {pages} pages, {hyphenated} hyphenated line ends")
    scanned_copy(OUT / "single-column.pdf", OUT / "scanned-page.pdf")
    print("scanned-page.pdf: 1 image-only page")


if __name__ == "__main__":
    main()

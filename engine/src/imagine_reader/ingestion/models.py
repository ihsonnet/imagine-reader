"""Data contracts produced by PDF ingestion.

Positions are fractions of the visible page: the CropBox, in the orientation
the page is displayed after its /Rotate value is applied, with the origin at
the top-left corner. A reader multiplies them by the rendered page size, so
highlights stay aligned at any zoom level.
"""

from typing import Literal

from pydantic import BaseModel

SCHEMA_VERSION = "0.1"

ExtractionMethod = Literal["native", "needs_ocr", "empty"]
LineRole = Literal["body", "header", "footer", "page_number"]


class Box(BaseModel):
    page: int  # 1-based page number
    x0: float
    top: float
    x1: float
    bottom: float


class Page(BaseModel):
    number: int
    width: float  # visible size in PDF points, as displayed
    height: float
    rotation: int
    method: ExtractionMethod
    word_count: int


class Word(BaseModel):
    id: str
    line_id: str
    text: str
    box: Box


class Line(BaseModel):
    id: str
    page: int
    role: LineRole
    text: str  # as printed, words joined by single spaces
    font_size: float  # points
    box: Box
    word_ids: list[str]


class TextEdit(BaseModel):
    """A tracked change between the printed words and the reading text."""

    kind: Literal["hyphen_joined", "hyphen_kept", "drop_cap_joined"]
    word_ids: list[str]
    printed: str  # e.g. "light- house", or "M y" for a drop cap
    reading: str  # e.g. "lighthouse", "My"


class Paragraph(BaseModel):
    id: str
    order: int
    line_ids: list[str]
    text: str  # reading text after the tracked edits
    edits: list[TextEdit]
    heading: bool = False  # set in larger type, like a chapter or story title


class Sentence(BaseModel):
    id: str
    order: int
    paragraph_id: str
    narration_text: str
    printed_text: str
    word_ids: list[str]
    boxes: list[Box]  # one per printed line the sentence touches, covering only its words


class Section(BaseModel):
    """A stretch of the book that starts at a heading, such as a chapter; readers pick one to play."""

    id: str
    title: str
    first_paragraph: int  # order of the first paragraph
    end_paragraph: int  # order one past the last paragraph
    page: int


class ExtractedDocument(BaseModel):
    schema_version: str = SCHEMA_VERSION
    extractor_version: str
    source_sha256: str
    pages: list[Page]
    lines: list[Line]
    words: list[Word]
    paragraphs: list[Paragraph]
    sentences: list[Sentence]
    sections: list[Section]
    warnings: list[str]

"""The slice of an extracted document that the reader UI loads.

The full extraction keeps every word and line; the reader needs pages,
sections, paragraphs, and sentences with their highlight boxes, plus where
the story starts (past the title page and publishing notes) and how it should
be read aloud.
"""

import functools
import json
import re
from pathlib import Path

from imagine_reader.ingestion import ExtractedDocument, story_start
from imagine_reader.narration import narration_style

from . import storage
from .config import Settings


def reading_view(book_id: str, document: ExtractedDocument) -> dict:
    view = {
        "book_id": book_id,
        "schema_version": document.schema_version,
        "pages": [
            {"number": page.number, "width": page.width, "height": page.height, "method": page.method}
            for page in document.pages
        ],
        "sections": [section.model_dump() for section in document.sections],
        "paragraphs": [
            {"id": paragraph.id, "order": paragraph.order, "text": paragraph.text, "heading": paragraph.heading}
            for paragraph in document.paragraphs
        ],
        "sentences": [
            {
                "id": sentence.id,
                "order": sentence.order,
                "paragraph_id": sentence.paragraph_id,
                "text": sentence.narration_text,
                "boxes": [
                    [box.page, box.x0, box.top, box.x1, box.bottom] for box in sentence.boxes
                ],  # compact: [page, x0, top, x1, bottom]
            }
            for sentence in document.sentences
        ],
        "warnings": document.warnings,
    }
    return add_story_details(view)


_ENGLISH = frozenset(
    "the and of to a in was he she it that i his her you had with for as on at but be not they is said".split()
)


def text_language(texts: list[str]) -> str:
    """ "bn" when most of the letters are Bengali script; "en" when the words are mostly English (a quarter or
    more of them among its commonest); else "" (the default narrator reads it)."""
    sample = " ".join(texts[:400])
    bengali = sum(1 for ch in sample if "\u0980" <= ch <= "\u09ff")
    letters = bengali + sum(1 for ch in sample if ch.isalpha() and not "\u0980" <= ch <= "\u09ff")
    if letters and bengali * 2 > letters:
        return "bn"
    words = re.findall(r"[a-z]+", sample.lower())
    return "en" if words and sum(word in _ENGLISH for word in words) * 4 >= len(words) else ""


def add_story_details(view: dict) -> dict:
    """Add ``start_section`` (where the story begins), ``narration_style`` ("story" or "plain"), and
    ``language`` ("bn" for Bangla, which has its own narrator)."""
    paragraphs = view["paragraphs"]
    sections = [
        (
            section["title"],
            " ".join(
                p["text"] for p in paragraphs[section["first_paragraph"] : section["end_paragraph"]] if not p["heading"]
            ),
        )
        for section in view["sections"]
    ]
    view["start_section"] = view["sections"][story_start(sections)]["id"] if sections else None
    view["narration_style"] = narration_style([sentence["text"] for sentence in view["sentences"]])
    view["language"] = text_language([sentence["text"] for sentence in view["sentences"]])
    return view


@functools.lru_cache(maxsize=16)
def _load(path: Path, _modified: int) -> dict:
    view = json.loads(path.read_text(encoding="utf-8"))
    if not {"start_section", "narration_style", "language"} <= view.keys():  # read before these were added
        add_story_details(view)
    return view


def load(settings: Settings, book_id: str) -> dict:
    """A book's reading view, with its story details. Shared between callers: do not change it."""
    path = storage.book_dir(settings, book_id) / storage.READING
    return _load(path, path.stat().st_mtime_ns)

"""The sample stories: one copy of each, read from the PDFs that come with the reader.

There is one in English and two in Bangla. Their voice and pictures (Gemini's
storyteller, and the default pencil pictures at the Auto pace) come ready-made
in a sample pack, downloaded once on first start (see ``sample_pack``), so the
reader can be tried before any key is added. The samples cannot be deleted.

A public demo may also keep them prepared in every picture style at every
pace (``IR_PREPARE_SAMPLES``), with its own keys: the worker does it one job
at a time, only while no other work is waiting and today's spending is under
half the daily limit.
"""

import hashlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import prepared, sketches, storage
from .config import Settings
from .db import Book
from .jobs import enqueue_ingest

HERE = Path(__file__).resolve().parent / "samples"


@dataclass(frozen=True)
class Sample:
    id: str
    pdf: Path
    name: str  # the file name visitors see in their list
    title: str
    language: str
    cover: Path | None = None  # a cover of its own, shown instead of its first page


SAMPLES = (
    Sample(
        "0" * 31 + "1",
        HERE / "the-lantern-keeper.pdf",
        "The Lantern Keeper (sample).pdf",
        "The Lantern Keeper",
        "en",
        HERE / "the-lantern-keeper-cover.webp",
    ),
    Sample("0" * 31 + "2", HERE / "devdas.pdf", "দেবদাস — শরৎচন্দ্র চট্টোপাধ্যায় (নমুনা).pdf", "দেবদাস", "bn"),
    Sample("0" * 31 + "3", HERE / "kabuliwala.pdf", "কাবুলিওয়ালা — রবীন্দ্রনাথ ঠাকুর (নমুনা).pdf", "কাবুলিওয়ালা", "bn"),
)
SAMPLE_BOOK_ID = SAMPLES[0].id


def own_cover(book_id: str) -> Path | None:
    """A sample's own cover, when it has one."""
    return next((sample.cover for sample in SAMPLES if sample.id == book_id and sample.cover), None)


@cache
def cover_version(book_id: str) -> str | None:
    """A short fingerprint of a sample's own cover, so a new cover is not served from a browser's old copy."""
    cover = own_cover(book_id)
    return hashlib.sha256(cover.read_bytes()).hexdigest()[:8] if cover else None


def ensure_samples(engine: Engine, settings: Settings) -> None:
    """Add each sample that is missing, and read again any whose reading failed."""
    for sample in SAMPLES:
        add_shared_book(engine, settings, sample)


def resume_samples(engine: Engine, settings: Settings) -> None:
    """Prepare the samples in every style and pace, a step at a time, when today's spending allows more."""
    for sample in SAMPLES:
        _resume(engine, settings, sample.id)


def add_shared_book(engine: Engine, settings: Settings, sample: Sample) -> None:
    """Add a sample if it is missing, or read it again if its reading failed."""
    with Session(engine) as db, db.begin():
        book = db.get(Book, sample.id)
        if book is not None and book.status != "failed":
            return
        data = sample.pdf.read_bytes()
        directory = storage.book_dir(settings, sample.id)
        directory.mkdir(parents=True, exist_ok=True)
        storage.write_bytes(directory / storage.SOURCE, data)
        if book is None:
            book = Book(
                id=sample.id,
                filename=sample.name,
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                shared=True,
                picture_style=settings.sketch_style,
                picture_words=0,  # Auto, as the sample pack has them
            )
            db.add(book)
        book.status, book.error_code, book.error_message = "queued", None, None
        db.flush()
        enqueue_ingest(db, sample.id)


def _resume(engine: Engine, settings: Settings, book_id: str) -> None:
    """Queue the next unfinished part of a sample: every style at every pace."""
    plans = [
        (style, words) for style in sketches.offered_styles(settings) for words in sketches.offered_words(settings)
    ]
    prepared.prepare_next(engine, settings, book_id, plans)

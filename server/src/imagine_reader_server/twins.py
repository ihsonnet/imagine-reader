"""Twins: the same PDF added again reuses what was already made from it.

Popular books, such as public-domain classics, are added by many visitors.
A book's reading of the story, its storyboards, reference portraits,
pictures, and voice are all made from its text alone, so a book with the same
file (the same SHA-256), read into the same text, can share them. A newer
reader can read a PDF differently (a Bangla book from Google Docs, once read
with its letters out of order), and then nothing is shared. The
files are hard-linked into the new book's folder: nothing is copied or paid
for twice, and each book still expires on its own. Preparation then finds them
already made, as it does for anything drawn or recorded before.

Only what the same text produces is shared, never who added a book or how far
they read.
"""

import hashlib
import logging
import os
import shutil
from functools import cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import storage
from .config import Settings
from .db import Book

log = logging.getLogger(__name__)

SHARED = (storage.STORY, storage.SCENES, storage.REFERENCES, storage.IMAGES, storage.AUDIO)


def share_from_twins(db: Session, settings: Settings, book: Book) -> int:
    """Link into this book what its twins have made that it has not; the number of files linked."""
    if book.status != "ready" or book.page_count is None:
        return 0
    twins = db.scalars(
        select(Book)
        .where(
            Book.sha256 == book.sha256,
            Book.id != book.id,
            Book.status == "ready",
            Book.page_count == book.page_count,
            Book.word_count == book.word_count,
        )
        .order_by(Book.created_at.desc())
    )
    target = storage.book_dir(settings, book.id)
    linked = 0
    for twin in twins:
        source = storage.book_dir(settings, twin.id)
        if not source.is_dir() or not _same_text(source, target):
            continue
        for name in SHARED:
            linked += _link(source / name, target / name)
    if linked:
        log.info("book %s shares %d files with an earlier copy of the same PDF", book.id, linked)
    return linked


def _link(source: Path, target: Path) -> int:
    """Hard-link a file, or every file under a folder, where the target has none yet."""
    if source.is_file():
        if target.exists():
            return 0
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, target)
        except OSError:  # another file system: copy instead
            shutil.copy2(source, target)
        return 1
    if source.is_dir():
        return sum(_link(child, target / child.name) for child in source.iterdir())
    return 0


def _same_text(source: Path, target: Path) -> bool:
    """Both books were read into the same document, so what one made from its text fits the other."""
    mine = _digest(target / storage.DOCUMENT)
    return mine is not None and _digest(source / storage.DOCUMENT) == mine


def _digest(path: Path) -> str | None:
    try:
        return _file_digest(path, path.stat().st_mtime_ns)
    except OSError:
        return None


@cache
def _file_digest(path: Path, modified: int) -> str:  # a document is written once, when its book is read
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()

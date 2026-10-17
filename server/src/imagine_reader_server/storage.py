"""Where each book's private files live: data/server/books/<book id>/."""

import json
import os
import re
import shutil
from pathlib import Path

from .config import Settings

SOURCE = "source.pdf"
DOCUMENT = "document.json"  # full extraction, for later pipeline stages
READING = "reading.json"  # the slice the reader UI loads
RESULT = "result.json"  # outcome of the ingest job
AUDIO = "audio"  # narration clips, named by cache key
IMAGES = "images"  # sketches, named by cache key
STORY = "story.json"  # the story bible from whole-book analysis
SCENES = "scenes"  # paid scene plans (storyboards), by bible and brain, a file per section
REFERENCES = "references"  # a reference picture of each main character, per drawing style
COVER = "cover-360.webp"  # page 1, small, for the libraries; named for its width, so older ones are drawn again
COVER_WIDTH = 360  # pixels; shown at up to about 180 px in the grid of covers, sharp on high-density screens


def write_cover(folder: Path) -> Path:
    """Draw the first page of the book's PDF, small, as its cover in the list of books."""
    import pypdfium2  # only needed here; the API and the ingest child both have it

    pdf = pypdfium2.PdfDocument(folder / SOURCE)
    try:
        page = pdf[0]
        width = page.get_width()
        image = page.render(scale=COVER_WIDTH / width if width else 1).to_pil().convert("RGB")
    finally:
        pdf.close()
    path = folder / COVER
    temporary = path.with_suffix(".tmp")
    image.save(temporary, "WEBP", quality=78)
    os.replace(temporary, path)
    return path


_BOOK_ID = re.compile(r"^[0-9a-f]{32}$")
MEDIA_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".webp": "image/webp", ".png": "image/png"}
EXTENSIONS = {media_type: suffix for suffix, media_type in MEDIA_TYPES.items()}


def book_dir(settings: Settings, book_id: str) -> Path:
    if not _BOOK_ID.match(book_id):  # ids reach the filesystem, so never trust their shape
        raise ValueError("invalid book id")
    return settings.data_dir / "books" / book_id


def delete_book_files(settings: Settings, book_id: str) -> None:
    shutil.rmtree(book_dir(settings, book_id), ignore_errors=True)


def find_asset(folder: Path, key: str) -> Path | None:
    """A cached clip or picture by its cache key, in whichever format its provider produced."""
    for suffix in MEDIA_TYPES:
        path = folder / f"{key}{suffix}"
        if path.exists():
            return path
    return None


def write_json(path: Path, data: object) -> None:
    write_bytes(path, json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())


def write_bytes(path: Path, data: bytes) -> None:
    """Write atomically, so a reader never sees half a file."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)

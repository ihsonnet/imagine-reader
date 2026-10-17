"""The sample pack: the samples' voice and pictures, ready-made, so the reader can be tried before any key is added.

On first start the worker downloads the pack (``IR_SAMPLE_PACK_URL``, a release asset of the project; a local
path works too), checks it (``IR_SAMPLE_PACK_SHA256``, when set), and installs it once every sample has been read:
for each sample, its reading of the text (so its sentences match its recordings exactly), the story's cast and
storyboards, every picture of its default plan (pencil, at the Auto pace), and Gemini's storyteller reading every
chapter. A sample whose PDF is not the one the pack was made from is left out; it is then prepared with your key,
like any book, when you read it.

The pack is made from a reader that has prepared the samples, with ``tools/make_sample_pack.py``.

Its files are taken by name only (books/<sample id>/<kind>/<file>), never by the paths the archive gives, so a
damaged or hostile pack cannot write anywhere else.
"""

import hashlib
import json
import logging
import re
import shutil
import tarfile
import threading
import time
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import storage
from .config import Settings
from .db import Book, NarrationClip, SceneRecord

log = logging.getLogger(__name__)

MARKER = "sample-pack.json"  # in the data folder, once the pack is installed
PACK_VERSION = 1
MAX_PACK_BYTES = 1024 * 1024 * 1024
RETRY_AFTER_SECONDS = 3600  # a failed download or install is tried again an hour later
# The files a pack may hold for a sample, by kind: their names only, checked against these patterns.
_FILES = {
    "": re.compile(r"(reading|document|story)\.json"),
    "scenes": re.compile(r"[A-Za-z0-9._-]+--[A-Za-z0-9._-]+/sec-\d{3}\.json"),
    "images": re.compile(r"[0-9a-f]{32}\.(webp|png)"),
    "audio": re.compile(r"[0-9a-f]{32}\.(mp3|wav)"),
}
_MEMBER = re.compile(r"books/([0-9a-f]{32})/(?:(scenes|images|audio)/)?(.+)")
_last_try = 0.0
_installer: threading.Thread | None = None


def pack_pending(settings: Settings) -> bool:
    """Whether the samples wait for their pack: nothing is made for them meanwhile, so no key pays for what the pack
    brings. Without a pack (``IR_SAMPLE_PACK_URL`` empty), the samples are prepared like any book."""
    return bool(settings.samples and settings.sample_pack_url) and not (settings.data_dir / MARKER).exists()


def install_in_background(engine: Engine, settings: Settings) -> None:
    """Install the pack in a thread of its own, so a long download holds up no book."""
    global _installer
    if not pack_pending(settings) or (_installer is not None and _installer.is_alive()):
        return
    _installer = threading.Thread(target=install_sample_pack, args=(engine, settings), name="sample-pack", daemon=True)
    _installer.start()


def install_sample_pack(engine: Engine, settings: Settings) -> bool:
    """Install the sample pack once, when every sample has been read; whether it is installed."""
    global _last_try
    if (settings.data_dir / MARKER).exists():
        return True
    if not settings.sample_pack_url or time.monotonic() - _last_try < RETRY_AFTER_SECONDS and _last_try:
        return False
    from .sample import SAMPLES  # here, not above: the samples' module reaches the job queue, which reaches this

    with Session(engine) as db:
        books = [db.get(Book, sample.id) for sample in SAMPLES]
    if not all(book is not None and book.status == "ready" for book in books):
        return False  # read first: the pack's files go beside the samples' own
    _last_try = time.monotonic()
    try:
        archive = _fetch(settings)
        installed = install_from(engine, settings, archive)
    except Exception:
        log.warning("could not install the sample pack from %s; trying again later", settings.sample_pack_url,
                    exc_info=True)  # fmt: skip
        return False
    storage.write_json(settings.data_dir / MARKER, {"url": settings.sample_pack_url, "samples": installed})
    log.info("installed the sample pack: %s", ", ".join(installed) or "nothing that matched")
    return True


def _fetch(settings: Settings) -> Path:
    """The pack, downloaded into the data folder (or a local file, used where it is), checked when a sum is set."""
    source = settings.sample_pack_url
    if not source.startswith(("https://", "http://")):
        path = Path(source.removeprefix("file://"))
    else:
        path = settings.data_dir / "sample-pack.tar.gz"
        partial = path.with_suffix(".part")
        with httpx.stream("GET", source, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            size = 0
            with partial.open("wb") as out:
                for chunk in response.iter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_PACK_BYTES:
                        raise ValueError("the sample pack is larger than expected")
                    out.write(chunk)
        partial.replace(path)
    if settings.sample_pack_sha256:
        digest = hashlib.sha256()
        with path.open("rb") as data:
            for chunk in iter(lambda: data.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != settings.sample_pack_sha256.lower():
            raise ValueError("the sample pack's checksum does not match IR_SAMPLE_PACK_SHA256")
    return path


def install_from(engine: Engine, settings: Settings, archive: Path) -> list[str]:
    """Install a pack's samples whose PDF matches; the titles installed."""
    from .sample import SAMPLES

    samples = {sample.id: sample for sample in SAMPLES}
    installed = []
    with tarfile.open(archive, "r:gz") as pack:
        manifest = json.loads(_read(pack, "manifest.json"))
        if manifest.get("version") != PACK_VERSION:
            raise ValueError(f"sample pack version {manifest.get('version')!r}, expected {PACK_VERSION}")
        members = {member.name: member for member in pack.getmembers() if member.isfile()}
        for entry in manifest.get("books", []):
            sample = samples.get(entry.get("id", ""))
            if sample is None:
                continue
            if hashlib.sha256(sample.pdf.read_bytes()).hexdigest() != entry.get("pdf_sha256"):
                log.info("the sample pack's %s was made from another PDF; it is prepared with your key instead",
                         sample.title)  # fmt: skip
                continue
            _install_files(pack, members, settings, sample.id)
            _install_rows(engine, sample.id, entry)
            installed.append(sample.title)
    return installed


def _read(pack: tarfile.TarFile, name: str) -> bytes:
    member = pack.getmember(name)
    if not member.isfile():
        raise ValueError(f"{name} in the sample pack is not a file")
    return pack.extractfile(member).read()


def _install_files(pack: tarfile.TarFile, members: dict, settings: Settings, book_id: str) -> None:
    folder = storage.book_dir(settings, book_id)
    for name, member in members.items():
        match = _MEMBER.fullmatch(name)
        if match is None or match.group(1) != book_id:
            continue
        kind, rest = match.group(2) or "", match.group(3)
        if not _FILES[kind].fullmatch(rest) or ".." in rest:
            raise ValueError(f"unexpected file in the sample pack: {name}")
        target = folder / kind / rest if kind else folder / rest
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        with pack.extractfile(member) as source, partial.open("wb") as out:
            shutil.copyfileobj(source, out)
        partial.replace(target)


def _install_rows(engine: Engine, book_id: str, entry: dict) -> None:
    """The sample's scenes and clips, ready, in place of any it had (its pictures and voice are the pack's now)."""
    with Session(engine) as db, db.begin():
        for row in entry.get("scenes", []):
            scene = db.scalar(
                select(SceneRecord).where(
                    SceneRecord.book_id == book_id,
                    SceneRecord.plan == row["plan"],
                    SceneRecord.scene_id == row["scene_id"],
                )
            )
            if scene is None:
                scene = SceneRecord(book_id=book_id, plan=row["plan"], scene_id=row["scene_id"])
                db.add(scene)
            scene.section_id, scene.first_sentence, scene.last_sentence = (
                row["section_id"], row["first_sentence"], row["last_sentence"]
            )  # fmt: skip
            scene.spec, scene.cache_key, scene.rendered_style = row["spec"], row["cache_key"], row.get("rendered_style")
            scene.status, scene.error_code, scene.attempts = "ready", None, 0
        for row in entry.get("clips", []):
            clip = db.scalar(
                select(NarrationClip).where(
                    NarrationClip.book_id == book_id,
                    NarrationClip.sentence_id == row["sentence_id"],
                    NarrationClip.voice == row["voice"],
                )
            )
            if clip is None:
                clip = NarrationClip(book_id=book_id, sentence_id=row["sentence_id"], voice=row["voice"])
                db.add(clip)
            clip.section_id, clip.order, clip.text = row["section_id"], row["order"], row["text"]
            clip.cache_key, clip.pause_after, clip.duration = row["cache_key"], row["pause_after"], row["duration"]
            clip.status, clip.error_code, clip.attempts = "ready", None, 0

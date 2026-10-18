"""Durable background jobs.

Jobs are rows in SQLite, so a restarted worker picks up where it left off.
Each PDF is parsed in a fresh child process with a time limit and a memory
cap: a hostile or broken file can fail its own job but cannot hang or crash
the worker. Once a PDF is read, the voice and pictures of the chapter the
reader will open at are prepared right away. Logs carry book ids and outcome
codes, never book text.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import resource
import sys
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from imagine_reader.ingestion import IngestionError, Limits, ingest_pdf

from . import narration, reading, sketches, storage, twins
from .config import Settings
from .db import Book, Job, utcnow

log = logging.getLogger(__name__)

_EXIT_OK = 0
_EXIT_REJECTED = 2  # the PDF was understood and refused (limits, encryption, damage)


PREPARATION = ("narrate", "sketch")  # jobs a reader starts with Play and may cancel


TRIMMABLE = ("too_many_pages", "too_many_words")  # read again, up to the limits


def enqueue_ingest(db: Session, book_id: str, trim: bool = False) -> None:
    """Queue reading a PDF; with ``trim``, one too long is read up to the limits."""
    db.add(Job(book_id=book_id, kind="ingest", state="queued", params={"trim": True} if trim else None))


def claim_next(engine: Engine) -> tuple[int, str, str, dict | None] | None:
    """Atomically move the oldest queued job to running; return (job id, book id, kind, params)."""
    oldest = select(Job.id).where(Job.state == "queued").order_by(Job.id).limit(1).scalar_subquery()
    with Session(engine) as db, db.begin():
        row = db.execute(
            update(Job)
            .where(Job.id == oldest, Job.state == "queued")
            .values(state="running", attempts=Job.attempts + 1, started_at=utcnow())
            .returning(Job.id, Job.book_id, Job.kind, Job.params)
        ).first()
        if row is None:
            return None
        if row.kind == "ingest":
            db.execute(update(Book).where(Book.id == row.book_id).values(status="processing"))
        return row.id, row.book_id, row.kind, row.params


def recover_interrupted(engine: Engine, settings: Settings) -> None:
    """Requeue jobs a stopped worker left running, or fail them after too many attempts."""
    with Session(engine) as db, db.begin():
        for job in list(db.scalars(select(Job).where(Job.state == "running"))):
            if job.attempts < settings.max_job_attempts:
                job.state = "queued"
                if job.kind == "ingest":
                    db.execute(update(Book).where(Book.id == job.book_id).values(status="queued"))
            else:
                _finish(db, job, "failed", "interrupted", "Processing was interrupted. Please upload the book again.")
            log.info("recovered job %s for book %s as %s", job.id, job.book_id, job.state)


def process_next(engine: Engine, settings: Settings) -> bool:
    """Run one queued job. Returns False when the queue is empty."""
    claimed = claim_next(engine)
    if claimed is None:
        return False
    run_claimed(engine, settings, claimed)
    return True


def run_claimed(engine: Engine, settings: Settings, claimed: tuple[int, str, str, dict | None]) -> None:
    """Run a job taken with ``claim_next``. An unexpected error fails that job, not the worker."""
    job_id, book_id, kind, params = claimed
    try:
        if kind in PREPARATION:
            _run_preparation(engine, settings, job_id, book_id, kind, params or {})
        else:
            _ingest(engine, settings, job_id, book_id, settings.limits, bool((params or {}).get("trim")))
    except Exception:
        log.exception("job %s (%s) for book %s failed unexpectedly", job_id, kind, book_id)
        with Session(engine) as db, db.begin():
            job = db.get(Job, job_id)
            if job is not None and job.state == "running":
                _finish(db, job, "failed", "internal_error", "This PDF could not be read.")


def _run_preparation(engine: Engine, settings: Settings, job_id: int, book_id: str, kind: str, params: dict) -> None:
    run = narration.run_job if kind == "narrate" else sketches.run_job
    state, code = run(engine, settings, job_id, book_id, params)
    with Session(engine) as db, db.begin():
        job = db.get(Job, job_id)
        if job is not None and job.state == "running":  # a cancelled job keeps its state
            _finish(db, job, state, code, None)
    log.info("job %s for book %s finished: %s %s", job_id, book_id, state, code or "")


def _ingest(
    engine: Engine,
    settings: Settings,
    job_id: int,
    book_id: str,
    limits: Limits,
    trim: bool = False,
) -> None:
    state, code, message, result = _run_ingest(settings, book_id, limits, trim)
    with Session(engine) as db, db.begin():
        job = db.get(Job, job_id)
        book = db.get(Book, book_id)
        if job is None or book is None:  # the book was deleted while it was being read
            storage.delete_book_files(settings, book_id)
            return True
        _finish(db, job, state, code, message)
        if state == "done":
            book.status = "ready"
            book.page_count = result["page_count"]
            book.word_count = result["word_count"]
            twins.share_from_twins(db, settings, book)  # the same PDF added before: reuse what it made
            if settings.prepare_on_upload and not book.shared:  # the samples' come in the sample pack
                _prepare_opening(db, settings, book)
    log.info("job %s for book %s finished: %s %s", job_id, book_id, state, code or "")


def _prepare_opening(db: Session, settings: Settings, book: Book) -> None:
    """Queue the pictures and voice of the chapter the book opens at, so they are ready when it opens."""
    start = reading.load(settings, book.id)["start_section"]
    if start is None:
        return
    sketches.request(db, settings, book, start, style=book.picture_style, words=book.picture_words)
    narration.request(db, settings, book, start, narration.section_voice(settings, book, start))


def cancel_preparation(db: Session, book_id: str) -> int:
    """Stop a book's queued and running narration and sketch jobs. Unfinished work can be requested again."""
    result = db.execute(
        update(Job)
        .where(Job.book_id == book_id, Job.kind.in_(PREPARATION), Job.state.in_(("queued", "running")))
        .values(state="cancelled")
    )
    return result.rowcount


def _finish(db: Session, job: Job, state: str, code: str | None, message: str | None) -> None:
    job.state = state
    job.error_code = code
    job.finished_at = utcnow()
    if state == "failed" and job.kind == "ingest":  # narration problems never fail the book itself
        db.execute(
            update(Book).where(Book.id == job.book_id).values(status="failed", error_code=code, error_message=message)
        )


def _run_ingest(
    settings: Settings, book_id: str, limits: Limits, trim: bool = False
) -> tuple[str, str | None, str | None, dict]:
    directory = storage.book_dir(settings, book_id)
    (directory / storage.RESULT).unlink(missing_ok=True)  # never read a previous attempt's outcome
    context = multiprocessing.get_context("spawn")  # a clean interpreter, sharing nothing with the worker
    child = context.Process(
        target=_ingest_child,
        args=(str(directory), limits, settings.job_memory_mb * 1024 * 1024, trim),
        daemon=True,
    )
    child.start()
    child.join(settings.job_timeout_seconds)
    if child.is_alive():
        child.kill()
        child.join()
        return "failed", "timeout", "Reading this PDF took too long.", {}

    result_path = directory / storage.RESULT
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    if child.exitcode == _EXIT_OK and "page_count" in result:
        return "done", None, None, result
    if child.exitcode == _EXIT_REJECTED and "code" in result:
        return "failed", result["code"], result["message"], result
    return "failed", "processing_failed", "This PDF could not be read.", result


def _ingest_child(directory: str, limits: Limits, memory_bytes: int, trim: bool = False) -> None:
    """Runs in the child process."""
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    folder = Path(directory)
    book_id = folder.name
    try:
        document = ingest_pdf(folder / storage.SOURCE, limits, trim=trim)
    except IngestionError as error:
        storage.write_json(folder / storage.RESULT, {"code": error.code, "message": error.message})
        sys.exit(_EXIT_REJECTED)
    storage.write_json(folder / storage.DOCUMENT, document.model_dump(mode="json"))
    storage.write_json(folder / storage.READING, reading.reading_view(book_id, document))
    try:
        storage.write_cover(folder)
    except Exception:  # a cover is a nicety; the book reads without one
        pass
    storage.write_json(
        folder / storage.RESULT,
        {
            "page_count": len(document.pages),
            "word_count": sum(len(sentence.word_ids) for sentence in document.sentences),
        },
    )
    sys.exit(_EXIT_OK)

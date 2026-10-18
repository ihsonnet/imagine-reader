"""HTTP API for the reader.

It runs on your own computer: every book on it is yours, kept until you delete it, and its voice and pictures are
made with your own keys (``.env``). A public demo turns uploads off (``IR_UPLOADS=false``) and offers only the
samples. Book routes answer 404 for a book that does not exist.
"""

import hashlib
import re
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePath
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from imagine_reader.narration import NarrationManifest
from imagine_reader.story import SketchManifest

from . import narration, project, reading, sketches, spending, storage, study
from .config import Settings
from .db import Book, Job, NarrationClip, SceneRecord, make_engine, utcnow
from .jobs import PREPARATION, TRIMMABLE, cancel_preparation, enqueue_ingest
from .limits import RateLimiter, StorageMeter, client_address
from .prepared import RESUME_MARGIN_USD
from .sample import SAMPLE_BOOK_ID, SAMPLES, cover_version, ensure_samples, own_cover

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# PDF.js needs WebAssembly for some image formats and blob: URLs for fonts and its worker; React sets highlight
# positions as inline styles. Everything else comes from this server: the AI is reached by the server, never the page.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' blob: data:; font-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; "
    "worker-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
_MULTIPART_OVERHEAD = 64 * 1024
# Why preparing stopped early: the daily spending limit, a key refused (no credit), or the book's budget.
STOPPED_BY = ("daily_limit", "provider_account", "budget_exceeded")
UNAVAILABLE_FOR = timedelta(minutes=15)  # after an AI service refused the key


class Preparation(BaseModel):
    """How far the voice and pictures of the chapter a book opens at have come."""

    section_id: str
    pictures_ready: int
    pictures_planned: int  # 0 until the scenes are planned
    clips_ready: int
    clips_planned: int
    active: bool  # still being prepared
    stopped: str | None = None  # why it stopped early: daily_limit, provider_account, or budget_exceeded


class BookOut(BaseModel):
    id: str
    filename: str
    status: str
    error_code: str | None
    error_message: str | None
    page_count: int | None
    word_count: int | None
    created_at: datetime
    start_section: str | None = None  # where the story begins, past the title page and notes
    cover_url: str | None = None  # page 1, small, once the book is read
    preparation: Preparation | None = None
    shared: bool = False  # a sample: not yours to change or delete
    mine: bool = False  # one you added: yours to change or delete
    picture_style: str = "pencil"  # the pictures its opening chapter is prepared with
    picture_words: int = 0


class NarrationRequest(BaseModel):
    section_id: str
    voice: str | None = None
    from_sentence: int | None = None  # where the reader is; preparation starts there


class SketchRequest(BaseModel):
    section_id: str
    from_sentence: int | None = None
    style: str | None = None  # one of the styles offered (/api/info); the default when left out
    words: int | None = None  # a new picture after about this many words, 0 for Auto


class _TooLarge(Exception):
    pass


def _cover_url(book_id: str) -> str:
    version = cover_version(book_id)  # a sample's own cover: its fingerprint, so a new one is fetched afresh
    return f"/api/books/{book_id}/cover" + (f"?v={version}" if version else "")


def _out(book: Book, db: Session | None = None, settings: Settings | None = None) -> BookOut:
    start, preparation = None, None
    if db is not None and settings is not None and book.status == "ready":
        start = reading.load(settings, book.id)["start_section"]
        preparation = _preparation(db, settings, book, start) if start else None
    return BookOut(
        id=book.id,
        filename=book.filename,
        status=book.status,
        error_code=book.error_code,
        error_message=book.error_message,
        page_count=book.page_count,
        word_count=book.word_count,
        created_at=book.created_at.replace(tzinfo=UTC),
        start_section=start,
        cover_url=_cover_url(book.id) if book.status == "ready" else None,
        preparation=preparation,
        shared=book.shared,
        mine=not book.shared,
        picture_style=book.picture_style,
        picture_words=book.picture_words,
    )


def _preparation(db: Session, settings: Settings, book: Book, section_id: str) -> Preparation:
    def counts(model, *where) -> tuple[int, int]:
        rows = db.execute(
            select(model.status, func.count())
            .where(model.book_id == book.id, model.section_id == section_id, *where)
            .group_by(model.status)
        ).all()
        return sum(n for status, n in rows if status == "ready"), sum(n for _, n in rows)

    plan = sketches.plan_for(settings, book.picture_style, book.picture_words, book.shared)
    pictures_ready, pictures_planned = counts(SceneRecord, SceneRecord.plan == plan.id)
    voice = narration.section_voice(settings, book, section_id)
    clips_ready, clips_planned = counts(NarrationClip, NarrationClip.voice == voice)
    jobs = list(
        db.scalars(select(Job).where(Job.book_id == book.id, Job.kind.in_(PREPARATION)).order_by(Job.id.desc()))
    )
    for_opening = [
        job
        for job in jobs
        if (job.params or {}).get("section_id") == section_id
        and (job.kind == "narrate" or plan.matches(job.params, settings))
    ]
    latest = {kind: next((job for job in for_opening if job.kind == kind), None) for kind in PREPARATION}
    stopped = next(
        (job.error_code for job in latest.values() if job and job.state == "failed" and job.error_code in STOPPED_BY),
        None,
    )
    return Preparation(
        section_id=section_id,
        pictures_ready=pictures_ready,
        pictures_planned=pictures_planned,
        clips_ready=clips_ready,
        clips_planned=clips_planned,
        active=any(job.state in ("queued", "running") for job in for_opening),  # later chapters do not count
        stopped=stopped,
    )


def _save_upload(upload: UploadFile, path: Path, maximum: int) -> tuple[int, str, bytes]:
    digest, size, head = hashlib.sha256(), 0, b""
    with path.open("wb") as out:
        while chunk := upload.file.read(1024 * 1024):
            size += len(chunk)
            if size > maximum:
                raise _TooLarge
            head += chunk[: max(0, 1024 - len(head))]
            digest.update(chunk)
            out.write(chunk)
    return size, digest.hexdigest(), head


def _services(settings: Settings) -> dict:
    """Who makes what with the keys set, for the reader to show (and to say when a key is missing)."""
    voice = narration.speech_provider(settings)
    pictures = sketches.image_provider(settings)

    def route(provider_id: str) -> str:
        if provider_id.startswith("mock"):
            return "placeholders"
        if provider_id.startswith("gemini"):
            return "openrouter" if settings.openrouter_api_key else "gemini" if settings.gemini_api_key else "none"
        return provider_id.split("-", 1)[0]  # openai | cloudflare | openrouter

    return {
        "planning": "openrouter" if settings.openrouter_api_key else "openai" if settings.openai_api_key else "rules",
        "voice": route(voice.id),
        "pictures": route(pictures.id),
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings)
    app = FastAPI(title="Imagine-Reader API", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings = settings
    app.state.engine = engine
    if settings.samples:
        ensure_samples(engine, settings)
    if settings.study_dir is not None:
        study.install(engine, settings)
    limiter = RateLimiter()
    storage_meter = StorageMeter(settings.data_dir / "books")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        length = request.headers.get("content-length", "")
        if (
            request.method == "POST"
            and length.isdigit()
            and int(length) > settings.max_upload_bytes + _MULTIPART_OVERHEAD
        ):
            return JSONResponse({"detail": "The file is larger than the upload limit."}, status_code=413)
        visitor = client_address(request, settings)
        if request.url.path.startswith("/api/") and not limiter.allow(
            f"requests:{visitor}", settings.requests_per_minute, 60
        ):
            return JSONResponse(
                {"detail": "Too many requests. Please slow down."}, status_code=429, headers={"Retry-After": "60"}
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        return response

    def check_new_book(request: Request, db: Session) -> None:
        """Limits on adding a book: uploads on, per visitor, and for the whole reader."""
        if not settings.uploads:
            raise HTTPException(403, "This reader offers its samples only. Run your own to read your books.")
        if not limiter.allow(f"uploads:{client_address(request, settings)}", settings.uploads_per_hour, 3600):
            raise HTTPException(429, "You have added many books recently. Please try again in an hour.")
        waiting = db.scalar(select(func.count()).where(Job.kind == "ingest", Job.state == "queued"))
        if waiting >= settings.max_queued_ingests or storage_meter.megabytes() >= settings.max_storage_mb:
            raise HTTPException(503, "The reader is busy, or its storage is full. Please try again later.")

    def check_preparation(request: Request) -> None:
        if not limiter.allow(f"preparations:{client_address(request, settings)}", settings.preparations_per_hour, 3600):
            raise HTTPException(429, "Too many narration or picture requests. Please try again later.")

    def database() -> Iterator[Session]:
        with Session(engine) as db:
            yield db

    Database = Annotated[Session, Depends(database)]

    def readable_book(book_id: str, db: Database) -> Book:
        """A book on this reader: a sample, or one you added."""
        book = db.get(Book, book_id) if re.fullmatch(r"[0-9a-f]{32}", book_id) else None
        if book is None:
            raise HTTPException(404, "Book not found.")
        return book

    def owned_book(book: Annotated[Book, Depends(readable_book)]) -> Book:
        """A book you added, yours to change; never a sample."""
        if book.shared:
            raise HTTPException(403, "A sample comes with the reader, so it cannot be changed.")
        return book

    OwnedBook = Annotated[Book, Depends(readable_book)]  # reading it, and preparing it
    MyBook = Annotated[Book, Depends(owned_book)]
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @router.get("/info")
    def info(db: Database) -> dict:
        """What the reader should know: who makes the voice and pictures, the choices, and the limits."""
        return {
            "ai_status": ai_status(db),  # ok, daily_limit, or unavailable: whether voice and pictures can be made now
            "services": _services(settings),  # planning, voice, pictures: openrouter | gemini | openai | cloudflare
            "uploads": settings.uploads,
            "max_upload_mb": settings.max_upload_bytes // (1024 * 1024),
            "max_pages": settings.max_pages,
            "daily_limit_usd": settings.daily_spend_limit_usd,
            "sample_book_id": SAMPLE_BOOK_ID if settings.samples else None,
            "samples": [{"id": sample.id, "title": sample.title, "language": sample.language} for sample in SAMPLES]
            if settings.samples
            else [],
            "picture_styles": sketches.offered_styles(settings),
            "picture_words": sketches.offered_words(settings),
            "default_style": settings.sketch_style,
            "default_words": sketches.default_words(settings),
            "study": settings.study_dir is not None,  # the study build: its console is at /#/study
        }

    @router.get("/project")
    def project_home() -> dict:
        """Where the project lives, and its stars on GitHub (left out when unknown)."""
        return {
            "repository": project.repository_url(settings.project_repository),
            "stars": project.stars(settings.project_repository),
        }

    @router.post("/books", status_code=201)
    def upload_book(
        request: Request,
        file: UploadFile,
        db: Database,
        style: Annotated[str | None, Form()] = None,
        words: Annotated[int | None, Form()] = None,
    ) -> BookOut:
        """Add a PDF. ``style`` and ``words`` choose the pictures its opening chapter is prepared with."""
        check_new_book(request, db)
        plan = picture_plan(style, words)
        book_id = secrets.token_hex(16)
        directory = storage.book_dir(settings, book_id)
        directory.mkdir(parents=True)
        try:
            size, digest, head = _save_upload(file, directory / storage.SOURCE, settings.max_upload_bytes)
        except _TooLarge:
            storage.delete_book_files(settings, book_id)
            raise HTTPException(413, "The file is larger than the upload limit.") from None
        if b"%PDF-" not in head:
            storage.delete_book_files(settings, book_id)
            raise HTTPException(415, "Only PDF files are supported.")
        name = _CONTROL.sub("", PurePath(file.filename or "book.pdf").name)[:200] or "book.pdf"
        book = Book(
            id=book_id,
            filename=name,
            size_bytes=size,
            sha256=digest,
            status="queued",
            picture_style=plan.style,
            picture_words=plan.words,
        )
        db.add(book)
        db.flush()
        enqueue_ingest(db, book_id)
        db.commit()
        return _out(book)

    @router.get("/books")
    def list_books(db: Database) -> list[BookOut]:
        """Your books, newest first, then the samples."""
        order = (Book.shared, case((Book.shared, Book.id), else_=""), Book.created_at.desc())  # samples in order
        books = db.scalars(select(Book).order_by(*order)).all()
        if settings.study_dir is None:  # the study's passages are shared too, but not for this list
            samples = {sample.id for sample in SAMPLES}
            books = [book for book in books if not book.shared or book.id in samples]
        return [_out(book, db, settings) for book in books]

    @router.get("/books/{book_id}")
    def get_book(book: OwnedBook, db: Database) -> BookOut:
        return _out(book, db, settings)

    @router.post("/books/{book_id}/trim", status_code=202)
    def trim_book(book: MyBook, db: Database) -> BookOut:
        """Read a book that was too long again, up to the page and word limits."""
        if book.status != "failed" or book.error_code not in TRIMMABLE:
            raise HTTPException(409, "Only a book that was too long can be trimmed.")
        waiting = db.scalar(select(func.count()).where(Job.kind == "ingest", Job.state == "queued"))
        if waiting >= settings.max_queued_ingests:
            raise HTTPException(503, "The reader is busy right now. Please try again in a little while.")
        book.status, book.error_code, book.error_message = "queued", None, None
        enqueue_ingest(db, book.id, trim=True)
        db.commit()
        return _out(book)

    @router.get("/books/{book_id}/reading")
    def get_reading(book: OwnedBook) -> JSONResponse:
        if book.status != "ready":
            raise HTTPException(409, "The book is not ready yet.")
        return JSONResponse(reading.load(settings, book.id), headers={"Cache-Control": "private, no-cache"})

    @router.get("/books/{book_id}/cover")
    def get_cover(book: OwnedBook) -> FileResponse:
        """The book's first page, small, or a sample's own cover. Books read before covers existed get theirs on
        first asking."""
        folder = storage.book_dir(settings, book.id)
        path = own_cover(book.id) or folder / storage.COVER
        if book.status != "ready":
            raise HTTPException(404, "No cover yet.")
        if not path.exists():
            try:
                storage.write_cover(folder)
            except Exception:
                raise HTTPException(404, "No cover for this book.") from None
        return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})

    @router.get("/books/{book_id}/source.pdf")
    def get_source(book: OwnedBook) -> FileResponse:
        path = storage.book_dir(settings, book.id) / storage.SOURCE
        return FileResponse(
            path,
            media_type="application/pdf",
            headers={"Cache-Control": "private, max-age=3600", "Content-Disposition": "inline"},
        )

    @router.post("/books/{book_id}/narration", status_code=202)
    def request_narration(body: NarrationRequest, request: Request, book: OwnedBook, db: Database) -> NarrationManifest:
        """Prepare narration for one section (recorded clips are reused) and return its manifest."""
        check_preparation(request)
        if book.status != "ready":
            raise HTTPException(409, "The book is not ready yet.")
        voice = _voice(body.voice, book.id) if body.voice else narration.section_voice(settings, book, body.section_id)
        try:
            narration.request(db, settings, book, body.section_id, voice, body.from_sentence)
        except narration.UnknownSection:
            raise HTTPException(404, "Section not found.") from None
        db.commit()
        return narration.manifest(db, settings, book, body.section_id, voice)

    @router.get("/books/{book_id}/narration")
    def get_narration(section_id: str, book: OwnedBook, db: Database, voice: str | None = None) -> NarrationManifest:
        voice = _voice(voice, book.id) if voice else narration.section_voice(settings, book, section_id)
        return narration.manifest(db, settings, book, section_id, voice)

    @router.post("/books/{book_id}/sketches", status_code=202)
    def request_sketches(body: SketchRequest, request: Request, book: OwnedBook, db: Database) -> SketchManifest:
        """Plan and draw one section's pictures (drawn pictures are reused) and return its manifest."""
        check_preparation(request)
        if book.status != "ready":
            raise HTTPException(409, "The book is not ready yet.")
        picture_plan(body.style, body.words)
        try:
            sketches.request(db, settings, book, body.section_id, body.from_sentence, body.style, body.words)
        except sketches.UnknownSection:
            raise HTTPException(404, "Section not found.") from None
        except sketches.UnknownPictureOption:
            raise HTTPException(422, "Unknown picture style or interval.") from None
        db.commit()
        return sketches.manifest(db, settings, book, body.section_id, body.style, body.words)

    @router.get("/books/{book_id}/sketches")
    def get_sketches(
        section_id: str, book: OwnedBook, db: Database, style: str | None = None, words: int | None = None
    ) -> SketchManifest:
        picture_plan(style, words)
        return sketches.manifest(db, settings, book, section_id, style, words)

    def ai_status(db: Session) -> str:
        """Whether voice and pictures can be made right now: ok, daily_limit, or unavailable (a key refused)."""
        if not spending.affordable(db, settings, RESUME_MARGIN_USD):
            return "daily_limit"
        rejected = db.scalar(
            select(Job.id)
            .where(
                Job.error_code == "provider_account",
                Job.finished_at.is_not(None),
                Job.finished_at >= utcnow() - UNAVAILABLE_FOR,
            )
            .limit(1)
        )
        return "unavailable" if rejected else "ok"

    def picture_plan(style: str | None, words: int | None) -> sketches.Plan:
        """The plan for a reader's choice of pictures; refused unless offered."""
        try:
            sketches.check_offered(settings, style)
            return sketches.plan_for(settings, style, words)
        except sketches.UnknownPictureOption:
            raise HTTPException(422, "Unknown picture style or interval.") from None

    @router.get("/books/{book_id}/images/{key}")
    def get_image(key: str, book: OwnedBook, db: Database) -> FileResponse:
        ready = re.fullmatch(r"[0-9a-f]{32}", key) and db.scalar(
            select(SceneRecord.id)
            .where(SceneRecord.book_id == book.id, SceneRecord.cache_key == key, SceneRecord.status == "ready")
            .limit(1)
        )
        path = storage.find_asset(storage.book_dir(settings, book.id) / storage.IMAGES, key) if ready else None
        if path is None:
            raise HTTPException(404, "Image not found.")
        return FileResponse(
            path,
            media_type=storage.MEDIA_TYPES[path.suffix],
            headers={"Cache-Control": "private, max-age=86400, immutable"},
        )

    @router.post("/books/{book_id}/cancel")
    def cancel(book: MyBook, db: Database) -> dict:
        """Stop preparing narration and pictures for this book."""
        cancelled = cancel_preparation(db, book.id)
        db.commit()
        return {"cancelled": cancelled}

    @router.get("/books/{book_id}/audio/{key}")
    def get_audio(key: str, book: OwnedBook, db: Database) -> FileResponse:
        ready = re.fullmatch(r"[0-9a-f]{32}", key) and db.scalar(
            select(NarrationClip.id)
            .where(NarrationClip.book_id == book.id, NarrationClip.cache_key == key, NarrationClip.status == "ready")
            .limit(1)
        )
        path = storage.find_asset(storage.book_dir(settings, book.id) / storage.AUDIO, key) if ready else None
        if path is None:
            raise HTTPException(404, "Audio not found.")
        # Named by content, so it never changes: let the browser cache it and fetch ahead.
        return FileResponse(
            path,
            media_type=storage.MEDIA_TYPES[path.suffix],
            headers={"Cache-Control": "private, max-age=86400, immutable"},
        )

    def _voice(voice: str | None, book_id: str) -> str:
        try:
            return narration.resolve_voice(settings, voice, book_id)
        except narration.UnknownVoice:
            raise HTTPException(422, "Unknown voice.") from None

    @router.delete("/books/{book_id}", status_code=204)
    def delete_book(book: MyBook, db: Database) -> Response:
        storage.delete_book_files(settings, book.id)
        db.delete(book)  # its jobs, clips, and scenes go with it
        db.commit()
        return Response(status_code=204)

    if settings.study_dir is not None:
        add_study_routes(router, settings, Database, readable_book)
    app.include_router(router)

    if settings.reader_dist.is_dir():  # serve the built reader from the same origin
        app.mount("/", StaticFiles(directory=settings.reader_dist, html=True), name="reader")
    return app


def add_study_routes(router: APIRouter, settings: Settings, Database, readable_book) -> None:
    """The study build's console, sessions, and interaction log (study.py, docs/study.md)."""

    @router.get("/study")
    def study_overview(db: Database) -> dict:
        """The build, the models, and how far each passage's voice and pictures are prepared."""
        return study.overview(db, settings)

    @router.get("/study/schedule")
    def study_schedule(participant: int) -> list[dict]:
        """The passage and pictures of each of a participant's four sessions."""
        try:
            return study.schedule_for(participant)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @router.post("/study/sessions", status_code=201)
    def start_study_session(body: study.SessionIn, request: Request, db: Database) -> study.StudySession:
        """Start a session; a study or practice session only once its pictures and voice are all prepared."""
        if body.kind == "benchmark":
            readable_book(body.book_id or "", db)  # a book on this reader
        try:
            return study.start_session(db, settings, body)
        except study.NotPrepared as error:
            raise HTTPException(409, str(error)) from None
        except (ValueError, sketches.UnknownPictureOption) as error:
            raise HTTPException(422, str(error)) from None

    @router.get("/study/sessions/{session_id}")
    def get_study_session(session_id: str) -> study.StudySession:
        session = study.load_session(settings, session_id)
        if session is None:
            raise HTTPException(404, "Session not found.")
        return session

    @router.post("/study/sessions/{session_id}/events", status_code=204)
    def log_study_events(session_id: str, body: study.EventBatch) -> Response:
        """Append the reader's events to the participant's log."""
        session = study.load_session(settings, session_id)
        if session is None:
            raise HTTPException(404, "Session not found.")
        try:
            study.log_events(settings, session, body.events)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return Response(status_code=204)

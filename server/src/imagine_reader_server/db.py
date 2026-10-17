"""Book, job, and narration records in SQLite.

Times are stored as naive UTC; ``utcnow`` is the only clock used. The schema
is managed by Alembic migrations (``migrations/``), applied on startup.
"""

import fcntl
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import Settings

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Book(Base):
    """A PDF added to this reader, or one of the samples (``shared``): kept until it is deleted."""

    __tablename__ = "books"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    filename: Mapped[str] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # queued | processing | ready | failed
    error_code: Mapped[str | None] = mapped_column(String(32), default=None)
    error_message: Mapped[str | None] = mapped_column(String(300), default=None)
    page_count: Mapped[int | None] = mapped_column(Integer, default=None)
    word_count: Mapped[int | None] = mapped_column(Integer, default=None)
    narration_characters: Mapped[int] = mapped_column(Integer, default=0)  # synthesized so far, for the budget
    sketch_count: Mapped[int] = mapped_column(Integer, default=0)  # drawn so far, for the budget
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    shared: Mapped[bool] = mapped_column(Boolean, default=False)  # a sample: not the reader's to change or delete
    # The pictures its opening chapter is prepared with, as chosen when it was added.
    picture_style: Mapped[str] = mapped_column(String(16), default="pencil")
    picture_words: Mapped[int] = mapped_column(Integer, default=0)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # ingest | narrate
    state: Mapped[str] = mapped_column(String(16), index=True)  # queued | running | done | failed | cancelled
    params: Mapped[dict | None] = mapped_column(JSON, default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(32), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class NarrationClip(Base):
    """One sentence's narration in one voice. Clips with the same cache key share an audio file."""

    __tablename__ = "narration_clips"
    __table_args__ = (UniqueConstraint("book_id", "sentence_id", "voice"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    section_id: Mapped[str] = mapped_column(String(16))
    sentence_id: Mapped[str] = mapped_column(String(16))
    order: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    voice: Mapped[str] = mapped_column(String(64))
    cache_key: Mapped[str] = mapped_column(String(32), index=True)
    pause_after: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16))  # pending | ready | failed | skipped
    duration: Mapped[float | None] = mapped_column(Float, default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(32), default=None)


class SceneRecord(Base):
    """One planned scene of a section and its sketch, in one plan. Scenes with the same cache key share a picture.

    A plan is a brain, its scene length, and a picture style; a book keeps
    every plan its readers chose, side by side.
    """

    __tablename__ = "scenes"
    __table_args__ = (
        UniqueConstraint("book_id", "plan", "scene_id"),
        Index("ix_scenes_book_section_plan", "book_id", "section_id", "plan"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    plan: Mapped[str] = mapped_column(String(64))
    scene_id: Mapped[str] = mapped_column(String(32))
    section_id: Mapped[str] = mapped_column(String(16))
    first_sentence: Mapped[int] = mapped_column(Integer)
    last_sentence: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict] = mapped_column(JSON)  # the SketchSpec sent to the image provider
    cache_key: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16))  # pending | ready | failed | skipped
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(32), default=None)
    # How its picture was drawn: the drawing style that was accepted (another when the first was declined),
    # when, and how many seconds the image model took over it, declined tries included.
    rendered_style: Mapped[str | None] = mapped_column(String(16), default=None)
    drawn_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    draw_seconds: Mapped[float | None] = mapped_column(Float, default=None)


class Spend(Base):
    """One paid AI request, for the site-wide daily limit. Kept when its book is deleted."""

    __tablename__ = "spending"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # UTC date, YYYY-MM-DD
    kind: Mapped[str] = mapped_column(String(16))  # speech | image | text
    book_id: Mapped[str] = mapped_column(String(32))
    usd: Mapped[float] = mapped_column(Float)
    provider: Mapped[str | None] = mapped_column(String(16), default=None)  # gemini | openai | cloudflare | openrouter
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def upgrade_database(engine: Engine, lock_path: Path) -> None:
    """Apply pending migrations. The API and the worker may start together, so they take turns."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")


def make_engine(settings: Settings) -> Engine:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _configure(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")  # the API and the worker share the file
        cursor.close()

    upgrade_database(engine, settings.data_dir / "migrate.lock")
    return engine

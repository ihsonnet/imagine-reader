"""Books kept fully prepared: the samples, on a public demo that asks for it (``IR_PREPARE_SAMPLES``).

Every chapter's voice, and every chapter's pictures in every style at every
pace, made once and kept. The worker asks about once a minute and queues one
job at a time, only while no other work is waiting; the plans beyond the
opening chapter use at most half of the day's spending limit.
"""

from sqlalchemy import or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import narration, reading, sketches, spending
from .config import Settings
from .db import Book, Job

RESUME_MARGIN_USD = 0.05  # resume only with room for a few pictures, so a spent day does not retry every minute
SHARE = 0.5  # the share of the daily limit these books' later chapters and other plans may use
# Still to make: not yet made, or stopped by a limit. A picture or clip that failed is left for a visitor's request,
# so a lasting failure is not tried again every minute.
UNFINISHED = ("pending", "skipped")


def prepare_next(engine: Engine, settings: Settings, book_id: str, plans: list[tuple[str, int]]) -> None:
    """Queue the next unfinished part of a book kept fully prepared, one job at a time."""
    with Session(engine) as db, db.begin():
        book = db.get(Book, book_id)
        if book is None or book.status != "ready" or not spending.affordable(db, settings, RESUME_MARGIN_USD):
            return
        busy = db.scalar(
            select(Job.id)
            .where(Job.state.in_(("queued", "running")), or_(Job.book_id == book_id, Job.state == "queued"))
            .limit(1)
        )
        if busy is not None:  # this book is being prepared, or a visitor's work is waiting
            return
        view = reading.load(settings, book.id)
        start = view["start_section"]
        if start is None:
            return
        sections = [start] + [section["id"] for section in view["sections"] if section["id"] != start]
        # The opening chapter in the pictures it was added with comes first.
        if _prepare_pictures(db, settings, book, start, book.picture_style, book.picture_words):
            return
        # The voice waits on a day its narrator's (Gemini's) requests are used up, rather than being made in
        # another voice.
        for section in [] if narration.voice_waits_today(settings, book) else sections:
            voice = narration.section_voice(settings, book, section)
            clips = narration.manifest(db, settings, book, section, voice)
            if not clips.preparing and (not clips.segments or any(c.status in UNFINISHED for c in clips.segments)):
                narration.request(db, settings, book, section, voice, background=True)
                return
        if spending.spent_today(db) >= SHARE * settings.daily_spend_limit_usd:
            return  # the rest waits for a day with room to spare
        for style, words in plans:
            for section in sections:
                if _prepare_pictures(db, settings, book, section, style, words):
                    return


def _prepare_pictures(db: Session, settings: Settings, book: Book, section: str, style: str, words: int) -> bool:
    """Queue a section's pictures in this style and pace unless they are all made; whether a job was queued."""
    pictures = sketches.manifest(db, settings, book, section, style, words)
    if pictures.status in ("drawing", "planning"):
        return False
    if pictures.scenes and not any(scene.status in UNFINISHED for scene in pictures.scenes):
        return False
    sketches.request(db, settings, book, section, style=style, words=words)
    db.flush()
    return sketches.manifest(db, settings, book, section, style, words).status in ("drawing", "planning")

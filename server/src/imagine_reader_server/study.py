"""The study build: counterbalanced reading sessions, pictures prepared ahead, and an interaction log.

Each participant reads four passages (A to D), each with its own picture
frequency and style, assigned by three mutually orthogonal Latin squares.
The passages are installed as shared books, and every frequency and style of
each is prepared before the study, so every participant sees the same
pictures at the same sentences. A session's pictures are fixed when it
starts, and what the participant does is appended to a JSON Lines file per
participant. docs/study.md says how to run a study.

From the repository root (or ``docker compose exec api``):

    python -m imagine_reader_server.study status
    python -m imagine_reader_server.study prepare [--run]
    python -m imagine_reader_server.study schedule [--participants 24]
    python -m imagine_reader_server.study export FOLDER
    python -m imagine_reader_server.study redraw PASSAGE STYLE WORDS [SCENE ...] [--failed] [--run]
    python -m imagine_reader_server.study clear-pictures BOOK_ID [--force]
"""

import argparse
import csv
import hashlib
import json
import logging
import re
import secrets
import shutil
import sys
import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import narration, reading, sketches, storage
from .config import Settings
from .db import Book, Job, SceneRecord, make_engine, utcnow
from .jobs import process_next
from .sample import SAMPLES, Sample, add_shared_book

FREQUENCIES = (1, 25, 60, 120)  # a new scene after so many words; the study does not offer Auto
FREQUENCY_NAMES = {1: "every sentence", 25: "about every 10 s", 60: "about every 25 s", 120: "about every 50 s"}
STYLES = sketches.STUDY_STYLES
PASSAGES = ("A", "B", "C", "D")

# Block 1 of the schedule: a row per slot, a column per session. Frequency, style and passage follow three
# mutually orthogonal Latin squares, so each level appears once per session and each pairing once per block.
_FREQUENCY = ((1, 25, 60, 120), (25, 1, 120, 60), (60, 120, 1, 25), (120, 60, 25, 1))
_STYLE = (
    ("pencil", "color-hd", "animated", "color"),
    ("color", "animated", "color-hd", "pencil"),
    ("color-hd", "pencil", "color", "animated"),
    ("animated", "color", "pencil", "color-hd"),
)
_PASSAGE = (("A", "D", "B", "C"), ("B", "C", "A", "D"), ("C", "B", "D", "A"), ("D", "A", "C", "B"))

PASSAGE_IDS = {letter: f"{'0' * 30}a{number}" for number, letter in enumerate(PASSAGES, start=1)}
PRACTICE = SAMPLES[0]  # The Lantern Keeper, read with the default pictures to learn the reader

EVENTS = (
    "session_start",
    "session_end",
    "sentence_start",
    "picture_shown",
    "picture_ready",
    "narration_wait",
    "pause",
    "resume",
    "replay",
    "skip",
    "pictures_hidden",
    "pictures_shown",
    "setting_change",
)
MAX_EVENT_DATA = 2000  # characters of JSON per event

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Condition:
    """What one session shows: a passage, its picture style, and a new picture after so many words."""

    passage: str  # A to D, or "practice"
    style: str
    words: int
    block: int | None = None  # four participants make a block
    slot: int | None = None  # the participant's row of the schedule
    column: int | None = None  # the schedule's column this session takes; even blocks run them backwards


PRACTICE_CONDITION = Condition("practice", "pencil", 25)


def condition(participant: int, session: int) -> Condition:
    """The passage and pictures of participant ``participant``'s session ``session`` (1 to 4)."""
    if participant < 1 or session not in (1, 2, 3, 4):
        raise ValueError("Participants are numbered from 1, and sessions run from 1 to 4.")
    block, slot = (participant - 1) // 4 + 1, (participant - 1) % 4 + 1
    column = session if block % 2 else 5 - session
    row, col = slot - 1, column - 1
    return Condition(_PASSAGE[row][col], _STYLE[row][col], _FREQUENCY[row][col], block, slot, column)


def schedule_for(participant: int) -> list[dict]:
    """The passage and pictures of each of a participant's four sessions."""
    return [{"session": session, **asdict(condition(participant, session))} for session in range(1, 5)]


def passages(settings: Settings) -> dict[str, Sample]:
    """The study's passages, A.pdf to D.pdf in the study folder, as shared books."""
    folder = settings.study_dir or Path("study")
    return {
        letter: Sample(
            PASSAGE_IDS[letter], folder / f"{letter}.pdf", f"Passage {letter}.pdf", f"Passage {letter}", "en"
        )
        for letter in PASSAGES
    }


def book_id_for(passage: str) -> str:
    return PRACTICE.id if passage == "practice" else PASSAGE_IDS[passage]


def _plans_for(passage: str) -> list[tuple[str, int]]:
    if passage == "practice":
        return [(PRACTICE_CONDITION.style, PRACTICE_CONDITION.words)]
    return [(style, words) for style in STYLES for words in FREQUENCIES]


def install(engine: Engine, settings: Settings) -> list[str]:
    """Add the practice story and each passage in the study folder; returns the letters whose PDF is missing.

    A passage whose PDF changed since it was added is removed and added again,
    so a draft passage never stays behind under a final one's letter.
    """
    add_shared_book(engine, settings, PRACTICE)
    missing = []
    for letter, passage in passages(settings).items():
        if not passage.pdf.is_file():
            missing.append(letter)
            continue
        digest = hashlib.sha256(passage.pdf.read_bytes()).hexdigest()
        with Session(engine) as db, db.begin():
            book = db.get(Book, passage.id)
            if book is not None and book.sha256 != digest:
                log.info("passage %s changed; reading it again", letter)
                storage.delete_book_files(settings, book.id)
                db.delete(book)
        add_shared_book(engine, settings, passage)
    return missing


def told_sections(settings: Settings, book_id: str) -> list[str]:
    """The ids of the book's sections that have sentences to narrate."""
    view = reading.load(settings, book_id)
    return [section.id for section in sketches.sections_from_reading(view) if section.sentences]


def readiness(db: Session, settings: Settings, passage: str) -> dict:
    """How far one passage's voice and pictures are prepared, plan by plan."""
    book = db.get(Book, book_id_for(passage))
    if book is None:
        return {"status": "missing", "narration": None, "plans": []}
    if book.status != "ready":
        return {"status": book.status, "narration": None, "plans": []}
    sections = told_sections(settings, book.id)
    voice = narration.resolve_voice(settings, None)
    voiced = [narration.manifest(db, settings, book, section, voice).segments for section in sections]
    segments = [segment for section in voiced for segment in section]
    clips_ready = sum(segment.status == "ready" for segment in segments)
    every_section_voiced = bool(sections) and all(voiced)  # a section nobody asked for yet has no clips
    plans = []
    for style, words in _plans_for(passage):
        manifests = [sketches.manifest(db, settings, book, section, style, words) for section in sections]
        scenes = [scene for manifest in manifests for scene in manifest.scenes]
        refused = sum(scene.error_code == "refused" for scene in scenes)
        ready = sum(scene.status == "ready" for scene in scenes)
        planned = all(manifest.scenes for manifest in manifests)
        plans.append(
            {
                "style": style,
                "words": words,
                "scenes": len(scenes),
                "ready": ready,
                "refused": refused,  # declined in every style: the previous picture stays on screen
                "fallbacks": sum(bool(s.rendered_style) and s.rendered_style != _drawing(style) for s in scenes),
                "complete": planned and ready + refused == len(scenes),
            }
        )
    return {
        "status": "ready",
        "narration": {
            "ready": clips_ready,
            "planned": len(segments),
            "complete": every_section_voiced and clips_ready == len(segments),
        },
        "plans": plans,
    }


def _drawing(style: str) -> str:
    return sketches.STYLES[style][0]


def condition_ready(readiness_of_passage: dict, style: str, words: int) -> bool:
    plan = next((p for p in readiness_of_passage["plans"] if p["style"] == style and p["words"] == words), None)
    voice = readiness_of_passage["narration"]
    return bool(plan and plan["complete"] and voice and voice["complete"])


def prepare(engine: Engine, settings: Settings) -> list[str]:
    """Queue the voice and every frequency and style of each passage that has been read; returns those not yet read.

    Asking again queues only what is unfinished, so it is safe to repeat.
    """
    voice = narration.resolve_voice(settings, None)
    waiting = []
    with Session(engine) as db, db.begin():
        for passage in ("practice", *PASSAGES):
            book = db.get(Book, book_id_for(passage))
            if book is None or book.status != "ready":
                waiting.append(passage)
                continue
            for section in told_sections(settings, book.id):
                narration.request(db, settings, book, section, voice)
                for style, words in _plans_for(passage):
                    sketches.request(db, settings, book, section, style=style, words=words)
    return waiting


def drain(engine: Engine, settings: Settings) -> None:
    """Run queued jobs in this process until none are left."""
    while process_next(engine, settings):
        pass


def redraw(
    engine: Engine,
    settings: Settings,
    passage: str,
    style: str,
    words: int,
    scene_ids: Sequence[str] = (),
    failed: bool = False,
) -> int:
    """Draw some of a plan's pictures again: the scenes named, and with ``failed`` every one not drawn.

    Pictures are named by their request, so a scene with the same request in
    another plan shows the new picture too. Returns how many were queued.
    """
    plan = sketches.plan_for(settings, style, words)
    with Session(engine) as db, db.begin():
        book = db.get(Book, book_id_for(passage))
        if book is None or book.status != "ready":
            raise ValueError(f"Passage {passage} has not been read yet.")
        in_plan = select(SceneRecord).where(SceneRecord.book_id == book.id, SceneRecord.plan == plan.id)
        scenes = list(db.scalars(in_plan))
        unknown = set(scene_ids) - {scene.scene_id for scene in scenes}
        if unknown:
            raise ValueError(f"No such scenes in this plan: {', '.join(sorted(unknown))}")
        chosen = [scene for scene in scenes if scene.scene_id in scene_ids or (failed and scene.status != "ready")]
        images = storage.book_dir(settings, book.id) / storage.IMAGES
        for scene in chosen:
            if (path := storage.find_asset(images, scene.cache_key)) is not None:
                path.unlink()
            scene.status, scene.error_code, scene.attempts = "pending", None, 0
            scene.rendered_style, scene.drawn_at, scene.draw_seconds = None, None, None
        for section in sorted({scene.section_id for scene in chosen}):
            sketches.request(db, settings, book, section, style=style, words=words)
    return len(chosen)


def clear_pictures(engine: Engine, settings: Settings, book_id: str, force: bool = False) -> int:
    """Forget every picture of a book, so the next reading draws them live; for timing the engine.

    The study's own passages are refused unless ``force``: their pictures are
    what every participant must see. Returns how many scenes were removed.
    """
    if book_id in {PRACTICE.id, *PASSAGE_IDS.values()} and not force:
        raise ValueError("That is one of the study's own books; clearing it would change what participants see.")
    with Session(engine) as db, db.begin():
        book = db.get(Book, book_id)
        if book is None:
            raise ValueError("No such book.")
        db.execute(
            update(Job)
            .where(Job.book_id == book_id, Job.kind == "sketch", Job.state.in_(("queued", "running")))
            .values(state="cancelled")
        )
        removed = db.execute(delete(SceneRecord).where(SceneRecord.book_id == book_id)).rowcount
        book.sketch_count = 0
    shutil.rmtree(storage.book_dir(settings, book_id) / storage.IMAGES, ignore_errors=True)
    return removed


def build_info(settings: Settings) -> dict:
    """Which build and models a session used, recorded with every logged event."""
    return {
        "build": settings.build_version,
        "narration": f"{narration.speech_provider(settings).id} voice {settings.speech_voice}",
        "pictures": f"{sketches.image_provider(settings).id} {settings.image_size}",
        "visual_bible": sketches.story_brain(settings).bible_id,  # the language model's, or the text rules
    }


def overview(db: Session, settings: Settings) -> dict:
    """Everything the study console shows: the build, and how far each passage is prepared."""
    return {
        **build_info(settings),
        "styles": list(STYLES),
        "frequencies": list(FREQUENCIES),
        "practice": {"book_id": PRACTICE.id, **asdict(PRACTICE_CONDITION), **readiness(db, settings, "practice")},
        "passages": [
            {"letter": letter, "book_id": PASSAGE_IDS[letter], **readiness(db, settings, letter)} for letter in PASSAGES
        ],
    }


# Sessions and their log.


class NotPrepared(Exception):
    """The session's pictures or voice are not all prepared, so it would draw live."""


class SessionIn(BaseModel):
    kind: Literal["study", "practice", "benchmark"]
    participant: int | None = Field(default=None, ge=1, le=999)  # study and practice
    session: int | None = Field(default=None, ge=1, le=4)  # study
    book_id: str | None = None  # benchmark: the book to read
    style: str | None = None  # benchmark: the pictures to start with
    words: int | None = None
    label: str | None = Field(default=None, max_length=40)  # benchmark: names the run in its log


class StudySession(BaseModel):
    id: str
    kind: Literal["study", "practice", "benchmark"]
    participant: int | None
    session: int | None
    attempt: int  # 2 or more when the same session was started again
    label: str | None
    passage: str  # A to D, "practice", or "benchmark"
    book_id: str
    style: str
    words: int
    block: int | None
    slot: int | None
    column: int | None
    locked: bool  # the pictures cannot be changed while reading
    created_at: datetime
    models: dict


class Event(BaseModel):
    type: Literal[EVENTS]
    narration_ms: int = Field(ge=0)  # time narration has played in this session, pauses and waits left out
    at: datetime  # the browser's clock
    data: dict = Field(default_factory=dict)


class EventBatch(BaseModel):
    events: list[Event] = Field(max_length=500)


def _sessions_dir(settings: Settings) -> Path:
    return settings.data_dir / "study" / "sessions"


def start_session(db: Session, settings: Settings, request: SessionIn) -> StudySession:
    """Fix a session's passage and pictures, from the schedule for study sessions."""
    if request.kind == "benchmark":
        if not request.book_id:
            raise ValueError("A benchmark session needs a book.")
        style, words = request.style or settings.sketch_style, request.words or settings.sketch_words or 25
        sketches.check_offered(settings, style)
        sketches.plan_for(settings, style, words)  # an unknown interval is refused too
        chosen, passage, book_id = Condition("benchmark", style, words), "benchmark", request.book_id
    else:
        if request.participant is None:
            raise ValueError("A study or practice session needs a participant number.")
        if request.kind == "study":
            if request.session is None:
                raise ValueError("A study session needs a session number from 1 to 4.")
            chosen = condition(request.participant, request.session)
        else:
            chosen = PRACTICE_CONDITION
        passage, book_id = chosen.passage, book_id_for(chosen.passage)
        if not condition_ready(readiness(db, settings, passage), chosen.style, chosen.words):
            raise NotPrepared(
                f"Passage {passage} is not prepared with these pictures yet. "
                "Run: python -m imagine_reader_server.study prepare"
            )
    folder = _sessions_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    attempt = 1
    if request.kind != "benchmark":  # a session started again (after a crash, say) is logged as another attempt
        for path in folder.glob("*.json"):
            earlier = json.loads(path.read_text(encoding="utf-8"))
            same = (earlier["kind"], earlier["participant"], earlier["session"])
            attempt += same == (request.kind, request.participant, request.session if request.kind == "study" else None)
    session = StudySession(
        id=secrets.token_hex(12),
        kind=request.kind,
        participant=None if request.kind == "benchmark" else request.participant,
        session=request.session if request.kind == "study" else None,
        attempt=attempt,
        label=request.label if request.kind == "benchmark" else None,
        passage=passage,
        book_id=book_id,
        style=chosen.style,
        words=chosen.words,
        block=chosen.block,
        slot=chosen.slot,
        column=chosen.column,
        locked=request.kind != "benchmark",
        created_at=datetime.now(UTC),
        models=build_info(settings),
    )
    storage.write_json(folder / f"{session.id}.json", session.model_dump(mode="json"))
    return session


def load_session(settings: Settings, session_id: str) -> StudySession | None:
    if not re.fullmatch(r"[0-9a-f]{24}", session_id):
        return None
    path = _sessions_dir(settings) / f"{session_id}.json"
    return StudySession.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


_log_lock = threading.Lock()


def log_file(settings: Settings, session: StudySession) -> Path:
    """One file per participant (P007.jsonl), and one per benchmark run."""
    if session.kind == "benchmark":
        name = "benchmark-" + (re.sub(r"[^A-Za-z0-9_-]+", "-", session.label or "").strip("-") or session.id)
    else:
        name = f"P{session.participant:03d}"
    return settings.data_dir / "study" / "events" / f"{name}.jsonl"


def log_events(settings: Settings, session: StudySession, events: Sequence[Event]) -> None:
    """Append events, each stamped with its session's participant, condition, build, and models."""
    for event in events:
        if len(json.dumps(event.data)) > MAX_EVENT_DATA:
            raise ValueError("An event carries too much data.")
    received = utcnow().replace(tzinfo=UTC).isoformat()
    fixed = {
        "participant": session.participant,
        "session": session.session,
        "attempt": session.attempt,
        "kind": session.kind,
        "label": session.label,
        "session_id": session.id,
        "passage": session.passage,
        "book_id": session.book_id,
        "style": session.style,
        "words": session.words,
        "build": session.models["build"],
        "models": {key: value for key, value in session.models.items() if key != "build"},
    }
    lines = [
        json.dumps(
            {
                **fixed,
                "event": event.type,
                "narration_ms": event.narration_ms,
                "client_at": event.at.isoformat(),
                "received_at": received,
                "data": event.data,
            },
            ensure_ascii=False,
        )
        for event in events
    ]
    path = log_file(settings, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _log_lock, path.open("a", encoding="utf-8") as out:
        out.write("".join(line + "\n" for line in lines))


# Exporting what was prepared, for screening and for the open materials.


def export(engine: Engine, settings: Settings, folder: Path) -> int:
    """Copy every prepared picture, each passage's visual bible, and an index of both into ``folder``.

    Writes study.json (build, models and every scene), pictures.csv (one row
    per scene, for screening), and <passage>/<words>w-<style>/<scene>.<ext>.
    Returns how many pictures were copied.
    """
    folder.mkdir(parents=True, exist_ok=True)
    rows, copied, record = [], 0, {**build_info(settings), "passages": []}
    with Session(engine) as db:
        for passage in ("practice", *PASSAGES):
            book = db.get(Book, book_id_for(passage))
            if book is None or book.status != "ready":
                continue
            directory = storage.book_dir(settings, book.id)
            out = folder / passage
            out.mkdir(exist_ok=True)
            if (directory / storage.STORY).exists():
                shutil.copyfile(directory / storage.STORY, out / "visual-bible.json")
            view = reading.load(settings, book.id)
            text = {sentence["order"]: sentence["text"] for sentence in view["sentences"]}
            entry = {"passage": passage, "book_id": book.id, "sha256": book.sha256, "plans": []}
            for style, words in _plans_for(passage):
                scenes = []
                for section in told_sections(settings, book.id):
                    for scene in sketches.manifest(db, settings, book, section, style, words).scenes:
                        key = scene.image_url.rsplit("/", 1)[1] if scene.image_url else None
                        source = storage.find_asset(directory / storage.IMAGES, key) if key else None
                        file = None
                        if source is not None:
                            target = out / f"{words}w-{style}" / f"{scene.scene_id}{source.suffix}"
                            target.parent.mkdir(exist_ok=True)
                            shutil.copyfile(source, target)
                            file, copied = str(target.relative_to(folder)), copied + 1
                        sentences = " ".join(
                            text.get(order, "") for order in range(scene.first_sentence, scene.last_sentence + 1)
                        )
                        row = {
                            "passage": passage,
                            "style": style,
                            "words": words,
                            "section": section,
                            "scene": scene.scene_id,
                            "first_sentence": scene.first_sentence,
                            "last_sentence": scene.last_sentence,
                            "status": scene.status,
                            "error_code": scene.error_code or "",
                            "rendered_style": scene.rendered_style or "",
                            "file": file or "",
                            "text": sentences,
                            "prompt": scene.prompt,
                        }
                        rows.append(row)
                        scenes.append(row)
                entry["plans"].append({"style": style, "words": words, "scenes": scenes})
            record["passages"].append(entry)
    storage.write_json(folder / "study.json", record)
    with (folder / "pictures.csv").open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]) if rows else ["passage"])
        writer.writeheader()
        writer.writerows(rows)
    return copied


# The command line.


def _status_lines(engine: Engine, settings: Settings) -> list[str]:
    with Session(engine) as db:
        summary = overview(db, settings)
    lines = [
        f"Build {summary['build']}",
        f"Narration: {summary['narration']}",
        f"Pictures: {summary['pictures']}",
        f"Visual bible: {summary['visual_bible']}",
        "",
    ]
    for item in [{"letter": "practice", **summary["practice"]}, *summary["passages"]]:
        name = item["letter"]
        if item["status"] != "ready":
            lines.append(f"{name:9} {item['status']}")
            continue
        voice = item["narration"]
        done = [plan for plan in item["plans"] if plan["complete"]]
        declined = sum(plan["refused"] for plan in item["plans"])
        fallbacks = sum(plan["fallbacks"] for plan in item["plans"])
        lines.append(
            f"{name:9} voice {voice['ready']}/{voice['planned']}  "
            f"pictures {len(done)}/{len(item['plans'])} settings ready"
            + (f"  ({declined} scenes declined in every style)" if declined else "")
            + (f"  ({fallbacks} drawn in watercolour instead)" if fallbacks else "")
        )
        for plan in item["plans"]:
            if not plan["complete"]:
                lines.append(
                    f"          {plan['style']:9} {FREQUENCY_NAMES[plan['words']]:17} "
                    f"{plan['ready']}/{plan['scenes'] or '?'} drawn"
                )
    return lines


def _schedule_lines(participants: int) -> list[str]:
    lines = ["Participant  Block  Slot  " + "  ".join(f"Session {n:<28}" for n in range(1, 5))]
    for participant in range(1, participants + 1):
        cells = []
        for session in range(1, 5):
            chosen = condition(participant, session)
            cells.append(f"{chosen.passage} · {FREQUENCY_NAMES[chosen.words]} · {chosen.style}")
        first = condition(participant, 1)
        row = f"P{participant:03d}         {first.block:<5}  {first.slot:<4}  "
        lines.append(row + "  ".join(f"{cell:36}" for cell in cells))
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m imagine_reader_server.study", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="how far each passage's voice and pictures are prepared")
    prepare_command = commands.add_parser("prepare", help="add the passages and queue every frequency and style")
    prepare_command.add_argument("--run", action="store_true", help="do the work in this process, not the worker")
    schedule_command = commands.add_parser("schedule", help="print each participant's passages and pictures")
    schedule_command.add_argument("--participants", type=int, default=24)
    export_command = commands.add_parser("export", help="copy every picture and an index of them, for screening")
    export_command.add_argument("folder", type=Path)
    redraw_command = commands.add_parser("redraw", help="draw some of a plan's pictures again")
    redraw_command.add_argument("passage", choices=[*PASSAGES, "practice"])
    redraw_command.add_argument("style", choices=STYLES)
    redraw_command.add_argument("words", type=int, choices=FREQUENCIES)
    redraw_command.add_argument("scenes", nargs="*", help="scene ids, as in pictures.csv")
    redraw_command.add_argument("--failed", action="store_true", help="every scene of the plan not yet drawn")
    redraw_command.add_argument("--run", action="store_true", help="do the work in this process, not the worker")
    clear_command = commands.add_parser("clear-pictures", help="forget a book's pictures, to time live drawing")
    clear_command.add_argument("book_id")
    clear_command.add_argument("--force", action="store_true", help="even one of the study's own books")
    args = parser.parse_args(argv)

    if args.command == "schedule":
        print("\n".join(_schedule_lines(args.participants)))
        return 0
    settings = Settings()
    if settings.study_dir is None:
        print("Set IR_STUDY_DIR to the folder holding A.pdf, B.pdf, C.pdf and D.pdf (see docs/study.md).")
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    engine = make_engine(settings)
    try:
        if args.command == "prepare":
            missing = install(engine, settings)
            if missing:
                print(f"Missing from {settings.study_dir}: {', '.join(f'{letter}.pdf' for letter in missing)}")
            if args.run:
                drain(engine, settings)  # read the passages first
            waiting = prepare(engine, settings)
            if args.run:
                drain(engine, settings)
            elif waiting:
                print(f"Still being read: {', '.join(waiting)}. Run prepare again once the worker has read them.")
        elif args.command == "redraw":
            count = redraw(engine, settings, args.passage, args.style, args.words, args.scenes, args.failed)
            print(f"{count} pictures queued to be drawn again.")
            if args.run:
                drain(engine, settings)
        elif args.command == "export":
            count = export(engine, settings, args.folder)
            print(f"Copied {count} pictures to {args.folder}; see pictures.csv and study.json there.")
            return 0
        elif args.command == "clear-pictures":
            count = clear_pictures(engine, settings, args.book_id, args.force)
            print(f"Removed {count} scenes and their pictures; the next reading draws them live.")
            return 0
    except ValueError as error:
        print(error)
        return 1
    print("\n".join(_status_lines(engine, settings)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

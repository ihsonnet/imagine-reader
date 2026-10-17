"""Sketches: scenes planned from the story, one picture each, prepared a section at a time.

Readers choose a picture style and how often a new picture appears (or Auto,
where the story sets the pace); together with the story brain these make a
plan, and a book keeps every plan its readers chose side by side, so switching
back costs nothing. Whole-book analysis (the story bible) runs once per book.
With a language model, each section is then planned as a storyboard, once per
pace and kept, whatever the style. Each section's scene plan is checked before
use, and each picture request is built only from what the reader has heard by
the end of its scene. With a storyboard, each main character is drawn once per
style as a reference picture, which goes with every picture of them so they
look the same throughout. Several pictures are drawn at once, nearest the
reader first. Pictures are cached by provider and prompt, failed ones are
retried on their own, a busy service is waited out, and a per-book picture
budget and the site's daily spending limit stop drawing before they would be
exceeded. Logs carry ids and counts, never book text.
"""

import hashlib
import io
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from PIL import Image
from sqlalchemy import case, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from imagine_reader.providers import (
    REFERENCE_SIZE,
    CloudflareImageProvider,
    ImageProvider,
    MockImageProvider,
    OpenAIImageProvider,
    OpenAITextModel,
    OpenRouterImageProvider,
    OpenRouterTextModel,
    Picture,
    ProviderAccountError,
    ProviderBusy,
    ProviderRefused,
    Reference,
)
from imagine_reader.story import (
    AUTO,
    CastingStoryBrain,
    HeuristicStoryBrain,
    InvalidScenePlan,
    Scene,
    SceneImage,
    SectionText,
    SentenceText,
    SketchManifest,
    SketchSpec,
    StoryBible,
    StoryboardStoryBrain,
    StoryBrain,
    check_scene_plan,
    reference_spec,
    restyle,
    sketch_spec,
)

from . import reading, sample_pack, spending, storage, twins
from .config import Settings
from .db import Book, Job, SceneRecord, utcnow

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")
_UNFINISHED = ("pending", "failed")

# Picture styles readers can choose: (drawing style, image quality). Low quality is $0.006 and about
# 8 seconds a picture on gpt-image-1-mini; medium is $0.015 and about 15 seconds.
STYLES = {
    "pencil": ("pencil", "low"),
    "color": ("color", "low"),
    "color-hd": ("color", "medium"),
    "ghibli": ("ghibli", "low"),
    "animated": ("animated", "low"),  # the same hand-painted look, with no studio named
}
# Drawing styles to try, in turn, when the provider declines a picture. Its moderation turns down many
# animated-film pictures of children that it draws in other styles; a scene is never left blank.
FALLBACKS = {"ghibli": ("animated", "color"), "animated": ("color",)}
# How often a new picture appears, in words: Auto (0: the story sets the pace), every sentence, then about
# every 10, 25, and 50 seconds of a story told slowly (about 140 words a minute).
WORD_CHOICES = (AUTO, 1, 25, 60, 120)
REFERENCE_PIXELS = 512  # reference pictures are kept and sent this size: a quarter of the input cost of 1024


class UnknownSection(Exception):
    pass


class UnknownPictureOption(ValueError):
    pass


# The study build's styles (study.py): the hand-painted look is offered without a studio's name.
STUDY_STYLES = ("pencil", "color", "color-hd", "animated")


def offered_styles(settings: Settings) -> list[str]:
    """The picture styles readers may choose on this site, in the order they are shown."""
    if settings.study_dir is not None:
        return list(STUDY_STYLES)
    return [style.strip() for style in settings.picture_styles.split(",") if style.strip() in STYLES]


def offered_words(settings: Settings) -> list[int]:
    """How often pictures may change on this site, Auto (0) first."""
    if settings.study_dir is not None:
        from .study import FREQUENCIES  # the study's own levels

        return list(FREQUENCIES)
    return sorted({*WORD_CHOICES, *([settings.sketch_words] if settings.sketch_words is not None else [])})


def default_words(settings: Settings) -> int:
    """The pace a reader starts with: the setting, else Auto (4 or 5 pictures a minute), or a picture about every 25
    seconds when OpenRouter draws them, where they cost about ten times Cloudflare's price."""
    offered = offered_words(settings)
    if settings.sketch_words is not None:
        return settings.sketch_words
    if settings.study_dir is not None:
        return 25 if 25 in offered else offered[0]
    return 60 if _image_choice(settings) == "openrouter" else AUTO


def check_offered(settings: Settings, style: str | None) -> None:
    """Refuse a style this site does not offer; books keep any style they were prepared with."""
    if style is not None and style not in offered_styles(settings):
        raise UnknownPictureOption(f"picture style {style!r} is not offered")


@dataclass(frozen=True)
class Plan:
    """How a section's pictures are made: which brain plans the scenes, how often, and in what style."""

    style: str
    words: int
    brain: StoryBrain
    artist: ImageProvider

    @property
    def id(self) -> str:
        return f"{self.brain.id}/{self.style}"

    @property
    def references(self) -> bool:
        """Whether its pictures are drawn with reference pictures of their main characters."""
        return getattr(self.brain, "references", False)

    def matches(self, params: dict, settings: Settings) -> bool:
        style = params.get("style") or settings.sketch_style
        words = params.get("words")
        return style == self.style and int(default_words(settings) if words is None else words) == self.words


def plan_for(settings: Settings, style: str | None = None, words: int | None = None, sample: bool = False) -> Plan:
    """The plan for a reader's choices, or the defaults where they made none. A sample's is the sample pack's,
    with or without a key."""
    style = style or settings.sketch_style
    words = default_words(settings) if words is None else words
    if style not in STYLES:
        raise UnknownPictureOption(f"unknown picture style {style!r}")
    if words not in offered_words(settings):
        raise UnknownPictureOption(f"unsupported picture interval {words!r}")
    return Plan(style, words, story_brain(settings, words, sample), image_provider(settings, STYLES[style][1]))


def story_brain(settings: Settings, words: int | None = None, sample: bool = False) -> StoryBrain:
    """auto: with a key for the language model (OpenRouter's or OpenAI's), storyboards (the study build keeps the
    cast-only brain its paper describes); without one, the text rules, except for the samples, whose storyboards
    come in the sample pack (anything new for them waits for a key)."""
    words = default_words(settings) if words is None else words
    choice = settings.story_brain
    if choice == "auto" and settings.study_dir is not None:
        choice = "cast" if settings.text_key else "heuristic"  # the study's passages are shared, not the samples
    elif choice == "auto":
        choice = "board" if settings.text_key or sample else "heuristic"
    if choice == "heuristic":
        return HeuristicStoryBrain(words)
    if choice in ("cast", "board"):
        reader = _text_model(settings)
        return CastingStoryBrain(reader, words) if choice == "cast" else StoryboardStoryBrain(reader, words)
    raise ValueError(f"unknown story brain {settings.story_brain!r}")


def _text_model(settings: Settings) -> OpenRouterTextModel | OpenAITextModel:
    """The language model, through OpenRouter or with OpenAI's own key (the same model, the same id); without
    either, one that cannot be asked, for the samples' plans that come ready-made."""
    if settings.openrouter_api_key:
        return OpenRouterTextModel(settings.openrouter_api_key.get_secret_value(), settings.story_model)
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    return OpenAITextModel(key, settings.story_model)


def _image_choice(settings: Settings) -> str:
    choice = settings.image_provider
    if choice == "auto":
        if settings.study_dir is not None:
            return "openai" if settings.openai_api_key else "mock"  # as the study's paper describes
        if settings.cloudflare:
            return "cloudflare"
        return "openrouter" if settings.openrouter_api_key else "openai" if settings.openai_api_key else "mock"
    return choice


def image_provider(settings: Settings, quality: str | None = None) -> ImageProvider:
    """Cloudflare's FLUX.2 [klein] 4B with its account, else the same model through OpenRouter, else OpenAI's."""
    choice = _image_choice(settings)
    if choice == "mock":
        return MockImageProvider()
    if choice == "cloudflare":
        if not (settings.cloudflare_account_id and settings.cloudflare_api_token):
            raise ValueError("IR_IMAGE_PROVIDER=cloudflare needs CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN")
        return CloudflareImageProvider(
            settings.cloudflare_account_id,
            settings.cloudflare_api_token.get_secret_value(),
            quality=quality or STYLES[settings.sketch_style][1],
        )
    if choice == "openrouter":
        if not settings.openrouter_api_key:
            raise ValueError("IR_IMAGE_PROVIDER=openrouter needs OPENROUTER_API_KEY")
        return OpenRouterImageProvider(settings.openrouter_api_key.get_secret_value())
    if choice == "openai":
        if not settings.openai_api_key:
            raise ValueError("IR_IMAGE_PROVIDER=openai needs OPENAI_API_KEY")
        return OpenAIImageProvider(
            settings.openai_api_key.get_secret_value(),
            model=settings.image_model,
            quality=quality or STYLES[settings.sketch_style][1],
            size=settings.image_size,
            usd_per_image=settings.image_usd_each,
        )
    raise ValueError(f"unknown image provider {choice!r}")


def sections_from_reading(view: dict) -> list[SectionText]:
    sections = []
    for section in view["sections"]:
        paragraphs = view["paragraphs"][section["first_paragraph"] : section["end_paragraph"]]
        ids = {paragraph["id"] for paragraph in paragraphs}
        sections.append(
            SectionText(
                id=section["id"],
                title=section["title"],
                sentences=[
                    SentenceText(s["id"], s["order"], s["paragraph_id"], s["text"])
                    for s in view["sentences"]
                    if s["paragraph_id"] in ids
                ],
                heading_paragraphs={paragraph["id"] for paragraph in paragraphs if paragraph["heading"]},
            )
        )
    return sections


def request(
    db: Session,
    settings: Settings,
    book: Book,
    section_id: str,
    from_sentence: int | None = None,
    style: str | None = None,
    words: int | None = None,
) -> None:
    """Queue the section's sketches in the chosen plan unless they are already drawn or being drawn.

    ``from_sentence`` is where the reader is: drawing starts with the scene
    there and the ones after it. Asking again while drawing moves that point.
    Nothing is made for a sample while its sample pack is on its way.
    """
    if book.shared and sample_pack.pack_pending(settings):
        return
    plan = plan_for(settings, style, words, book.shared)
    if section_id not in {section["id"] for section in reading.load(settings, book.id)["sections"]}:
        raise UnknownSection(section_id)
    _rekey(db, plan, book.id, section_id)
    db.execute(  # asking again retries pictures that failed or were stopped; the job re-checks every limit
        update(SceneRecord)
        .where(
            *_in_plan(plan, book.id, section_id),
            SceneRecord.status.in_(("failed", "skipped")),
            or_(SceneRecord.error_code.is_(None), SceneRecord.error_code != "refused"),
        )
        .values(status="pending", error_code=None, attempts=0)
    )
    scenes = list(db.scalars(select(SceneRecord).where(*_in_plan(plan, book.id, section_id))))
    active = _active_job(db, settings, book.id, section_id, plan)
    if active is not None:
        if from_sentence is not None:
            active.params = {**active.params, "from": from_sentence}
    elif not scenes or any(scene.status == "pending" for scene in scenes):
        params = {"section_id": section_id, "from": from_sentence or 0, "style": plan.style, "words": plan.words}
        db.add(Job(book_id=book.id, kind="sketch", state="queued", params=params))


def manifest(
    db: Session, settings: Settings, book: Book, section_id: str, style: str | None = None, words: int | None = None
) -> SketchManifest:
    plan = plan_for(settings, style, words, book.shared)
    scenes = list(
        db.scalars(select(SceneRecord).where(*_in_plan(plan, book.id, section_id)).order_by(SceneRecord.first_sentence))
    )
    active = _active_job(db, settings, book.id, section_id, plan) is not None
    last = _latest_job(db, settings, book.id, section_id, plan)
    error_code = last.error_code if not active and last is not None and last.state == "failed" else None
    pending = any(scene.status == "pending" for scene in scenes)
    if active:
        status = "drawing" if scenes else "planning"
    elif error_code and (pending or not scenes):
        status = "failed"
    elif scenes and not pending:
        status = "complete"
    else:
        status = "idle"
    return SketchManifest(
        book_id=book.id,
        section_id=section_id,
        brain=plan.brain.id,
        provider=plan.artist.id,
        style=plan.style,
        words=plan.words,
        status=status,
        error_code=error_code,
        scenes=[
            SceneImage(
                scene_id=scene.scene_id,
                first_sentence=scene.first_sentence,
                last_sentence=scene.last_sentence,
                status=scene.status,
                image_url=f"/api/books/{book.id}/images/{scene.cache_key}" if scene.status == "ready" else None,
                caption=scene.spec["caption"],
                error_code=scene.error_code,
                prompt=scene.spec["prompt"],
                rendered_style=scene.rendered_style,
                drawn_at=scene.drawn_at.replace(tzinfo=UTC) if scene.drawn_at else None,
                draw_seconds=scene.draw_seconds,
            )
            for scene in scenes
        ],
        sketches_used=book.sketch_count,
        sketch_budget=settings.sketch_budget,
    )


def run_job(engine: Engine, settings: Settings, job_id: int, book_id: str, params: dict) -> tuple[str, str | None]:
    """Analyze the book if needed, plan the section's scenes if needed, then draw them."""
    with Session(engine) as db:
        book = db.get(Book, book_id)
        sample = book is not None and book.shared
    try:
        plan = plan_for(settings, params.get("style"), params.get("words"), sample)
    except UnknownPictureOption:
        return "failed", "unknown_picture_option"
    section_id = params["section_id"]
    directory = storage.book_dir(settings, book_id)
    sections = sections_from_reading(reading.load(settings, book_id))
    section = next((s for s in sections if s.id == section_id), None)
    if section is None:
        return "failed", "unknown_section"

    with Session(engine) as db:
        book = db.get(Book, book_id)
        if book is not None:
            twins.share_from_twins(db, settings, book)  # pictures a copy of the same PDF has drawn since
    try:  # this site's AI reads and plans; when it cannot now, the pictures wait for it (never the text rules)
        bible = _story_bible(engine, settings, book_id, plan, sections)
        with Session(engine) as db:
            planned = db.scalar(select(SceneRecord.id).where(*_in_plan(plan, book_id, section_id)).limit(1))
        scenes = _scene_plan(engine, settings, book_id, plan, section, bible) if planned is None else None
    except PlanningWaits as waits:
        return "failed", waits.code
    if planned is None:
        try:
            check_scene_plan(scenes, section, bible)
        except InvalidScenePlan as error:
            log.warning("rejected the scene plan for book %s section %s: %s", book_id, section_id, error)
            return "failed", "invalid_scene_plan"
    with Session(engine) as db, db.begin():
        planned = db.scalar(select(SceneRecord.id).where(*_in_plan(plan, book_id, section_id)).limit(1))
        if planned is None:
            for scene in scenes:
                spec = sketch_spec(scene, bible, plan.artist.id, STYLES[plan.style][0], references=plan.references)
                db.add(
                    SceneRecord(
                        book_id=book_id,
                        plan=plan.id,
                        scene_id=scene.id,
                        section_id=section_id,
                        first_sentence=scene.first_sentence,
                        last_sentence=scene.last_sentence,
                        spec=spec.model_dump(),
                        cache_key=spec.cache_key,
                        status="pending",
                    )
                )

    images = directory / storage.IMAGES
    images.mkdir(exist_ok=True)
    return _Drawing(engine, settings, plan, job_id, book_id, section_id, images, bible).run()


class PlanningWaits(Exception):
    """This site's AI cannot read or plan the book now: the daily limit, its account, or an error. The pictures
    wait for it, and asking again tries again; the text rules never stand in."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _scene_plan(
    engine: Engine, settings: Settings, book_id: str, plan: Plan, section: SectionText, bible: StoryBible
) -> list[Scene]:
    """The section's scenes. A storyboard is paid for once per pace and kept, so every style shares it. Raises
    PlanningWaits when it cannot be had now."""
    brain = plan.brain
    if not isinstance(brain, StoryboardStoryBrain):
        return brain.plan_scenes(section, bible)
    path = storage.book_dir(settings, book_id) / storage.SCENES / f"{bible.brain}--{brain.id}" / f"{section.id}.json"
    with _lock_for(book_id, str(path)):
        return _storyboard(engine, settings, book_id, brain, section, bible, path)


def _storyboard(engine, settings, book_id, brain: StoryboardStoryBrain, section, bible, path: Path):
    if path.exists():
        return [Scene.model_validate(scene) for scene in json.loads(path.read_text(encoding="utf-8"))]
    cost = brain.estimate_planning_cost(section, bible)
    with Session(engine) as db:
        if not spending.reserve(db, settings, cost):
            log.info("storyboard for book %s section %s waits for the daily limit", book_id, section.id)
            raise PlanningWaits("daily_limit")
    with spending.reserved(cost):
        try:
            scenes = brain.plan_scenes(section, bible)
        except ProviderAccountError:
            log.error("text model rejected the account; check its key in .env and that its account has credit")
            raise PlanningWaits("provider_account") from None
        except Exception:
            log.exception("storyboard failed for book %s section %s", book_id, section.id)
            raise PlanningWaits("plan_failed") from None
        with Session(engine) as db, db.begin():
            spending.record(db, "text", book_id, brain.last_cost, brain)
    path.parent.mkdir(parents=True, exist_ok=True)
    storage.write_json(path, [scene.model_dump(mode="json") for scene in scenes])
    log.info("storyboard for book %s section %s: %d pictures", book_id, section.id, len(scenes))
    return scenes


class _Drawing:
    """Draws a section's unfinished scenes in one plan, in several lanes at once, nearest the reader first.

    Lanes take scenes one at a time under a lock, so no scene is drawn twice
    and every limit is checked with the pictures already on their way counted.
    """

    def __init__(self, engine, settings, plan: Plan, job_id, book_id, section_id, images: Path, bible: StoryBible):
        self.engine, self.settings, self.plan, self.artist = engine, settings, plan, plan.artist
        self.job_id, self.book_id, self.section_id, self.images = job_id, book_id, section_id, images
        self.bible = bible
        self.lock = threading.Lock()
        self.in_flight: set[int] = set()
        self.outcome: tuple[str, str | None] | None = None  # set when the whole job has to stop

    def run(self) -> tuple[str, str | None]:
        lanes = max(1, self.settings.image_lanes)
        with ThreadPoolExecutor(lanes, thread_name_prefix=f"sketch-{self.job_id}") as pool:
            for lane in [pool.submit(self._lane) for _ in range(lanes)]:
                lane.result()
        return self.outcome or ("done", None)

    def _lane(self) -> None:
        while (claimed := self._claim()) is not None:
            scene_id, spec, cost = claimed
            try:
                with spending.reserved(cost):
                    self._draw(scene_id, spec, cost)
            finally:
                with self.lock:
                    self.in_flight.discard(scene_id)

    def _stop(self, outcome: tuple[str, str | None]) -> None:
        if self.outcome is None:
            self.outcome = outcome

    def _claim(self) -> tuple[int, SketchSpec, float] | None:
        """The next scene to draw, held for this lane; None when nothing is left or the job must stop."""
        with self.lock, Session(self.engine) as db, db.begin():
            if self.outcome is not None:
                return None
            job, book = db.get(Job, self.job_id), db.get(Book, self.book_id)
            if job is None or book is None or job.state == "cancelled":
                self._stop(("cancelled", None))
                return None
            _rekey(db, self.plan, self.book_id, self.section_id)
            start = int((job.params or {}).get("from") or 0)
            while True:
                scene = db.scalars(
                    select(SceneRecord)
                    .where(
                        *_in_plan(self.plan, self.book_id, self.section_id),
                        SceneRecord.status.in_(_UNFINISHED),
                        SceneRecord.attempts < self.settings.max_clip_attempts,
                        SceneRecord.id.not_in(list(self.in_flight)),
                    )
                    .order_by(case((SceneRecord.last_sentence < start, 1), else_=0), SceneRecord.first_sentence)
                    .limit(1)
                ).first()
                if scene is None:
                    return None
                if storage.find_asset(self.images, scene.cache_key) is not None:  # already drawn for this book
                    scene.status, scene.error_code = "ready", None
                    db.flush()
                    continue

                over = book.sketch_count + len(self.in_flight) + 1 > self.settings.sketch_budget
                if over and not book.shared:  # the samples are short and the site's own: every pace and style
                    self._stop(self._skip_unfinished(db, "budget_exceeded", everywhere=True))
                    return None
                spec = SketchSpec.model_validate(scene.spec)
                cost = self.artist.estimate_cost(spec)
                if not spending.reserve(db, self.settings, cost):
                    self._stop(self._skip_unfinished(db, "daily_limit"))
                    return None
                scene.attempts += 1
                self.in_flight.add(scene.id)
                return scene.id, spec, cost

    def _skip_unfinished(self, db: Session, code: str, everywhere: bool = False) -> tuple[str, str]:
        where = [
            SceneRecord.book_id == self.book_id,
            SceneRecord.status.in_(_UNFINISHED),
            SceneRecord.id.not_in(list(self.in_flight)),  # those finish on their own
        ]
        if not everywhere:
            where += [SceneRecord.section_id == self.section_id, SceneRecord.plan == self.plan.id]
        db.execute(update(SceneRecord).where(*where).values(status="skipped", error_code=code))
        log.info("sketches for book %s stopped: %s", self.book_id, code)
        return "failed", code

    def _draw(self, scene_id: int, spec: SketchSpec, cost: float) -> None:
        drawing = STYLES[self.plan.style][0]
        tries = [(drawing, spec, self._references(spec, drawing))] + [
            (fallback, restyled, [])  # another style's look; the references would pull it back
            for fallback in FALLBACKS.get(self.plan.style, ())
            if (restyled := restyle(spec, drawing, fallback, self.artist.id)) is not None
        ]
        started = time.monotonic()
        try:
            rendered_style, picture = self._render_first_accepted(scene_id, tries)
        except ProviderBusy as busy:
            log.info("image provider busy; book %s waits %.0f s", self.book_id, busy.retry_after)
            self._update(scene_id, attempts=-1)  # not this scene's fault; it is tried again
            time.sleep(busy.retry_after)
            return
        except ProviderRefused:
            self._update(scene_id, status="failed", error_code="refused", give_up=True)
            return
        except ProviderAccountError:
            log.error("image provider rejected the account; check its key in .env and that its account has credit")
            self._update(scene_id, attempts=-1)
            with self.lock:
                self._stop(("failed", "provider_account"))
            return
        except Exception:
            log.exception("image provider failed for scene %s of book %s", scene_id, self.book_id)
            self._update(scene_id, status="failed", error_code="provider_error")
            return
        seconds = time.monotonic() - started
        storage.write_bytes(self.images / f"{spec.cache_key}{storage.EXTENSIONS[picture.media_type]}", picture.data)
        with Session(self.engine) as db, db.begin():
            spending.record(db, "image", self.book_id, cost, self.artist)
            db.execute(update(Book).where(Book.id == self.book_id).values(sketch_count=Book.sketch_count + 1))
            scene = db.get(SceneRecord, scene_id)
            if scene is not None and scene.cache_key == spec.cache_key:  # not re-keyed meanwhile
                scene.status, scene.error_code = "ready", None
                scene.rendered_style, scene.drawn_at, scene.draw_seconds = rendered_style, utcnow(), round(seconds, 2)

    def _render_first_accepted(self, scene_id: int, tries: list[tuple[str, SketchSpec, list[Reference]]]):
        """Ask for each version of the picture in turn until one is drawn: (its drawing style, the picture).

        Declined requests are not paid for.
        """
        for number, (style, attempt, references) in enumerate(tries):
            try:
                return style, (self.artist.render(attempt, references) if references else self.artist.render(attempt))
            except ProviderRefused:
                if number == len(tries) - 1:
                    raise
                log.info("scene %s of book %s declined; trying another style", scene_id, self.book_id)
        raise AssertionError("no versions to draw")

    def _references(self, spec: SketchSpec, drawing: str) -> list[Reference]:
        """Reference pictures of the spec's characters in this drawing style, drawn the first time they are needed.

        A reference that cannot be had (declined, the daily limit, an error) is
        left out; the picture is still drawn from its description.
        """
        people = {character.id: character for character in self.bible.characters}
        references = []
        for character_id in spec.references:
            character = people.get(character_id)
            if character is None:
                continue
            reference = reference_spec(character, self.bible, self.artist.id, drawing)
            picture = self._reference(reference)
            if picture is not None:
                references.append(Reference(character.name, picture))
        return references

    def _reference(self, spec: SketchSpec) -> Picture | None:
        folder = storage.book_dir(self.settings, self.book_id) / storage.REFERENCES
        path, declined = folder / f"{spec.cache_key}.webp", folder / f"{spec.cache_key}.declined"
        with _lock_for(self.book_id, spec.cache_key):  # lanes needing the same person wait for one drawing
            if path.exists():
                return Picture(path.read_bytes(), "image/webp", REFERENCE_PIXELS, REFERENCE_PIXELS)
            if declined.exists():
                return None
            cost = self.artist.reference_cost()
            with Session(self.engine) as db:
                if not spending.reserve(db, self.settings, cost):
                    return None
            with spending.reserved(cost):
                try:
                    drawn = self.artist.render(spec, size=REFERENCE_SIZE)
                except ProviderRefused:
                    folder.mkdir(exist_ok=True)
                    declined.touch()
                    return None
                except Exception:
                    log.exception("reference picture failed for book %s", self.book_id)
                    return None
                with Session(self.engine) as db, db.begin():
                    spending.record(db, "image", self.book_id, cost, self.artist)
            buffer = io.BytesIO()
            small = Image.open(io.BytesIO(drawn.data)).convert("RGB")
            small.resize((REFERENCE_PIXELS, REFERENCE_PIXELS), Image.LANCZOS).save(buffer, "WEBP", quality=85)
            folder.mkdir(exist_ok=True)
            storage.write_bytes(path, buffer.getvalue())
            log.info("drew a reference picture for book %s", self.book_id)
            return Picture(buffer.getvalue(), "image/webp", REFERENCE_PIXELS, REFERENCE_PIXELS)

    def _update(self, scene_id: int, status=None, error_code=None, attempts: int = 0, give_up: bool = False) -> None:
        with Session(self.engine) as db, db.begin():
            scene = db.get(SceneRecord, scene_id)
            if scene is None:
                return
            if status is not None:
                scene.status, scene.error_code = status, error_code
            scene.attempts = self.settings.max_clip_attempts if give_up else max(0, scene.attempts + attempts)


def _key(provider_id: str, prompt: str) -> str:
    return hashlib.sha256(f"{provider_id}\0{prompt}".encode()).hexdigest()[:32]


def _in_plan(plan: Plan, book_id: str, section_id: str) -> list:
    return [SceneRecord.book_id == book_id, SceneRecord.section_id == section_id, SceneRecord.plan == plan.id]


def _rekey(db: Session, plan: Plan, book_id: str, section_id: str) -> None:
    """Point a plan's scenes at the current image provider, so adding a key redraws its placeholders. A picture an
    image model has already drawn is kept (the samples' come ready-made from one provider, and are kept with any)."""
    for scene in db.scalars(select(SceneRecord).where(*_in_plan(plan, book_id, section_id))):
        key = _key(plan.artist.id, scene.spec["prompt"])
        placeholder = scene.cache_key == _key(MockImageProvider().id, scene.spec["prompt"])
        if key != scene.cache_key and (scene.status != "ready" or placeholder):
            scene.cache_key = key
            scene.spec = {**scene.spec, "cache_key": key}
            scene.status, scene.error_code, scene.attempts = "pending", None, 0
            scene.rendered_style, scene.drawn_at, scene.draw_seconds = None, None, None


_bible_lock = threading.Lock()  # jobs for two plans of one book may start together
_locks: dict[tuple[str, ...], threading.Lock] = {}
_locks_lock = threading.Lock()


def _lock_for(*key: str) -> threading.Lock:
    """One lock per key, so two jobs or lanes never pay for the same storyboard or reference picture."""
    with _locks_lock:
        return _locks.setdefault(key, threading.Lock())


def _story_bible(
    engine: Engine, settings: Settings, book_id: str, plan: Plan, sections: list[SectionText]
) -> StoryBible:
    """Whole-book analysis, run once per book and kept beside its other files.

    A language model's reading is paid for and limited like pictures. If it
    cannot be had now (the daily limit, an error), PlanningWaits is raised:
    the pictures wait for it, and it is tried again the next time.
    """
    brain = plan.brain
    path = storage.book_dir(settings, book_id) / storage.STORY
    with _bible_lock:
        if path.exists():
            bible = StoryBible.model_validate_json(path.read_text(encoding="utf-8"))
            if bible.brain == brain.bible_id:
                return bible
        if isinstance(brain, CastingStoryBrain | StoryboardStoryBrain):
            bible = _cast(engine, settings, book_id, brain, sections)
        else:
            bible = brain.build_bible(sections)
        storage.write_json(path, bible.model_dump(mode="json"))
        return bible


def _cast(engine, settings, book_id: str, brain: CastingStoryBrain | StoryboardStoryBrain, sections) -> StoryBible:
    cost = brain.estimate_cost(sections)
    with Session(engine) as db:
        if not spending.reserve(db, settings, cost):
            log.info("story analysis for book %s waits for the daily limit", book_id)
            raise PlanningWaits("daily_limit")
    with spending.reserved(cost):
        try:
            bible = brain.build_bible(sections)
        except ProviderAccountError:
            log.error("text model rejected the account; check its key in .env and that its account has credit")
            raise PlanningWaits("provider_account") from None
        except Exception:
            log.exception("story analysis failed for book %s", book_id)
            raise PlanningWaits("plan_failed") from None
        with Session(engine) as db, db.begin():
            spending.record(db, "text", book_id, brain.last_cost, brain)
    log.info(
        "story analysis for book %s: %d characters, %d objects, %d places",
        book_id,
        len(bible.characters),
        len(bible.things),
        len(bible.locations),
    )
    return bible


def _active_job(db: Session, settings: Settings, book_id: str, section_id: str, plan: Plan) -> Job | None:
    jobs = db.scalars(select(Job).where(Job.book_id == book_id, Job.kind == "sketch", Job.state.in_(ACTIVE)))
    return next((job for job in jobs if _for(job, settings, section_id, plan)), None)


def _latest_job(db: Session, settings: Settings, book_id: str, section_id: str, plan: Plan) -> Job | None:
    jobs = db.scalars(select(Job).where(Job.book_id == book_id, Job.kind == "sketch").order_by(Job.id.desc()))
    return next((job for job in jobs if _for(job, settings, section_id, plan)), None)


def _for(job: Job, settings: Settings, section_id: str, plan: Plan) -> bool:
    params = job.params or {}
    return params.get("section_id") == section_id and plan.matches(params, settings)

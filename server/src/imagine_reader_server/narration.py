"""Narration: one clip per sentence, prepared a section at a time when the reader asks.

Preparing on request keeps cost tied to what is actually read. Clips are
cached by provider, style, voice, and text, so replaying never pays twice; a
failed clip is retried on its own, a refused one is not retried at all, and a
busy service is waited out. Several clips are recorded at once, starting at
the reader's sentence. A per-book character budget and the site's daily
spending limit stop preparation before they would be exceeded. Logs carry ids
and counts, never book text.
"""

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import case, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from imagine_reader.narration import (
    PAUSES,
    NarrationItem,
    NarrationManifest,
    NarrationSegment,
    SentenceText,
    clip_key,
    plan_sentences,
    read_in_runs,
)
from imagine_reader.providers import (
    ClipChecker,
    GeminiSentenceTimer,
    GeminiSpeechProvider,
    MockSpeechProvider,
    OpenAISpeechProvider,
    OpenAITranscriber,
    OpenRouterSentenceTimer,
    OpenRouterSpeechProvider,
    OpenRouterTranscriber,
    ProviderAccountError,
    ProviderBusy,
    ProviderQuotaExhausted,
    ProviderRefused,
    SpeechProvider,
    measure_duration,
)

from . import reading, sample_pack, spending, storage, twins
from .config import Settings
from .db import Book, Job, NarrationClip

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")
_UNFINISHED = ("pending", "failed")


class UnknownSection(Exception):
    pass


class UnknownVoice(Exception):
    pass


# The language as Gemini's style names it ("a warm native Bengali storyteller"); any other book's style names none.
GEMINI_LANGUAGES = {"bn": "Bengali", "en": "English"}
QUOTA = "voice-quota.json"  # in the data folder: Gemini models whose requests are used up, and until when


def speech_provider(settings: Settings, language: str = "", voice: str | None = None) -> SpeechProvider:
    """The narrator for books in this language ("bn", "en", or "" for the rest): Gemini's storyteller, through
    OpenRouter or with a Gemini key, even on a day its requests are used up (the reading waits for them; it never
    goes on in another voice). OpenAI's narrator when chosen, or for one of its own voices; placeholders without a
    key. With ``voice``, the narrator that owns that voice."""
    choice = settings.speech_provider
    if choice == "auto":
        gemini = settings.openrouter_api_key or settings.gemini_api_key
        choice = "gemini" if gemini else "openai" if settings.openai_api_key else "mock"
    if voice in OpenAISpeechProvider.voices and voice not in GeminiSpeechProvider.voices:
        choice = "openai"  # the narrator that owns this voice
    elif voice in GeminiSpeechProvider.voices:
        choice = "gemini"
    if choice == "gemini":
        return _gemini(settings, language)
    if choice == "openai":
        if not settings.openai_api_key:
            raise ValueError("OpenAI's narrator needs OPENAI_API_KEY")
        return OpenAISpeechProvider(
            settings.openai_api_key.get_secret_value(),
            model=settings.openai_speech_model,
            default_voice=settings.openai_speech_voice,
            usd_per_million_characters=settings.speech_usd_per_million_characters,
        )
    if choice == "mock":
        return MockSpeechProvider()
    raise ValueError(f"unknown speech provider {choice!r}")


def _gemini(settings: Settings, language: str) -> GeminiSpeechProvider:
    """Gemini's storyteller for this language: Flash-Lite for Bangla, Flash for the rest; each run's clips heard."""
    model = settings.bangla_speech_model if language == "bn" else settings.english_speech_model
    name = GEMINI_LANGUAGES.get(language, "")
    checker = _checker(settings, language)
    if settings.openrouter_api_key:
        key = settings.openrouter_api_key.get_secret_value()
        timer = OpenRouterSentenceTimer(key, settings.timing_model)
        return OpenRouterSpeechProvider(key, model, settings.speech_voice, name, timer=timer, checker=checker)
    # Without a key, one that cannot be asked: the samples' voice comes ready-made, and anything new waits for a key.
    key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else ""
    timer = GeminiSentenceTimer(key, settings.timing_model)
    return GeminiSpeechProvider(key, model, settings.speech_voice, name, timer=timer, checker=checker)


def _checker(settings: Settings, language: str) -> ClipChecker | None:
    """Hears each clip of a run, so a misplaced cut is read again: through OpenRouter, or OpenAI's own key."""
    if settings.openrouter_api_key:
        transcriber = OpenRouterTranscriber(settings.openrouter_api_key.get_secret_value(), settings.transcribe_model)
    elif settings.openai_api_key:
        transcriber = OpenAITranscriber(settings.openai_api_key.get_secret_value(), settings.transcribe_model)
    else:
        return None
    return ClipChecker(transcriber, language or None)


def quota_used_up(settings: Settings, model: str) -> bool:
    """Whether this Gemini model's requests for today are used up (shared by the API and the worker)."""
    try:
        until = json.loads((settings.data_dir / QUOTA).read_text(encoding="utf-8")).get(model)
    except (OSError, ValueError):
        return False
    return until is not None and datetime.fromisoformat(until) > datetime.now(UTC)


def mark_quota_used_up(settings: Settings, model: str, seconds: float) -> None:
    path = settings.data_dir / QUOTA
    try:
        used = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        used = {}
    used[model] = (datetime.now(UTC) + timedelta(seconds=seconds + 60)).isoformat()
    storage.write_json(path, used)


def book_speech_provider(settings: Settings, book_id: str, voice: str | None = None) -> SpeechProvider:
    """The narrator for this book, by its language; with ``voice``, the one that owns that voice."""
    return speech_provider(settings, reading.load(settings, book_id)["language"], voice)


def section_voice(settings: Settings, book: Book, section_id: str) -> str:
    """The voice a section is read in: the narrator's own (Gemini's storyteller, Charon). The samples' is the sample
    pack's, with or without a key."""
    if book.shared:
        return settings.speech_voice
    return resolve_voice(settings, None, book.id)


def voice_waits_today(settings: Settings, book: Book) -> bool:
    """Whether this book's narrator (Gemini, with its own key) has used up today's requests: it waits for tomorrow."""
    provider = book_speech_provider(settings, book.id)
    return isinstance(provider, GeminiSpeechProvider) and quota_used_up(settings, provider.model)


def resolve_voice(settings: Settings, voice: str | None, book_id: str | None = None) -> str:
    """The voice to read with: the one asked for, or the narrator's own; for a book, its language's narrator."""
    provider = book_speech_provider(settings, book_id) if book_id else speech_provider(settings)
    chosen = voice or provider.default_voice
    if chosen not in provider.voices:
        raise UnknownVoice(chosen)
    return chosen


def request(
    db: Session,
    settings: Settings,
    book: Book,
    section_id: str,
    voice: str,
    from_sentence: int | None = None,
    background: bool = False,
) -> None:
    """Create the section's clips if new, and queue a job if any still need audio.

    ``from_sentence`` is where the reader is: preparation starts there. Asking
    again while preparing moves that point. Nothing is made for a sample while
    its sample pack is on its way.
    """
    if book.shared and sample_pack.pack_pending(settings):
        return
    provider = book_speech_provider(settings, book.id, voice)  # the voice's own narrator, so its clips keep their keys
    view = reading.load(settings, book.id)
    style = view["narration_style"]
    existing = {
        clip.sentence_id: clip
        for clip in db.scalars(
            select(NarrationClip).where(NarrationClip.book_id == book.id, NarrationClip.voice == voice)
        )
    }
    for item in (item for run in _runs(view, section_id, style, provider) for item in run):
        key = clip_key(f"{provider.id}/{style}", voice, item.text)
        clip = existing.get(item.sentence_id)
        if clip is None:
            db.add(
                NarrationClip(
                    book_id=book.id,
                    section_id=section_id,
                    sentence_id=item.sentence_id,
                    order=item.order,
                    text=item.text,
                    voice=voice,
                    cache_key=key,
                    pause_after=item.pause_after,
                    status="pending",
                )
            )
        elif clip.cache_key != key:  # the narrator, its reading style, or the sentence changed: record it again
            clip.cache_key, clip.text, clip.order, clip.pause_after = key, item.text, item.order, item.pause_after
            clip.status, clip.error_code, clip.attempts, clip.duration = "pending", None, 0, None
    db.execute(  # asking again retries clips that failed or were stopped; the job re-checks every limit
        update(NarrationClip)
        .where(
            NarrationClip.book_id == book.id,
            NarrationClip.section_id == section_id,
            NarrationClip.voice == voice,
            NarrationClip.status.in_(("failed", "skipped")),
            or_(NarrationClip.error_code.is_(None), NarrationClip.error_code != "refused"),
        )
        .values(status="pending", error_code=None, attempts=0)
    )
    db.flush()

    waiting = db.scalars(
        select(NarrationClip.id).where(
            NarrationClip.book_id == book.id,
            NarrationClip.section_id == section_id,
            NarrationClip.voice == voice,
            NarrationClip.status.in_(_UNFINISHED),
            NarrationClip.attempts < settings.max_clip_attempts,
        )
    ).first()
    active = _active_job(db, book.id, section_id, voice)
    if active is not None:
        if from_sentence is not None:
            active.params = {**active.params, "from": from_sentence}
    elif waiting is not None:
        params = {"section_id": section_id, "voice": voice, "from": from_sentence or 0}
        if background:
            params["background"] = True  # prepared ahead (the samples on a demo), not asked for by a reader
        db.add(Job(book_id=book.id, kind="narrate", state="queued", params=params))


def manifest(db: Session, settings: Settings, book: Book, section_id: str, voice: str) -> NarrationManifest:
    clips = list(
        db.scalars(
            select(NarrationClip)
            .where(
                NarrationClip.book_id == book.id,
                NarrationClip.section_id == section_id,
                NarrationClip.voice == voice,
            )
            .order_by(NarrationClip.order)
        )
    )
    preparing = _active_job(db, book.id, section_id, voice) is not None
    last = _latest_job(db, book.id, section_id, voice)
    return NarrationManifest(
        book_id=book.id,
        section_id=section_id,
        provider=book_speech_provider(settings, book.id, voice).id,
        voice=voice,
        segments=[
            NarrationSegment(
                sentence_id=clip.sentence_id,
                order=clip.order,
                status=clip.status,
                duration=clip.duration,
                pause_after=clip.pause_after,
                audio_url=f"/api/books/{book.id}/audio/{clip.cache_key}" if clip.status == "ready" else None,
                error_code=clip.error_code,
            )
            for clip in clips
        ],
        characters_used=book.narration_characters,
        character_budget=settings.narration_character_budget,
        complete=bool(clips) and not any(clip.status == "pending" for clip in clips),
        preparing=preparing,
        error_code=last.error_code if not preparing and last is not None and last.state == "failed" else None,
    )


def run_job(engine: Engine, settings: Settings, job_id: int, book_id: str, params: dict) -> tuple[str, str | None]:
    """Prepare a section's clips, from the reader's sentence on. Returns the job's final state and error code."""
    with Session(engine) as db:
        book = db.get(Book, book_id)
        if book is not None:
            twins.share_from_twins(db, settings, book)  # voice a copy of the same PDF has recorded since
    audio_dir = storage.book_dir(settings, book_id) / storage.AUDIO
    audio_dir.mkdir(parents=True, exist_ok=True)
    style = reading.load(settings, book_id)["narration_style"]
    provider = book_speech_provider(settings, book_id, params.get("voice"))
    view = reading.load(settings, book_id)
    runs = _runs(view, params["section_id"], style, provider)
    sentence_pause = PAUSES[style][0]
    paragraph_ends = {
        item.sentence_id for item in _plan(view, params["section_id"], style) if item.pause_after > sentence_pause
    }
    if isinstance(provider, GeminiSpeechProvider) and quota_used_up(settings, provider.model):
        outcome = ("failed", "voice_quota")  # known to be used up today: nothing is sent
    else:
        outcome = _Recording(
            engine, settings, provider, style, job_id, book_id, params, audio_dir, runs, paragraph_ends
        ).run()
    if outcome == ("failed", "voice_quota"):
        # The reading waits for Gemini's requests to reset, never going on in another voice: what is ready plays,
        # and asking again once they reset records the rest.
        with Session(engine) as db, db.begin():
            db.execute(
                update(NarrationClip)
                .where(
                    NarrationClip.book_id == book_id,
                    NarrationClip.section_id == params["section_id"],
                    NarrationClip.voice == params["voice"],
                    NarrationClip.status.in_(_UNFINISHED),
                )
                .values(status="skipped", error_code="voice_quota")
            )
    return outcome


class _Recording:
    """Records a section's unfinished clips in several lanes at once, from the reader's sentence on.

    Lanes take clips one at a time under a lock, so no clip is recorded twice
    and every limit is checked with the clips already on their way counted.
    """

    def __init__(
        self, engine, settings, provider, style, job_id, book_id, params: dict, audio_dir: Path, runs, paragraph_ends
    ):
        self.engine, self.settings, self.provider, self.style = engine, settings, provider, style
        self.paragraph_ends = paragraph_ends  # sentences after which a run's text has a paragraph break
        self.run_of = {item.sentence_id: number for number, run in enumerate(runs) for item in run}
        self.job_id, self.book_id, self.audio_dir = job_id, book_id, audio_dir
        self.section_id, self.voice = params["section_id"], params["voice"]
        self.lock = threading.Lock()
        self.in_flight: dict[int, int] = {}  # clip id -> characters on their way
        self.outcome: tuple[str, str | None] | None = None  # set when the whole job has to stop

    def run(self) -> tuple[str, str | None]:
        lanes = max(1, self.settings.speech_lanes)
        with ThreadPoolExecutor(lanes, thread_name_prefix=f"narrate-{self.job_id}") as pool:
            for lane in [pool.submit(self._lane) for _ in range(lanes)]:
                lane.result()
        return self.outcome or ("done", None)

    def _lane(self) -> None:
        while (claimed := self._claim()) is not None:
            clips, cost = claimed
            try:
                with spending.reserved(cost):
                    self._record(clips, cost)
            finally:
                with self.lock:
                    for clip_id, *_ in clips:
                        self.in_flight.pop(clip_id, None)

    def _stop(self, outcome: tuple[str, str | None]) -> None:
        if self.outcome is None:
            self.outcome = outcome

    def _claim(self) -> tuple[list[tuple[int, str, str, str]], float] | None:
        """The next clip to record, with the rest of its run for a narrator that reads runs, held for this lane:
        ([(clip id, text, cache key, sentence id)], cost). None when nothing is left or the job must stop."""
        with self.lock, Session(self.engine) as db, db.begin():
            if self.outcome is not None:
                return None
            job, book = db.get(Job, self.job_id), db.get(Book, self.book_id)
            if job is None or book is None or job.state == "cancelled":
                self._stop(("cancelled", None))
                return None
            start = int((job.params or {}).get("from") or 0)
            while True:
                clip = db.scalars(
                    select(NarrationClip)
                    .where(
                        NarrationClip.book_id == self.book_id,
                        NarrationClip.section_id == self.section_id,
                        NarrationClip.voice == self.voice,
                        NarrationClip.status.in_(_UNFINISHED),
                        NarrationClip.attempts < self.settings.max_clip_attempts,
                        NarrationClip.id.not_in(list(self.in_flight)),
                    )
                    .order_by(case((NarrationClip.order < start, 1), else_=0), NarrationClip.order)
                    .limit(1)
                ).first()
                if clip is None:
                    return None
                cached = storage.find_asset(self.audio_dir, clip.cache_key)
                if cached is not None:  # the same text was already narrated this way
                    clip.status, clip.error_code = "ready", None
                    clip.duration = measure_duration(cached.read_bytes(), storage.MEDIA_TYPES[cached.suffix])
                    db.flush()
                    continue

                clips = [clip, *self._rest_of_run(db, clip)]
                characters = sum(len(c.text) for c in clips)
                on_the_way = sum(self.in_flight.values())
                if book.narration_characters + on_the_way + characters > self.settings.narration_character_budget:
                    self._stop(self._skip_unfinished(db, "budget_exceeded", everywhere=True))
                    return None
                cost = self.provider.estimate_cost(" ".join(c.text for c in clips))
                if not spending.reserve(db, self.settings, cost):
                    self._stop(self._skip_unfinished(db, "daily_limit"))
                    return None
                for c in clips:
                    c.attempts += 1
                    self.in_flight[c.id] = len(c.text)
                return [(c.id, c.text, c.cache_key, c.sentence_id) for c in clips], cost

    def _rest_of_run(self, db: Session, first: NarrationClip) -> list[NarrationClip]:
        """The unfinished clips after this one in its run, in order, up to the first that is not."""
        run = self.run_of.get(first.sentence_id)
        if run is None or not hasattr(self.provider, "synthesize_many"):
            return []
        followers = db.scalars(
            select(NarrationClip)
            .where(
                NarrationClip.book_id == self.book_id,
                NarrationClip.section_id == self.section_id,
                NarrationClip.voice == self.voice,
                NarrationClip.order > first.order,
            )
            .order_by(NarrationClip.order)
        )
        rest = []
        for clip in followers:
            if (
                self.run_of.get(clip.sentence_id) != run
                or clip.status not in _UNFINISHED
                or clip.attempts >= self.settings.max_clip_attempts
                or clip.id in self.in_flight
                or storage.find_asset(self.audio_dir, clip.cache_key) is not None
            ):
                break
            rest.append(clip)
        return rest

    def _skip_unfinished(self, db: Session, code: str, everywhere: bool = False) -> tuple[str, str]:
        where = [
            NarrationClip.book_id == self.book_id,
            NarrationClip.status.in_(_UNFINISHED),
            NarrationClip.id.not_in(list(self.in_flight)),  # those finish on their own
        ]
        if not everywhere:
            where += [NarrationClip.section_id == self.section_id, NarrationClip.voice == self.voice]
        db.execute(update(NarrationClip).where(*where).values(status="skipped", error_code=code))
        log.info("narration for book %s stopped: %s", self.book_id, code)
        return "failed", code

    def _record(self, clips: list[tuple[int, str, str, str]], cost: float) -> None:
        texts = [text for _, text, _, _ in clips]
        try:
            if len(clips) > 1:
                read = [  # a blank line where a paragraph ends inside the run, so the narrator pauses there
                    f"{text}\n\n" if sentence_id in self.paragraph_ends and number < len(clips) - 1 else text
                    for number, (_, text, _, sentence_id) in enumerate(clips)
                ]
                speeches = self.provider.synthesize_many(read, self.voice, self.style)
            else:
                speeches = [self.provider.synthesize(texts[0], self.voice, self.style)]
        except ProviderBusy as busy:
            log.info("speech provider busy; book %s waits %.0f s", self.book_id, busy.retry_after)
            for clip_id, *_ in clips:
                self._update(clip_id, attempts=-1)  # not these clips' fault; they are tried again
            time.sleep(busy.retry_after)
            return
        except ProviderRefused:
            for clip_id, *_ in clips:
                self._update(clip_id, status="failed", error_code="refused", give_up=True)
            return
        except ProviderQuotaExhausted as used_up:
            log.warning("%s has no requests left today; reset in %.0f s", self.provider.id, used_up.retry_after)
            mark_quota_used_up(self.settings, self.provider.model, used_up.retry_after)
            for clip_id, *_ in clips:
                self._update(clip_id, attempts=-1)
            with self.lock:
                self._stop(("failed", "voice_quota"))
            return
        except ProviderAccountError:
            log.error(
                "speech provider %s refused the account or its quota is used up; check its key and billing",
                self.provider.id,
            )
            for clip_id, *_ in clips:
                self._update(clip_id, attempts=-1)
            with self.lock:
                self._stop(("failed", "provider_account"))
            return
        except Exception:
            log.exception("speech provider failed for %d clips of book %s", len(clips), self.book_id)
            for clip_id, *_ in clips:
                self._update(clip_id, status="failed", error_code="provider_error")
            return
        for (clip_id, _, key, _), speech in zip(clips, speeches, strict=True):
            if speech is None:  # a sentence the narrator declined (as garbled text): left out, never asked again
                self._update(clip_id, status="failed", error_code="refused", give_up=True)
                continue
            storage.write_bytes(self.audio_dir / f"{key}{storage.EXTENSIONS[speech.media_type]}", speech.audio)
        cost += self.provider.extra_cost() if hasattr(self.provider, "extra_cost") else 0.0  # parts read again
        with Session(self.engine) as db, db.begin():
            spending.record(db, "speech", self.book_id, cost, self.provider)
            db.execute(
                update(Book)
                .where(Book.id == self.book_id)
                .values(
                    narration_characters=Book.narration_characters
                    + sum(len(text) for text, speech in zip(texts, speeches, strict=True) if speech is not None)
                )
            )
            for (clip_id, _, key, _), speech in zip(clips, speeches, strict=True):
                clip = db.get(NarrationClip, clip_id)
                if speech is not None and clip is not None and clip.cache_key == key:  # not re-keyed meanwhile
                    clip.status, clip.error_code = "ready", None
                    clip.duration = measure_duration(speech.audio, speech.media_type)

    def _update(self, clip_id: int, status=None, error_code=None, attempts: int = 0, give_up: bool = False) -> None:
        with Session(self.engine) as db, db.begin():
            clip = db.get(NarrationClip, clip_id)
            if clip is None:
                return
            if status is not None:
                clip.status, clip.error_code = status, error_code
            clip.attempts = self.settings.max_clip_attempts if give_up else max(0, clip.attempts + attempts)


def _active_job(db: Session, book_id: str, section_id: str, voice: str) -> Job | None:
    jobs = db.scalars(select(Job).where(Job.book_id == book_id, Job.kind == "narrate", Job.state.in_(ACTIVE)))
    return next((job for job in jobs if _for(job, section_id, voice)), None)


def _latest_job(db: Session, book_id: str, section_id: str, voice: str) -> Job | None:
    jobs = db.scalars(select(Job).where(Job.book_id == book_id, Job.kind == "narrate").order_by(Job.id.desc()))
    return next((job for job in jobs if _for(job, section_id, voice)), None)


def _for(job: Job, section_id: str, voice: str) -> bool:
    params = job.params or {}
    return params.get("section_id") == section_id and params.get("voice") == voice


def _runs(view: dict, section_id: str, style: str, provider: SpeechProvider) -> list[list[NarrationItem]]:
    """The section's sentences as runs read together, for a narrator that reads several at once; else one
    sentence a run, with the planned pauses."""
    items = _plan(view, section_id, style)
    batch = getattr(provider, "batch", None)
    if not batch:
        return [[item] for item in items]
    return read_in_runs(items, style, *batch, across_paragraphs=getattr(provider, "batch_across_paragraphs", False))


def _plan(view: dict, section_id: str, style: str):
    section = next((s for s in view["sections"] if s["id"] == section_id), None)
    if section is None:
        raise UnknownSection(section_id)
    paragraphs = view["paragraphs"][section["first_paragraph"] : section["end_paragraph"]]
    in_section = {paragraph["id"] for paragraph in paragraphs}
    return plan_sentences(
        [
            SentenceText(s["id"], s["order"], s["paragraph_id"], s["text"])
            for s in view["sentences"]
            if s["paragraph_id"] in in_section
        ],
        {paragraph["id"] for paragraph in paragraphs if paragraph["heading"]},
        style,
    )

"""Narration planning and the playback manifest.

Narration is one clip per sentence of the original text, so every clip has a
known source range. Players join clips with pauses that follow the book's
structure: short between sentences, longer after a paragraph or a heading.

A book is read in one of two styles. Stories (dialogue, "said", "asked") are
told slowly, like a storyteller reading aloud, with longer pauses; other books
are read by a calm, clear narrator.
"""

import hashlib
import re
from typing import Literal

from pydantic import BaseModel

from .ingestion.models import ExtractedDocument, Section
from .story.models import SentenceText

MANIFEST_VERSION = "narration-0.1"
NarrationStyle = Literal["story", "plain"]
# Seconds of silence after a sentence, a paragraph, and a heading, by style.
PAUSES: dict[str, tuple[float, float, float]] = {"story": (0.4, 0.9, 1.3), "plain": (0.25, 0.6, 0.9)}
SENTENCE_PAUSE, PARAGRAPH_PAUSE, HEADING_PAUSE = PAUSES["plain"]

_SPEECH_VERB = re.compile(
    r"\b(said|asked|replied|cried|whispered|shouted|exclaimed|answered|thought)\b"
    r"|বলিল|বলিলেন|কহিল|কহিলেন|বলল|বললেন|বললো|জিজ্ঞাসা|জিজ্ঞেস|ডাকিল|উত্তর দিল|চেঁচাইয়া",  # Bangla, old and new
    re.IGNORECASE,
)
_QUOTED = re.compile(r"[“”\"]|(^|\s)‘|’(\s|$)")


class NarrationItem(BaseModel):
    sentence_id: str
    order: int
    text: str
    pause_after: float


class NarrationSegment(BaseModel):
    sentence_id: str
    order: int
    status: Literal["pending", "ready", "failed", "skipped"]
    duration: float | None
    pause_after: float
    audio_url: str | None
    error_code: str | None = None


class NarrationManifest(BaseModel):
    schema_version: str = MANIFEST_VERSION
    book_id: str
    section_id: str
    provider: str
    voice: str
    segments: list[NarrationSegment]
    characters_used: int  # across the whole book, against the budget
    character_budget: int
    complete: bool  # no segment is still pending
    preparing: bool = False  # a job is working on this section now
    error_code: str | None = None  # why preparation last stopped early, if it did
    # How to make a missing clip with the reader's own key, in their browser: model, voice, instructions, speed.
    request: dict | None = None


def narration_style(texts: list[str]) -> NarrationStyle:
    """ "story" when the book tells a story with people speaking in it, otherwise "plain"."""
    if not texts:
        return "plain"
    quoted = sum(1 for text in texts if _QUOTED.search(text)) / len(texts)
    words = sum(len(text.split()) for text in texts) or 1
    speech_verbs = sum(len(_SPEECH_VERB.findall(text)) for text in texts) * 1000 / words
    return "story" if quoted >= 0.05 or speech_verbs >= 1.5 else "plain"


def plan_sentences(
    sentences: list[SentenceText], heading_paragraphs: set[str], style: NarrationStyle = "plain"
) -> list[NarrationItem]:
    """Ordered sentences with the pause that follows each: longer at paragraph ends, longest after headings."""
    sentence_pause, paragraph_pause, heading_pause = PAUSES[style]
    items = []
    for index, sentence in enumerate(sentences):
        following = sentences[index + 1] if index + 1 < len(sentences) else None
        if following is not None and following.paragraph_id == sentence.paragraph_id:
            pause = sentence_pause
        else:
            pause = heading_pause if sentence.paragraph_id in heading_paragraphs else paragraph_pause
        items.append(
            NarrationItem(sentence_id=sentence.id, order=sentence.order, text=sentence.text, pause_after=pause)
        )
    return items


def read_in_runs(
    items: list[NarrationItem],
    style: NarrationStyle,
    most_sentences: int,
    most_characters: int,
    across_paragraphs: bool = False,
) -> list[list[NarrationItem]]:
    """Group sentences into runs for a narrator that reads several at once: consecutive sentences, up to a
    limit, of one paragraph or (``across_paragraphs``) of several, never past a heading. Within a run nothing
    is added between sentences, since the narrator's own pauses are in its recording; the pause after a run's
    last sentence is kept."""
    sentence_pause, paragraph_pause, _ = PAUSES[style]
    ends_run = (sentence_pause,) if not across_paragraphs else (sentence_pause, paragraph_pause)
    runs: list[list[NarrationItem]] = [[]]
    characters = 0
    for item in items:
        if runs[-1] and (len(runs[-1]) >= most_sentences or characters + len(item.text) > most_characters):
            runs.append([])
            characters = 0
        runs[-1].append(item)
        characters += len(item.text)
        if item.pause_after not in ends_run:  # the paragraph (or, across paragraphs, the heading) ends here
            runs.append([])
            characters = 0
    runs = [run for run in runs if run]
    return [[item.model_copy(update={"pause_after": 0.0}) for item in run[:-1]] + run[-1:] for run in runs]


def plan_section(document: ExtractedDocument, section: Section) -> list[NarrationItem]:
    """The sentences of one section, ready for narration."""
    paragraphs = document.paragraphs[section.first_paragraph : section.end_paragraph]
    in_section = {paragraph.id for paragraph in paragraphs}
    return plan_sentences(
        [
            SentenceText(s.id, s.order, s.paragraph_id, s.narration_text)
            for s in document.sentences
            if s.paragraph_id in in_section
        ],
        {paragraph.id for paragraph in paragraphs if paragraph.heading},
    )


def clip_key(provider_id: str, voice: str, text: str) -> str:
    """Cache key for a clip: the same text, voice, and provider settings are never synthesized twice."""
    return hashlib.sha256(f"{provider_id}\0{voice}\0{text}".encode()).hexdigest()[:32]

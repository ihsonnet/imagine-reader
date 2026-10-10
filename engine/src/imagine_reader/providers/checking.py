"""Checking where a narrator's run of sentences was cut: each clip should say its own sentence, all of it, and
nothing of the next.

A run is read in one request and cut into a clip per sentence at the pauses (see ``gemini``). A cut in the
wrong pause moves part of a sentence into the next clip, and the highlight and a reader starting from that
sentence go with it. So each clip is heard by a speech recogniser, the transcripts are lined up with the text
letter by letter, and every break the transcripts put more than a few letters from the text's own is reported.
The recogniser's own slips (a vowel sign, a word spelt another way) move a break by a letter or two at most,
since the letters around it still match.
"""

import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from typing import Protocol

from .openai import ProviderAccountError, ProviderBusy
from .speech import Speech

TOLERANCE = 3  # letters a break may move in the transcripts before it is reported (a Bangla word is about five)
BUSY_TRIES = 4
ACCOUNT_PAUSE = 3600.0  # seconds the check is taken as unavailable after its account refused (no credit, a bad key)
_unavailable_until = 0.0  # for every checker in this process: a run is not recorded while its check cannot be had
_BANGLA = re.compile(r"[ঀ-৿]")


class Transcriber(Protocol):
    usd_per_minute: float

    def transcribe(self, audio: bytes, media_type: str, language: str | None) -> str: ...


def letters(text: str) -> str:
    """The text's letters and digits only, in one form, for lining a transcript up with the book."""
    text = unicodedata.normalize("NFC", text).lower()
    return "".join(ch for ch in text if ch.isalnum() or "ঀ" <= ch <= "৿")


def misplaced_breaks(texts: list[str], heard: list[str]) -> list[int]:
    """The breaks (``k``: between sentences ``k`` and ``k + 1``) that the clips' transcripts put elsewhere than
    the text does. A transcript in another script (a recogniser's guess at a very short clip) counts as right."""
    if len(texts) < 2:
        return []
    said = [letters(text) for text in texts]
    got = []
    for text, transcript in zip(texts, heard, strict=True):
        clean = letters(transcript)
        same_script = bool(_BANGLA.search(text)) == bool(_BANGLA.search(transcript))
        got.append(clean if clean and same_script else letters(text))
    said_all, got_all = "".join(said), "".join(got)
    blocks = SequenceMatcher(None, got_all, said_all, autojunk=False).get_matching_blocks()

    def in_text(position: int) -> float:
        """Where a position in the transcripts falls in the text: in a matching stretch exactly, else between."""
        before = (0, 0)
        for block in blocks:
            if block.a <= position < block.a + block.size:
                return block.b + position - block.a
            if block.a > position:
                (h0, t0), (h1, t1) = before, (block.a, block.b)
                return t0 + (t1 - t0) * (position - h0) / max(1, h1 - h0)
            before = (block.a + block.size, block.b + block.size)
        return before[1]

    misplaced, said_at, got_at = [], 0, 0
    for k in range(len(texts) - 1):
        said_at += len(said[k])
        got_at += len(got[k])
        if abs(in_text(got_at) - said_at) > TOLERANCE:
            misplaced.append(k)
    return misplaced


class ClipChecker:
    """Hears each clip of a run and reports the breaks in the wrong place."""

    def __init__(self, transcriber: Transcriber, language: str | None = None, lanes: int = 4):
        self.transcriber, self.language, self.lanes = transcriber, language, lanes
        self.usd_per_minute = transcriber.usd_per_minute

    def misplaced(self, texts: list[str], clips: list[Speech]) -> list[int]:
        """The run's misplaced breaks. A clip the recogniser is too busy for is tried again a few times; any other
        failure is raised, so the run waits rather than being kept unchecked."""
        with ThreadPoolExecutor(self.lanes) as pool:
            heard = list(pool.map(self._hear, clips))
        return misplaced_breaks(texts, heard)

    def ready(self) -> bool:
        """Whether runs can be checked now: not within an hour of the recogniser's account refusing."""
        return time.monotonic() >= _unavailable_until

    def _hear(self, clip: Speech) -> str:
        global _unavailable_until
        for attempt in range(BUSY_TRIES):
            try:
                return self.transcriber.transcribe(clip.audio, clip.media_type, self.language)
            except ProviderAccountError:
                _unavailable_until = time.monotonic() + ACCOUNT_PAUSE
                raise
            except ProviderBusy as busy:
                if attempt == BUSY_TRIES - 1:
                    raise
                time.sleep(min(busy.retry_after, 30.0))
        raise AssertionError("unreachable")

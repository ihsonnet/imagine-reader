"""Google Gemini narration, for languages OpenAI's voice reads poorly, such as Bangla.

In a listening test on Devdas, Gemini 3.8 Flash-Lite TTS read Bangla more
clearly than gpt-4o-mini-tts (its transcript differed from the book in 15% of
letters, against 22%), and listeners preferred its male voice. It reads a run
of sentences from one paragraph in a single request, in one steady style for
the whole book, so the reading flows as it did in that test; Gemini also
allows only 10 requests a minute on its first paid tier. It answers with
24 kHz WAV, which is cut into one MP3 clip per sentence for the highlight.

Where one sentence ends in the recording is not marked, and a storyteller
pauses at dashes and commas too, so the pause lengths alone cannot tell. A
Gemini text model listens to the recording and says when each sentence starts
(when a timer is given; otherwise the sentences' lengths and punctuation do),
and each cut is made in the best pause near there. On a Devdas test this placed
7 of 7 breaks exactly; Whisper's word timings dropped 20 s of Bangla speech. Each sentence is sent on a line of
its own, so Gemini pauses at every sentence end (at least 0.36 s, against 0.18 s with spaces, on কিছুক্ষণ), and
a cut is only ever made in a pause, never in a word. Even so, a run of short, repeated sentences (কিছুক্ষণ's
"পানি।", "কি কুৎসিত!") was cut a sentence early or late in about one clip in seven; so with a checker, each clip
is heard and every stretch around a misplaced break is read again on its own, down to single sentences, which
need no cut. A run with too few pauses for its sentences is read again in halves.

Errors are sorted like OpenAI's: a refusal, an account problem, a used-up
daily quota (no waiting fixes it today; it says when it resets), a busy
service, and the rest.
"""

import base64
import io
import json
import logging
import re
import threading
import wave
from array import array
from datetime import date
from typing import Protocol

import httpx
import lameenc

from .checking import ClipChecker
from .openai import ProviderAccountError, ProviderBusy, ProviderRefused
from .speech import Speech

log = logging.getLogger(__name__)


class ProviderQuotaExhausted(ProviderAccountError):
    """The model's requests for today are used up; ``retry_after`` seconds until they reset."""

    def __init__(self, message: str, retry_after: float):
        super().__init__(message)
        self.retry_after = retry_after


class SentenceTimer(Protocol):
    usd_per_minute: float

    def sentence_starts(self, wav: bytes, texts: list[str]) -> list[float]: ...  # seconds, for sentences 2 on


class GeminiSentenceTimer:
    """When each sentence starts in a reading of several, as a Gemini model hears it (to about half a second)."""

    usd_per_minute = 0.003  # audio in at about 25 tokens a second, $0.75 a million on gemini-3.8-flash in 2026

    def __init__(self, api_key: str, model: str = "gemini-3.8-flash", client: httpx.Client | None = None):
        self._api_key, self._client, self.model = api_key, client, model

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=API, headers={"x-goog-api-key": self._api_key})
        return self._client

    def sentence_starts(self, wav: bytes, texts: list[str]) -> list[float]:
        answer = self._ask(base64.b64encode(wav).decode(), timing_prompt(texts), TIMING_SCHEMA)
        starts = {item["sentence"]: float(item["seconds"]) for item in answer.get("starts", [])}
        return [starts[number] for number in range(2, len(texts) + 1)]  # a missing one raises KeyError

    def _ask(self, wav_base64: str, prompt: str, schema: dict) -> dict:
        """Gemini's answer, in JSON following the schema, about the recording."""
        audio = {"inline_data": {"mime_type": "audio/wav", "data": wav_base64}}
        response = self.client.post(
            f"/models/{self.model}:generateContent",
            json={
                "contents": [{"parts": [audio, {"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema},
            },
            timeout=120,
        )
        _check(response)
        return json.loads(response.json()["candidates"][0]["content"]["parts"][0]["text"])


def timing_prompt(texts: list[str]) -> str:
    numbered = "\n".join(f"{number}. {text.strip()}" for number, text in enumerate(texts, 1))
    return (
        "The audio is a narrator reading these sentences in order. For each sentence from 2 on, give the time "
        "in seconds, to a tenth, when its first word begins.\n\n" + numbered
    )


_START = {
    "type": "object",
    "properties": {"sentence": {"type": "integer"}, "seconds": {"type": "number"}},
    "required": ["sentence", "seconds"],
}
TIMING_SCHEMA = {"type": "object", "properties": {"starts": {"type": "array", "items": _START}}, "required": ["starts"]}
# The same, closed, for OpenAI-style strict structured outputs (Google's own schema form has no additionalProperties).
STRICT_TIMING_SCHEMA = {
    **TIMING_SCHEMA,
    "additionalProperties": False,
    "properties": {"starts": {"type": "array", "items": {**_START, "additionalProperties": False}}},
}


API = "https://generativelanguage.googleapis.com/v1beta"
# US dollars per million tokens, (text in, audio out): Google's list prices, which double on 1 January 2027.
PRICES = {"gemini-3.8-flash-lite-tts": (0.50, 6.00), "gemini-3.8-flash-tts": (0.50, 9.00)}
UNKNOWN_PRICE = (1.00, 20.00)
PRICES_DOUBLE_ON = date(2027, 1, 1)
AUDIO_TOKENS_PER_SECOND = 25
CHARACTERS_PER_SECOND = 12  # Bangla read slowly, rounded down so estimates err high
STYLES = {  # one style for the whole book; the transcript itself carries no directions
    "story": "a warm native {language} storyteller, unhurried",
    "plain": "a clear native {language} narrator, at an even pace",
}
MP3_KBPS = 64
# Silence Gemini leaves at either end of a run is cut, keeping a little, so the pause after it is the reader's.
QUIET = 600  # of 32767: about -35 dB
KEEP_BEFORE, KEEP_AFTER = 0.05, 0.15  # seconds
# Sentences and characters read in one request, across paragraphs: about a minute of speech. A whole chapter
# reads well too, but the timer's sentence starts are exact only for about a minute: on live Devdas clips, runs
# of two minutes put 5 of 12 sentence breaks a sentence early or late.
BATCH = (20, 750)
WINDOW, SHORTEST_PAUSE = 0.02, 0.15  # seconds: how finely silence is measured, and the shortest pause to cut in
# Characters a pause is worth when guessing where a sentence ends (about 13 characters a second): a dash,
# a comma or colon, a full stop inside a sentence, and the pause between two sentences.
_PAUSE_WEIGHTS = (("—–…", 10), (",;:", 4), (".!?।", 6))
_BETWEEN_SENTENCES = 8
_DISTANCE_COST = 0.5  # seconds of pause given up for each second a cut is from where it is expected


class GeminiSpeechProvider:
    voices = (
        "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe",
        "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia",
        "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi", "Vindemiatrix",
        "Sadachbia", "Sadaltager", "Sulafat",
    )  # fmt: skip

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.8-flash-lite-tts",
        default_voice: str = "Charon",
        language: str = "Bengali",
        timer: SentenceTimer | None = None,  # finds where each sentence of a run starts
        checker: ClipChecker | None = None,  # hears each clip, so a misplaced cut is read again
        client: httpx.Client | None = None,
    ):
        self._api_key, self._client = api_key, client
        self.model, self.default_voice, self.language = model, default_voice, language
        self.timer, self.checker = timer, checker
        self._extra = threading.local()  # what reading parts of a run again cost, per recording lane
        self.batch = BATCH
        self.batch_across_paragraphs = True  # a paragraph break in a run is a blank line in its text
        self.id = f"gemini-{model}-3"  # bump the suffix when the styles or the way runs are read change

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=API, headers={"x-goog-api-key": self._api_key})
        return self._client

    def estimate_cost(self, text: str, today: date | None = None) -> float:
        text_in, audio_out = PRICES.get(self.model, UNKNOWN_PRICE)
        if (today or date.today()) >= PRICES_DOUBLE_ON:
            text_in, audio_out = 2 * text_in, 2 * audio_out
        seconds = len(text) / CHARACTERS_PER_SECOND
        timing = seconds / 60 * self.timer.usd_per_minute if self.timer else 0.0
        checking = seconds / 60 * self.checker.usd_per_minute if self.checker else 0.0
        return (len(text) / 2 * text_in + seconds * AUDIO_TOKENS_PER_SECOND * audio_out) / 1_000_000 + timing + checking

    def extra_cost(self) -> float:
        """What reading parts of the last run again cost, in this thread, beyond its estimate."""
        return getattr(self._extra, "usd", 0.0)

    def synthesize(self, text: str, voice: str, style: str = "plain") -> Speech:
        speech = self.synthesize_many([text], voice, style)[0]
        if speech is None:
            raise ProviderRefused("declined")
        return speech

    def synthesize_many(self, texts: list[str], voice: str, style: str = "plain") -> list[Speech | None]:
        """Read consecutive sentences in one request and cut the recording into a clip for each; any stretch
        whose cuts are misplaced is read again on its own. A run Gemini declines is read in parts, so only the
        sentences it declines are left out (None), such as text garbled in the book itself."""
        if voice not in self.voices:
            raise ValueError(f"unknown voice {voice!r}")
        if len(texts) > 1 and self.checker is not None and not self.checker.ready():
            raise ProviderAccountError("the clip check is unavailable; the run waits for it")  # no request spent
        self._extra.usd = 0.0
        speeches = self._read_checked(texts, voice, style)
        if all(speech is None for speech in speeches):
            raise ProviderRefused("declined")
        return speeches

    def _read_checked(self, texts: list[str], voice: str, style: str) -> list[Speech | None]:
        try:
            clips: list[Speech] | None = self._read(texts, voice, style)
        except ProviderRefused:
            if len(texts) == 1:
                return [None]  # declined: this sentence is left out
            middle = len(texts) // 2
            for part in (texts[:middle], texts[middle:]):
                self._extra.usd += self.estimate_cost(" ".join(part))
            return self._read_checked(texts[:middle], voice, style) + self._read_checked(texts[middle:], voice, style)
        except NoPlaceToCut:
            clips = None
        if len(texts) == 1:
            return clips  # type: ignore[return-value]  # one sentence is never cut
        if clips is None:
            stretches = [(0, len(texts) // 2 - 1), (len(texts) // 2, len(texts) - 1)]  # too few pauses: in halves
        else:
            spoken = [text.strip() for text in texts]
            misplaced = self.checker.misplaced(spoken, clips) if self.checker else []
            if not misplaced:
                return clips
            stretches = _stretches(misplaced, len(texts))
            log.info("a run of %d sentences was cut wrongly at %s; reading those again", len(texts), misplaced)
        if stretches == [(0, len(texts) - 1)]:  # every break is in doubt: in halves, so each read is shorter
            stretches = [(0, len(texts) // 2 - 1), (len(texts) // 2, len(texts) - 1)]
        clips = clips or [None] * len(texts)  # type: ignore[list-item]
        for first, last in stretches:
            self._extra.usd += self.estimate_cost(" ".join(texts[first : last + 1]))
            clips[first : last + 1] = self._read_checked(texts[first : last + 1], voice, style)
        return clips  # type: ignore[return-value]

    def _read(self, texts: list[str], voice: str, style: str) -> list[Speech]:
        """One request for the run, a sentence a line, cut into a clip for each sentence."""
        direction = " ".join(STYLES[style].format(language=self.language).split())
        return self._clips(self._speak("\n".join(texts).strip(), voice, direction), texts)

    def _speak(self, text: str, voice: str, direction: str) -> bytes:
        """The recording of this text in this voice and style, as WAV."""
        part = {"type": "text", "text": text, "annotations": [{"type": "speech_metadata", "style": direction}]}
        response = self.client.post(
            "/interactions",
            json={
                "model": self.model,
                "input": [{"type": "user_input", "content": [part]}],
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"voice": voice}]},
            },
            timeout=180,
        )
        _check(response)
        audio = [
            content["data"]
            for step in response.json().get("steps", [])
            if step.get("type") == "model_output"
            for content in step.get("content", [])
            if content.get("type") == "audio" and content.get("data")
        ]
        if not audio:  # answered without speech: declined, as when a safety filter stops it
            raise ProviderRefused("no audio in the answer")
        return base64.b64decode(audio[-1])

    def _clips(self, data: bytes, texts: list[str]) -> list[Speech]:
        """Gemini's WAV for a run of sentences, as one MP3 clip per sentence."""
        with wave.open(io.BytesIO(data)) as wav:
            rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
            frames = wav.readframes(wav.getnframes())
        if width != 2:
            raise RuntimeError(f"unexpected sample width {width}")
        samples = array("h", _trim(frames, rate, channels))
        starts = None
        if len(texts) > 1 and self.timer is not None:
            try:
                starts = self.timer.sentence_starts(_wav(samples, rate, channels), texts)
            except Exception:  # the lengths of the sentences will do
                log.warning("sentence timings failed; placing sentence breaks by length", exc_info=True)
        cuts = [0, *sentence_cuts(samples, rate, channels, texts, starts), len(samples)]
        return [_mp3(samples[a:b].tobytes(), rate, channels) for a, b in zip(cuts, cuts[1:], strict=False)]


def _wav(samples: array, rate: int, channels: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples.tobytes())
    return buffer.getvalue()


def sentence_cuts(
    samples: array, rate: int, channels: int, texts: list[str], starts: list[float] | None = None
) -> list[int]:
    """Where each sentence after the first starts, as sample positions: in the middle of a pause, the pauses
    chosen in order to be long and near where each sentence is expected to start, by the timer's answer when
    it is usable, else by the sentences' lengths and punctuation. Raises NoPlaceToCut when there are fewer
    pauses than breaks."""
    if len(texts) < 2:
        return []
    step = max(1, int(WINDOW * rate)) * channels
    quiet = [max(map(abs, samples[i : i + step]), default=0) <= QUIET for i in range(0, len(samples), step)]
    pauses, began = [], None  # (middle in seconds, length in seconds)
    for index, still in enumerate([*quiet, False]):
        if still and began is None:
            began = index
        elif not still and began is not None:
            if began > 0 and index < len(quiet) and (index - began) * WINDOW >= SHORTEST_PAUSE:
                pauses.append(((began + index) / 2 * WINDOW, (index - began) * WINDOW))
            began = None
    seconds = len(samples) / channels / rate
    usable = (
        starts is not None
        and len(starts) == len(texts) - 1
        and all(0 < at < seconds for at in starts)
        and starts == sorted(starts)
    )
    expected = starts if usable else _expected_from_lengths(texts, seconds)
    if len(pauses) < len(expected):
        raise NoPlaceToCut(f"{len(pauses)} pauses for {len(expected)} sentence breaks")
    chosen = _best_in_order(pauses, expected)  # in pauses only: a cut inside a word splits it
    frame = channels
    return [int(at * rate) * frame for at in chosen]


class NoPlaceToCut(Exception):
    """A run's recording has fewer pauses than it has sentence breaks."""


def _stretches(misplaced: list[int], count: int) -> list[tuple[int, int]]:
    """The sentences (first, last) around each group of neighbouring misplaced breaks, to read again."""
    stretches: list[tuple[int, int]] = []
    for k in misplaced:
        if stretches and stretches[-1][1] >= k:
            stretches[-1] = (stretches[-1][0], k + 1)
        else:
            stretches.append((k, k + 1))
    return [(first, min(last, count - 1)) for first, last in stretches]


def _expected_from_lengths(texts: list[str], seconds: float) -> list[float]:
    weights = [_weight(text) for text in texts]
    total = sum(weights) + _BETWEEN_SENTENCES * (len(texts) - 1)
    expected, so_far = [], 0.0
    for weight in weights[:-1]:
        so_far += weight + _BETWEEN_SENTENCES / 2
        expected.append(seconds * so_far / total)
        so_far += _BETWEEN_SENTENCES / 2
    return expected


def _weight(text: str) -> float:
    weight = float(len(text))
    for marks, worth in _PAUSE_WEIGHTS:
        weight += worth * sum(text.count(mark) for mark in marks)
    return weight


def _best_in_order(candidates: list[tuple[float, float]], expected: list[float]) -> list[float]:
    """One candidate pause per expected cut, in order, scoring each by its length less its distance."""
    count, width = len(expected), len(candidates)
    best = [[float("-inf")] * width for _ in range(count)]
    came = [[-1] * width for _ in range(count)]
    for k, at in enumerate(expected):
        running, running_at = float("-inf"), -1  # the best score of the cuts before, among earlier candidates
        for j, (middle, length) in enumerate(candidates):
            if k > 0 and j > 0 and best[k - 1][j - 1] > running:
                running, running_at = best[k - 1][j - 1], j - 1
            score = min(length, 1.5) - _DISTANCE_COST * abs(middle - at)
            if k == 0:
                best[k][j] = score
            elif running_at >= 0:
                best[k][j], came[k][j] = running + score, running_at
    j = max(range(width), key=lambda index: best[count - 1][index])
    chosen = []
    for k in range(count - 1, -1, -1):
        chosen.append(candidates[j][0])
        j = came[k][j]
    return chosen[::-1]


def _mp3(frames: bytes, rate: int, channels: int) -> Speech:
    """16-bit PCM as MP3, with its length."""
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(MP3_KBPS)
    encoder.set_in_sample_rate(rate)
    encoder.set_channels(channels)
    encoder.set_quality(2)
    audio = bytes(encoder.encode(frames) + encoder.flush())
    return Speech(audio=audio, media_type="audio/mpeg", duration_seconds=len(frames) / (2 * channels * rate))


def _trim(frames: bytes, rate: int, channels: int) -> bytes:
    """The audio without the silence at its ends, beyond a short margin; unchanged if it is all quiet."""
    samples = array("h", frames)
    loud = [index for index in range(0, len(samples), channels) if abs(samples[index]) > QUIET]
    if not loud:
        return frames
    first = max(0, loud[0] - int(KEEP_BEFORE * rate) * channels)
    last = min(len(samples), loud[-1] + int(KEEP_AFTER * rate) * channels)
    first -= first % channels
    return samples[first:last].tobytes()


def _check(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        body = response.json()
    except ValueError:
        body = {}
    if isinstance(body, list):  # some errors come as a list of one
        body = body[0] if body and isinstance(body[0], dict) else {}
    error = body.get("error", {}) or {}
    status = error.get("status", "")
    message = f"{response.status_code} {status}".strip()
    details = error.get("details", []) or []
    if response.status_code in (401, 403) or status in ("PERMISSION_DENIED", "UNAUTHENTICATED"):
        raise ProviderAccountError(message)
    if response.status_code == 429:
        text = error.get("message") or ""
        quotas = [v.get("quotaId", "") for d in details for v in d.get("violations", []) or []]
        if any("PerDay" in quota for quota in quotas) or "per day" in text.lower():  # nothing more today
            raise ProviderQuotaExhausted(f"{message} daily quota", _wait(details, text) or 3600.0)
        raise ProviderBusy(message, min(max(_wait(details, text) or 10.0, 1.0), 60.0))
    if response.status_code in (500, 503, 504):
        raise ProviderBusy(message, 5.0)
    if response.status_code == 400 and re.search(r"safety|blocked|policy", error.get("message") or "", re.IGNORECASE):
        raise ProviderRefused(message)  # "Request blocked for an unspecified policy reason", as for garbled text
    raise RuntimeError(f"Gemini request failed: {message}: {(error.get('message') or '')[:300]}")


def _wait(details: list, text: str) -> float | None:
    """Seconds to wait: the error's retryDelay ("12s"), or its message ("Please retry in 3h41m5s")."""
    delays = [str(detail.get("retryDelay", "")) for detail in details]
    delays += re.findall(r"retry in ((?:\d+h)?(?:\d+m)?(?:\d+(?:\.\d+)?s)?)", text)
    for delay in delays:
        match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", delay)
        if delay and match:
            hours, minutes, seconds = match.groups()
            return 3600 * int(hours or 0) + 60 * int(minutes or 0) + float(seconds or 0)
    return None

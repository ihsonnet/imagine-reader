"""OpenAI connectors for narration and sketches.

Uses the HTTP API directly (httpx), so the engine does not need the OpenAI
SDK. Errors are sorted into kinds the server treats differently: a refusal
(retrying the same input will not help), an account problem (stop all work
until it is fixed), a busy service (wait, then send the same request again),
and anything else (retry later).
"""

import base64
import io
import json
import re
from collections.abc import Sequence

import httpx
from PIL import Image

from ..story.models import SketchSpec
from .audio import mp3_duration
from .image import REFERENCE_SIZE, Picture, Reference
from .speech import Speech

API = "https://api.openai.com/v1"
NARRATION_INSTRUCTIONS = (
    "Read this passage from a book aloud as a calm, natural audiobook narrator. "
    "Read the words exactly as written, without adding, skipping, or commenting on anything."
)
STORY_INSTRUCTIONS = (
    "Read this passage from a story book aloud like a warm, gentle storyteller reading to a listener. "
    "Speak slowly and unhurriedly, letting each phrase breathe, with natural pauses at commas, colons, and full stops. "
    "Let the feeling of the moment come through softly, and give spoken lines a little life without putting on voices. "
    "Read the words exactly as written, without adding, skipping, or commenting on anything."
)
# Instructions and speaking speed for each narration style; 0.85 is about 140 words a minute.
SPEECH_STYLES = {"story": (STORY_INSTRUCTIONS, 0.85), "plain": (NARRATION_INSTRUCTIONS, 0.95)}
_MAX_SPEECH_INPUT = 4000  # characters per request; longer passages are sent in pieces
# US dollars per picture, by model, quality, and size, from OpenAI's token prices: the picture's output tokens
# (low 1024x1024 is 272, 1536x1024 is 400; medium 1056 and 1584; high 4160 and 6240) at $8 a million on
# gpt-image-1-mini and $40 on gpt-image-1, plus about 300 tokens of prompt. The older flat list prices
# ($0.006 for low 1536x1024 on mini) were about half as high again as what is billed.
_OUTPUT_TOKENS = {
    ("low", "1024x1024"): 272, ("low", "1536x1024"): 400,
    ("medium", "1024x1024"): 1056, ("medium", "1536x1024"): 1584,
    ("high", "1024x1024"): 4160, ("high", "1536x1024"): 6240,
}  # fmt: skip
_TOKEN_PRICES = {"gpt-image-1-mini": (2.00, 8.00), "gpt-image-1": (5.00, 40.00)}  # text in, image out, per million
IMAGE_PRICES = {
    (model, quality, size): round((300 * text_in + tokens * image_out) / 1_000_000, 5)
    for model, (text_in, image_out) in _TOKEN_PRICES.items()
    for (quality, size), tokens in _OUTPUT_TOKENS.items()
}
UNKNOWN_IMAGE_PRICE = 0.25  # an unlisted combination is assumed to be expensive
# US dollars for sending one 512 x 512 reference picture with a request: 256 image tokens of input
# ($2.50 a million on gpt-image-1-mini, $10 on gpt-image-1), rounded up.
REFERENCE_INPUT_PRICES = {"gpt-image-1-mini": 0.001, "gpt-image-1": 0.003}
REFERENCE_NOTE = (
    "Reference pictures, in order: {names}. Draw {them} with exactly the same face, hair, build, and clothes as in "
    "the reference pictures, in this scene's poses, place, and light. Do not copy the references' poses or plain "
    "backgrounds, and do not make anyone else in the picture look like {them}."
)
# US dollars per million tokens, (input, output), for reading books (OpenAI's list prices).
TEXT_PRICES = {
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
}
UNKNOWN_TEXT_PRICE = (2.50, 15.00)
_ANSWER_TOKENS = 3000  # a cast list is about 1,000 tokens; allow for the model's reasoning too


_EXTENSIONS = {"image/webp": ".webp", "image/png": ".png", "image/jpeg": ".jpg"}


class ProviderRefused(Exception):
    """The provider declined this input, for example by moderation. Retrying will not help."""


class ProviderAccountError(Exception):
    """The key is missing, wrong, or out of credit. Nothing will work until it is fixed."""


class ProviderBusy(Exception):
    """Too many requests at once, or the service is overloaded. The same request can be sent again later."""

    def __init__(self, message: str, retry_after: float = 5.0):
        super().__init__(message)
        self.retry_after = retry_after


# Errors no waiting fixes: the key, or the account's credit (a 429 too, when the balance has run out).
ACCOUNT_CODES = ("insufficient_quota", "invalid_api_key", "credit_balance_exhausted", "billing_hard_limit_reached")


def _check(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        error = response.json().get("error", {}) or {}
    except ValueError:
        error = {}
    code = error.get("code") or error.get("type") or ""
    message = f"{response.status_code} {code}".strip()
    if response.status_code in (401, 403) or code in ACCOUNT_CODES:
        raise ProviderAccountError(message)
    if response.status_code == 400 and code in ("moderation_blocked", "content_policy_violation"):
        raise ProviderRefused(message)
    if response.status_code in (429, 503):
        raise ProviderBusy(message, _retry_after(response))
    raise RuntimeError(f"OpenAI request failed: {message}")


def _retry_after(response: httpx.Response) -> float:
    """Seconds to wait before trying again: the Retry-After header, or a rate-limit reset like "1.5s"."""
    for header in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        value = response.headers.get(header, "")
        match = re.fullmatch(r"(?:(\d+)m)?(\d+(?:\.\d+)?)(ms|s)?", value.strip())
        if match:
            minutes, amount, unit = match.groups()
            seconds = float(amount) / (1000 if unit == "ms" else 1) + 60 * int(minutes or 0)
            return min(max(seconds, 1.0), 60.0)
    return 5.0


class _OpenAI:
    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self._api_key = api_key
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:  # created on first use, so building a provider is cheap
            self._client = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {self._api_key}"})
        return self._client


class OpenAITranscriber(_OpenAI):
    """What a recording says, as OpenAI's recogniser hears it: to check where a narrator's run was cut."""

    usd_per_minute = 0.006  # gpt-4o-transcribe

    def __init__(self, api_key: str, model: str = "gpt-4o-transcribe", client: httpx.Client | None = None):
        super().__init__(api_key, client)
        self.model = model

    def transcribe(self, audio: bytes, media_type: str, language: str | None) -> str:
        data = {"model": self.model, "response_format": "json", **({"language": language} if language else {})}
        extension = media_type.split("/")[-1].replace("mpeg", "mp3")
        response = self.client.post(
            "/audio/transcriptions", data=data, files={"file": (f"clip.{extension}", audio, media_type)}, timeout=120
        )
        _check(response)
        return response.json().get("text", "")


class OpenAISpeechProvider(_OpenAI):
    voices = (
        "marin",
        "cedar",
        "coral",
        "sage",
        "ash",
        "ballad",
        "verse",
        "alloy",
        "echo",
        "fable",
        "nova",
        "onyx",
        "shimmer",
    )

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini-tts",
        default_voice: str = "marin",
        usd_per_million_characters: float = 17.0,
        client: httpx.Client | None = None,
    ):
        super().__init__(api_key, client)
        self.model = model
        self.default_voice = default_voice
        self.usd_per_million_characters = usd_per_million_characters
        self.id = f"openai-{model}-2"  # bump the suffix when the instructions or speeds change

    def estimate_cost(self, text: str) -> float:
        return len(text) * self.usd_per_million_characters / 1_000_000

    def synthesize(self, text: str, voice: str, style: str = "plain") -> Speech:
        if voice not in self.voices:
            raise ValueError(f"unknown voice {voice!r}")
        instructions, speed = SPEECH_STYLES[style]
        audio = b"".join(self._speak(piece, voice, instructions, speed) for piece in _pieces(text, _MAX_SPEECH_INPUT))
        return Speech(audio=audio, media_type="audio/mpeg", duration_seconds=mp3_duration(audio))

    def _speak(self, text: str, voice: str, instructions: str, speed: float) -> bytes:
        response = self.client.post(
            "/audio/speech",
            json={
                "model": self.model,
                "voice": voice,
                "input": text,
                "instructions": instructions,
                "speed": speed,
                "response_format": "mp3",
            },
            timeout=120,
        )
        _check(response)
        return response.content


class OpenAIImageProvider(_OpenAI):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-image-1-mini",
        quality: str = "low",
        size: str = "1536x1024",
        usd_per_image: float | None = None,  # None: OpenAI's list price for the model, quality, and size
        client: httpx.Client | None = None,
    ):
        super().__init__(api_key, client)
        self.model, self.quality, self.size = model, quality, size
        if usd_per_image is None:
            usd_per_image = IMAGE_PRICES.get((model, quality, size), UNKNOWN_IMAGE_PRICE)
        self.usd_per_image = usd_per_image
        self.usd_per_reference = REFERENCE_INPUT_PRICES.get(model, UNKNOWN_IMAGE_PRICE / 10)
        self.id = f"openai-{model}-{quality}-{size}-1"

    def estimate_cost(self, spec: SketchSpec) -> float:
        return self.usd_per_image + len(spec.references) * self.usd_per_reference

    def reference_cost(self) -> float:
        return IMAGE_PRICES.get((self.model, self.quality, REFERENCE_SIZE), UNKNOWN_IMAGE_PRICE)

    def render(self, spec: SketchSpec, references: Sequence[Reference] = (), size: str | None = None) -> Picture:
        """Draw the spec's picture; with references, as an edit that is shown how those characters look."""
        fields = {
            "model": self.model,
            "size": size or self.size,
            "quality": self.quality,
            "n": 1,
            "output_format": "webp",
            "output_compression": 82,
        }
        if references:
            names = [reference.name for reference in references]
            listed = "; ".join(f"{number}. {name}" for number, name in enumerate(names, 1))
            them = names[0] if len(names) == 1 else "them"
            note = REFERENCE_NOTE.format(names=listed, them=them)
            files = [
                ("image[]", (f"reference-{number}{_EXTENSIONS.get(r.picture.media_type, '')}", r.picture.data,
                             r.picture.media_type))
                for number, r in enumerate(references, 1)
            ]  # fmt: skip
            data = {**{key: str(value) for key, value in fields.items()}, "prompt": f"{spec.prompt} {note}"}
            response = self.client.post("/images/edits", data=data, files=files, timeout=180)
        else:
            response = self.client.post("/images/generations", json={**fields, "prompt": spec.prompt}, timeout=180)
        _check(response)
        data = base64.b64decode(response.json()["data"][0]["b64_json"])
        width, height = Image.open(io.BytesIO(data)).size
        return Picture(data, "image/webp", width, height)


class OpenAITextModel(_OpenAI):
    """Reads text and answers in JSON that follows a schema (structured outputs)."""

    def __init__(self, api_key: str, model: str = "gpt-5.4-mini", client: httpx.Client | None = None):
        super().__init__(api_key, client)
        self.model = model
        self.prices = TEXT_PRICES.get(model, UNKNOWN_TEXT_PRICE)
        self.id = f"openai-{model}-1"

    def estimate_cost(self, characters: int, answer_tokens: int | None = None) -> float:
        tokens_in = characters / 4  # about four characters a token in English
        answer = _ANSWER_TOKENS if answer_tokens is None else answer_tokens
        return (tokens_in * self.prices[0] + answer * self.prices[1]) / 1_000_000

    def json(self, instructions: str, text: str, schema: dict, name: str) -> tuple[dict, float]:
        response = self.client.post(
            "/chat/completions",
            json={
                "model": self.model,
                "reasoning_effort": "low",
                "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": text}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": name, "schema": schema, "strict": True},
                },
            },
            timeout=300,
        )
        _check(response)
        body = response.json()
        usage = body.get("usage") or {}
        cost = (usage.get("prompt_tokens", 0) * self.prices[0] + usage.get("completion_tokens", 0) * self.prices[1]) / (
            1_000_000
        )
        message = body["choices"][0]["message"]
        if message.get("refusal"):
            raise ProviderRefused(f"declined: {message['refusal'][:80]}")
        return json.loads(message["content"]), cost


def _pieces(text: str, limit: int) -> list[str]:
    """Split text at spaces into pieces no longer than ``limit``."""
    pieces, current = [], ""
    for word in text.split(" "):
        if current and len(current) + 1 + len(word) > limit:
            pieces.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    return [*pieces, current] if current else pieces

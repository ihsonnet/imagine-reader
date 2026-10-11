"""OpenRouter: one key for the reader's language model, its storyteller voice, the check of every clip, and (without
a Cloudflare account) its pictures.

OpenRouter passes each request to the same models this reader uses with their makers' own keys, at their makers'
prices: OpenAI's gpt-5.4-mini plans the pictures, Google's Gemini TTS reads aloud (voice Charon), Gemini Flash hears
where each sentence of a run starts, OpenAI's gpt-4o-transcribe hears each clip, and Black Forest Labs' FLUX.2
[klein] 4B draws. Requests follow OpenAI's shapes. Each provider here keeps the id of the same model reached
directly, so a book's plans, readings, and recordings serve either key; the pictures are the exception, since
OpenRouter's FLUX.2 [klein] draws at its own size.

Errors are sorted like the other providers': no credit or a bad key (no waiting fixes it), busy, declined, and the
rest. FLUX.2 [klein] here costs about $0.014 a megapixel, some ten times Cloudflare's price for the same model.
"""

import base64
import io
import json
import re
import wave
from collections.abc import Sequence

import httpx

from ..story.models import SketchSpec
from .cloudflare import flux_prompt, small_png, webp
from .gemini import STRICT_TIMING_SCHEMA, GeminiSentenceTimer, GeminiSpeechProvider, SentenceTimer
from .image import REFERENCE_SIZE, Picture, Reference
from .openai import _ANSWER_TOKENS, TEXT_PRICES, UNKNOWN_TEXT_PRICE, ProviderAccountError, ProviderBusy, ProviderRefused

API = "https://openrouter.ai/api/v1"
HEADERS = {"X-Title": "Imagine-Reader"}  # how OpenRouter names this app on its pages
PCM_RATE = 24_000  # Gemini's speech, as OpenRouter returns it: 16-bit mono PCM at 24 kHz
FLUX = "black-forest-labs/flux.2-klein-4b"
FLUX_USD_PER_MEGAPIXEL = 0.014
FLUX_MEGAPIXELS = 1.1  # about what a 3:2 picture comes out at; a square reference about 1
_REFUSED = re.compile(r"safety|blocked|policy|moderation|flagged", re.IGNORECASE)


class _OpenRouter:
    route = "openrouter"  # where the requests go (the ids name the models, as reached directly)

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self._api_key, self._client = api_key, client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:  # created on first use, so building a provider is cheap
            headers = {"Authorization": f"Bearer {self._api_key}", **HEADERS}
            self._client = httpx.Client(base_url=API, headers=headers)
        return self._client


def _check(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        error = response.json().get("error") or {}
    except ValueError:
        error = {}
    text = str(error.get("message") or "")
    message = f"{response.status_code} {text[:200]}".strip()
    if _REFUSED.search(text) and response.status_code in (400, 403):
        raise ProviderRefused(message)
    if response.status_code in (401, 402, 403):  # a bad key, or no credit left
        raise ProviderAccountError(message)
    if response.status_code == 429:
        raise ProviderBusy(message, min(max(float(response.headers.get("retry-after", 10) or 10), 1.0), 60.0))
    if response.status_code in (408, 500, 502, 503, 504):
        raise ProviderBusy(message, 5.0)
    raise RuntimeError(f"OpenRouter request failed: {message}")


class OpenRouterTextModel(_OpenRouter):
    """OpenAI's language model through OpenRouter, answering in JSON that follows a schema."""

    def __init__(self, api_key: str, model: str = "gpt-5.4-mini", client: httpx.Client | None = None):
        super().__init__(api_key, client)
        self.model = model
        self.prices = TEXT_PRICES.get(model, UNKNOWN_TEXT_PRICE)
        self.id = f"openai-{model}-1"  # the same model as OpenAI's own, so plans and readings serve both

    def estimate_cost(self, characters: int, answer_tokens: int | None = None) -> float:
        tokens_in = characters / 4  # about four characters a token in English
        answer = _ANSWER_TOKENS if answer_tokens is None else answer_tokens
        return (tokens_in * self.prices[0] + answer * self.prices[1]) / 1_000_000

    def json(self, instructions: str, text: str, schema: dict, name: str) -> tuple[dict, float]:
        response = self.client.post(
            "/chat/completions",
            json={
                "model": f"openai/{self.model}",
                "reasoning": {"effort": "low"},
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


class OpenRouterSpeechProvider(GeminiSpeechProvider):
    """Gemini's storyteller through OpenRouter: the same model, voice, style, cutting, and checks as Google's own,
    and the same id, so a recording made either way serves both."""

    route = "openrouter"

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            headers = {"Authorization": f"Bearer {self._api_key}", **HEADERS}
            self._client = httpx.Client(base_url=API, headers=headers)
        return self._client

    def _speak(self, text: str, voice: str, direction: str) -> bytes:
        response = self.client.post(
            "/audio/speech",
            json={
                "model": f"google/{self.model}",
                "input": text,
                "voice": voice,
                "instructions": direction,  # sent on to Gemini as its speech_metadata style
                "response_format": "pcm",  # Gemini answers only in PCM
            },
            timeout=180,
        )
        _check(response)
        if not response.content:
            raise ProviderRefused("no audio in the answer")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(PCM_RATE)
            out.writeframes(response.content)
        return buffer.getvalue()


class OpenRouterSentenceTimer(GeminiSentenceTimer):
    """When each sentence of a run starts, as Gemini Flash hears it through OpenRouter."""

    route = "openrouter"

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            headers = {"Authorization": f"Bearer {self._api_key}", **HEADERS}
            self._client = httpx.Client(base_url=API, headers=headers)
        return self._client

    def _ask(self, wav_base64: str, prompt: str, schema: dict) -> dict:
        audio = {"type": "input_audio", "input_audio": {"data": wav_base64, "format": "wav"}}
        response = self.client.post(
            "/chat/completions",
            json={
                "model": f"google/{self.model}",
                "messages": [{"role": "user", "content": [audio, {"type": "text", "text": prompt}]}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "starts", "schema": STRICT_TIMING_SCHEMA, "strict": True},
                },
            },
            timeout=120,
        )
        _check(response)
        return json.loads(response.json()["choices"][0]["message"]["content"])


class OpenRouterTranscriber(_OpenRouter):
    """What a recording says, as OpenAI's recogniser hears it through OpenRouter: to check where a run was cut."""

    usd_per_minute = 0.006  # gpt-4o-transcribe

    def __init__(self, api_key: str, model: str = "gpt-4o-transcribe", client: httpx.Client | None = None):
        super().__init__(api_key, client)
        self.model = model

    def transcribe(self, audio: bytes, media_type: str, language: str | None) -> str:
        audio_format = media_type.split("/")[-1].replace("mpeg", "mp3")
        body = {
            "model": f"openai/{self.model}",
            "input_audio": {"data": base64.b64encode(audio).decode(), "format": audio_format},
            **({"language": language} if language else {}),
        }
        response = self.client.post("/audio/transcriptions", json=body, timeout=120)
        _check(response)
        return response.json().get("text", "")


class OpenRouterImageProvider(_OpenRouter):
    """FLUX.2 [klein] 4B through OpenRouter, asked as Cloudflare's is: the style said again at the end, and the head
    and shoulders of each reference picture. It draws at its own size for the shape asked (3:2, or square for a
    reference), about a megapixel."""

    def __init__(self, api_key: str, model: str = FLUX, client: httpx.Client | None = None):
        super().__init__(api_key, client)
        self.model = model
        self.id = f"openrouter-{model.rsplit('/', 1)[-1]}-3x2-1"

    def estimate_cost(self, spec: SketchSpec) -> float:
        return FLUX_MEGAPIXELS * FLUX_USD_PER_MEGAPIXEL

    def reference_cost(self) -> float:
        return FLUX_USD_PER_MEGAPIXEL

    def render(self, spec: SketchSpec, references: Sequence[Reference] = (), size: str | None = None) -> Picture:
        references = list(references)[:4]
        square = size == REFERENCE_SIZE or (size is not None and len(set(size.split("x"))) == 1)
        body = {
            "model": self.model,
            "prompt": flux_prompt(spec, references),
            "aspect_ratio": "1:1" if square else "3:2",
            "output_format": "png",
        }
        if references:
            body["input_references"] = [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64," + base64.b64encode(small_png(r.picture)).decode()},
                }
                for r in references
            ]
        response = self.client.post("/images", json=body, timeout=180)
        _check(response)
        images = response.json().get("data") or []
        if not images or not images[0].get("b64_json"):
            raise ProviderRefused("no image in the answer")
        return webp(base64.b64decode(images[0]["b64_json"]))


__all__ = [
    "OpenRouterImageProvider",
    "OpenRouterSentenceTimer",
    "OpenRouterSpeechProvider",
    "OpenRouterTextModel",
    "OpenRouterTranscriber",
    "SentenceTimer",
]

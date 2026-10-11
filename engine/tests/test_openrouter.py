"""OpenRouter: one key for the language model, Gemini's storyteller, the clip check, the timer, and FLUX pictures."""

import base64
import io
import json
import math
import struct

import httpx
import pytest
from PIL import Image

from imagine_reader.providers import (
    GeminiSpeechProvider,
    OpenRouterImageProvider,
    OpenRouterSentenceTimer,
    OpenRouterSpeechProvider,
    OpenRouterTextModel,
    OpenRouterTranscriber,
    Picture,
    ProviderAccountError,
    ProviderBusy,
    ProviderRefused,
    Reference,
)
from imagine_reader.story.models import SketchSpec

API = "https://openrouter.ai/api/v1"


def _client(handler) -> httpx.Client:
    return httpx.Client(base_url=API, transport=httpx.MockTransport(handler))


def _pcm(sound_seconds: float, quiet_seconds: float = 0.3) -> bytes:
    quiet = [0] * int(quiet_seconds * 24000)
    sound = [int(8000 * math.sin(i / 9)) for i in range(int(sound_seconds * 24000))]
    samples = quiet + sound + quiet
    return struct.pack(f"<{len(samples)}h", *samples)


def test_the_language_model_answers_in_json_as_openais_own_and_keeps_its_id():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        answer = {"choices": [{"message": {"content": json.dumps({"shots": []})}}]}
        return httpx.Response(200, json={**answer, "usage": {"prompt_tokens": 1000, "completion_tokens": 200}})

    model = OpenRouterTextModel("or-test", client=_client(handler))
    answer, cost = model.json("Plan the pictures.", "Once upon a time.", {"type": "object"}, "storyboard")
    assert answer == {"shots": []} and cost == pytest.approx((1000 * 0.75 + 200 * 4.5) / 1e6)
    body = seen[0]
    assert body["model"] == "openai/gpt-5.4-mini" and body["reasoning"] == {"effort": "low"}
    assert body["response_format"]["json_schema"]["strict"] is True
    assert model.id == "openai-gpt-5.4-mini-1"  # as OpenAI's own: a book's plans serve either key


def test_geminis_storyteller_reads_through_openrouter_as_it_does_through_google():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=_pcm(2.0), headers={"content-type": "audio/pcm"})

    provider = OpenRouterSpeechProvider("or-test", client=_client(handler))
    speech = provider.synthesize("পার্ব্বতী হাততালি দিয়া মাটিতে লুটাইয়া পড়িল।", "Charon", "story")
    body = seen[0]
    assert seen and body["model"] == "google/gemini-3.8-flash-lite-tts" and body["voice"] == "Charon"
    assert body["instructions"] == "a warm native Bengali storyteller, unhurried" and body["response_format"] == "pcm"
    assert speech.media_type == "audio/mpeg" and speech.duration_seconds == pytest.approx(2.2, abs=0.05)
    assert provider.id == GeminiSpeechProvider("g-test").id  # the same voice either way: its clips serve both


def test_openrouters_errors_are_sorted_like_the_other_providers():
    def answering(status, message="", headers=None):
        reply = httpx.Response(status, json={"error": {"code": status, "message": message}}, headers=headers or {})
        return OpenRouterSpeechProvider("or-test", client=_client(lambda request: reply))

    with pytest.raises(ProviderAccountError):  # no credit: waiting does not help
        answering(402, "Insufficient credits").synthesize("আবার!", "Charon")
    with pytest.raises(ProviderAccountError):
        answering(401, "No auth credentials found").synthesize("আবার!", "Charon")
    with pytest.raises(ProviderBusy) as busy:
        answering(429, "Rate limit exceeded", {"retry-after": "12"}).synthesize("আবার!", "Charon")
    assert busy.value.retry_after == 12
    with pytest.raises(ProviderRefused):
        answering(400, "Request blocked for an unspecified policy reason").synthesize("আবার!", "Charon")
    with pytest.raises(ProviderRefused):
        answering(403, "Your input was flagged by moderation").synthesize("আবার!", "Charon")


def test_the_timer_hears_gemini_flash_through_openrouter():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        content = json.dumps({"starts": [{"sentence": 3, "seconds": 9.1}, {"sentence": 2, "seconds": 5.1}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    timer = OpenRouterSentenceTimer("or-test", client=_client(handler))
    assert timer.sentence_starts(b"RIFF", ["one", "two", "three"]) == [5.1, 9.1]
    body = seen[0]
    audio, prompt = body["messages"][0]["content"]
    assert body["model"] == "google/gemini-3.8-flash" and audio["type"] == "input_audio"
    assert audio["input_audio"] == {"data": base64.b64encode(b"RIFF").decode(), "format": "wav"}
    assert "1. one\n2. two\n3. three" in prompt["text"]
    schema = body["response_format"]["json_schema"]["schema"]
    assert (
        schema["additionalProperties"] is False
        and schema["properties"]["starts"]["items"]["additionalProperties"] is False
    )


def test_each_clip_is_heard_through_openrouters_transcription():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"text": "আমি বাড়ি যাব।", "usage": {"seconds": 1.2}})

    heard = OpenRouterTranscriber("or-test", client=_client(handler)).transcribe(b"ID3", "audio/mpeg", "bn")
    path, body = seen[0]
    assert heard == "আমি বাড়ি যাব।" and path == "/api/v1/audio/transcriptions"
    assert body == {
        "model": "openai/gpt-4o-transcribe",
        "input_audio": {"data": "SUQz", "format": "mp3"},
        "language": "bn",
    }


def _png() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (12, 8), "white").save(buffer, "PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def test_flux_draws_through_openrouter_with_its_references_and_shape():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"b64_json": _png(), "media_type": "image/png"}]})

    artist = OpenRouterImageProvider("or-test", client=_client(handler))
    spec = SketchSpec(
        scene_id="s", reveal_boundary=0, prompt="Pencil sketch. Scene: Nora climbs the stairs.", caption="",
        characters=["Nora"], location=None, cache_key="k", references=["chr-001"],
    )  # fmt: skip
    portrait = Picture(base64.b64decode(_png()), "image/png", 12, 8)
    picture = artist.render(spec, [Reference("Nora", portrait)])
    body = seen[0]
    assert body["model"] == "black-forest-labs/flux.2-klein-4b" and body["aspect_ratio"] == "3:2"
    assert (
        body["prompt"].endswith("do not make anyone else look like Nora.")
        and "Drawn entirely as: Pencil sketch" in body["prompt"]
    )
    assert body["input_references"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert picture.media_type == "image/webp"
    artist.render(spec, size="1024x1024")
    assert seen[-1]["aspect_ratio"] == "1:1" and "input_references" not in seen[-1]  # a reference portrait
    assert artist.estimate_cost(spec) == pytest.approx(0.0154) and artist.id == "openrouter-flux.2-klein-4b-3x2-1"

"""Provider connectors, tested without network access."""

import base64
import io
import json

import httpx
import pytest
from PIL import Image

from imagine_reader.providers import (
    OpenAIImageProvider,
    OpenAISpeechProvider,
    ProviderAccountError,
    ProviderBusy,
    ProviderRefused,
    measure_duration,
    mp3_duration,
)
from imagine_reader.story import SketchSpec

# MPEG-1 Layer III, 128 kbps, 44.1 kHz, stereo: 417-byte frames of 1152 samples.
MPEG1_HEADER = bytes([0xFF, 0xFB, 0x90, 0x00])
# MPEG-2 Layer III, 64 kbps, 24 kHz, mono: 192-byte frames of 576 samples.
MPEG2_HEADER = bytes([0xFF, 0xF3, 0x84, 0xC0])


def frames(header: bytes, length: int, count: int) -> bytes:
    return (header + bytes(length - 4)) * count


def test_mp3_duration_follows_frame_headers():
    assert mp3_duration(frames(MPEG1_HEADER, 417, 100)) == pytest.approx(100 * 1152 / 44100)
    assert mp3_duration(frames(MPEG2_HEADER, 192, 250)) == pytest.approx(250 * 576 / 24000)


def test_mp3_duration_skips_id3_tags_and_reads_xing_counts():
    id3 = b"ID3" + bytes([4, 0, 0, 0, 0, 0, 20]) + bytes(20)
    assert mp3_duration(id3 + frames(MPEG2_HEADER, 192, 10)) == pytest.approx(10 * 576 / 24000)

    xing = bytearray(MPEG2_HEADER + bytes(188))
    xing[4 + 9 : 4 + 9 + 12] = b"Xing" + (1).to_bytes(4, "big") + (500).to_bytes(4, "big")
    assert mp3_duration(bytes(xing) + frames(MPEG2_HEADER, 192, 3)) == pytest.approx(500 * 576 / 24000)
    assert measure_duration(frames(MPEG2_HEADER, 192, 10), "audio/mpeg") == pytest.approx(0.24)


def fake_openai(handler):
    return httpx.Client(base_url="https://api.openai.com/v1", transport=httpx.MockTransport(handler))


def test_openai_speech_requests_the_original_text_as_mp3():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=frames(MPEG2_HEADER, 192, 125))

    provider = OpenAISpeechProvider("sk-test", client=fake_openai(handler))
    speech = provider.synthesize("Alice was beginning to get very tired.", "marin")
    assert speech.media_type == "audio/mpeg" and speech.duration_seconds == pytest.approx(3.0)
    assert seen[0]["model"] == "gpt-4o-mini-tts" and seen[0]["voice"] == "marin"
    assert seen[0]["input"] == "Alice was beginning to get very tired."
    assert seen[0]["response_format"] == "mp3" and "exactly as written" in seen[0]["instructions"]
    assert seen[0]["speed"] == pytest.approx(0.95)
    assert provider.estimate_cost("x" * 1000) == pytest.approx(0.017)


def test_stories_are_told_slowly_like_a_storyteller():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=frames(MPEG2_HEADER, 192, 10))

    OpenAISpeechProvider("sk-test", client=fake_openai(handler)).synthesize("Once upon a time.", "marin", "story")
    assert "storyteller" in seen[0]["instructions"] and seen[0]["speed"] == pytest.approx(0.85)


def test_openai_speech_sends_long_passages_in_pieces():
    inputs = []

    def handler(request: httpx.Request) -> httpx.Response:
        inputs.append(json.loads(request.content)["input"])
        return httpx.Response(200, content=frames(MPEG2_HEADER, 192, 10))

    text = " ".join(["word"] * 2000)  # 9,999 characters
    speech = OpenAISpeechProvider("sk-test", client=fake_openai(handler)).synthesize(text, "marin")
    assert len(inputs) == 3 and all(len(piece) <= 4000 for piece in inputs)
    assert " ".join(inputs) == text
    assert speech.duration_seconds == pytest.approx(3 * 10 * 576 / 24000)


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (400, {"error": {"code": "moderation_blocked"}}, ProviderRefused),
        (401, {"error": {"code": "invalid_api_key"}}, ProviderAccountError),
        (429, {"error": {"code": "insufficient_quota"}}, ProviderAccountError),
        (429, {"error": {"code": "rate_limit_exceeded"}}, ProviderBusy),
        (503, {"error": {"code": "overloaded"}}, ProviderBusy),
        (500, {"error": {"code": "server_error"}}, RuntimeError),
    ],
)
def test_openai_errors_are_sorted_by_what_to_do_next(status, body, error):
    client = fake_openai(lambda request: httpx.Response(status, json=body))
    with pytest.raises(error):
        OpenAISpeechProvider("sk-test", client=client).synthesize("Hello.", "marin")


def test_openai_images_return_landscape_webp_sketches():
    seen = []
    buffer = io.BytesIO()
    Image.new("RGB", (1536, 1024), (240, 238, 230)).save(buffer, format="WEBP")
    encoded = base64.b64encode(buffer.getvalue()).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

    spec = SketchSpec(
        scene_id="s", reveal_boundary=3, prompt="Pencil sketch.", caption="A scene.", characters=[], location=None,
        cache_key="k",
    )  # fmt: skip
    picture = OpenAIImageProvider("sk-test", client=fake_openai(handler)).render(spec)
    assert (picture.media_type, picture.width, picture.height) == ("image/webp", 1536, 1024)
    assert seen[0] | {"prompt": "Pencil sketch."} == seen[0]
    assert (seen[0]["model"], seen[0]["size"], seen[0]["output_format"]) == ("gpt-image-1-mini", "1536x1024", "webp")


def test_a_busy_service_says_how_long_to_wait():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"error": {"code": "rate_limit_exceeded"}}, headers={"x-ratelimit-reset-requests": "1m2.5s"}
        )

    with pytest.raises(ProviderBusy) as busy:
        OpenAISpeechProvider("sk-test", client=fake_openai(handler)).synthesize("Hello.", "marin")
    assert busy.value.retry_after == pytest.approx(60.0)  # capped at a minute


def test_picture_costs_follow_the_model_quality_and_size():
    spec = SketchSpec(
        scene_id="s", reveal_boundary=0, prompt="p", caption="c", characters=[], location=None, cache_key="k"
    )
    assert OpenAIImageProvider("sk-test").estimate_cost(spec) == pytest.approx(0.0038)  # low, 1536x1024, by tokens
    assert OpenAIImageProvider("sk-test", quality="medium").estimate_cost(spec) == pytest.approx(0.01327)
    assert OpenAIImageProvider("sk-test", model="new-model").estimate_cost(spec) == pytest.approx(0.25)
    assert OpenAIImageProvider("sk-test", usd_per_image=0.01).estimate_cost(spec) == pytest.approx(0.01)

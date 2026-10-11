"""Cloudflare's FLUX.2 [klein] 4B: the multipart request, reference pictures, the answer, prices, and errors."""

import base64
import io

import httpx
import pytest
from PIL import Image

from imagine_reader.providers import (
    CloudflareImageProvider,
    Picture,
    ProviderAccountError,
    ProviderBusy,
    ProviderRefused,
    Reference,
)
from imagine_reader.story.models import SketchSpec

SPEC = SketchSpec(
    scene_id="s1",
    reveal_boundary=4,
    prompt="Pencil sketch. Scene: Nora climbs the stairs with a lantern.",
    caption="Nora climbs",
    characters=["Nora"],
    location=None,
    cache_key="k1",
    references=["chr-001"],
)


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _provider(handler, quality="low") -> CloudflareImageProvider:
    client = httpx.Client(base_url="https://api.cloudflare.com/client/v4", transport=httpx.MockTransport(handler))
    return CloudflareImageProvider("acct", "tok", quality=quality, client=client)


def test_a_picture_is_asked_for_with_its_reference_pictures_and_kept_as_webp():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": {"image": base64.b64encode(_png(1536, 1024)).decode()}})

    portrait = Picture(_png(512, 512), "image/png", 512, 512)
    picture = _provider(handler).render(SPEC, [Reference("Nora", portrait)])
    request = seen[0]
    assert request.url.path == "/client/v4/accounts/acct/ai/run/@cf/black-forest-labs/flux-2-klein-4b"
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = request.content
    assert b'name="width"\r\n\r\n1536' in body and b'name="height"\r\n\r\n1024' in body
    assert b"image 0: Nora" in body and b"Draw Nora with exactly the same face" in body
    start = body.index(b'name="input_image_0"')
    sent = Image.open(io.BytesIO(body[body.index(b"\x89PNG", start) :]))
    assert max(sent.size) < 512  # Cloudflare takes references smaller than 512 x 512
    assert sent.size == (496, 496)  # head and shoulders: the middle 60% of the width, the top 60% of the height
    assert b"Drawn entirely as: Pencil sketch" in body  # the style said again at the end
    assert picture.media_type == "image/webp" and (picture.width, picture.height) == (1536, 1024)


def test_more_detail_is_a_larger_picture_and_prices_follow_the_tiles():
    low, detailed = _provider(lambda r: None), _provider(lambda r: None, quality="medium")
    assert low.id == "cloudflare-flux-2-klein-4b-1536x1024-2" and detailed.size == "1920x1280"
    assert low.estimate_cost(SPEC) == pytest.approx(6 * 0.000287 + 0.000059)  # six tiles, one reference
    assert detailed.estimate_cost(SPEC.model_copy(update={"references": []})) == pytest.approx(12 * 0.000287)
    assert low.reference_cost() == pytest.approx(4 * 0.000287)  # a square portrait, once


@pytest.mark.parametrize(
    ("status", "message", "headers", "error"),
    [
        (401, "Authentication error", {}, ProviderAccountError),
        (429, "you have used up your daily free allocation of 10,000 neurons", {}, ProviderAccountError),
        (429, "Too many requests", {"retry-after": "7"}, ProviderBusy),
        (503, "Service unavailable", {}, ProviderBusy),
        (400, "Input prompt contains NSFW content", {}, ProviderRefused),
        (400, "Bad input", {}, RuntimeError),
    ],
)
def test_cloudflare_errors_are_sorted_like_the_others(status, message, headers, error):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"success": False, "errors": [{"message": message}]}, headers=headers)

    with pytest.raises(error) as raised:
        _provider(handler).render(SPEC)
    if error is ProviderBusy and headers:
        assert raised.value.retry_after == 7


def test_an_answer_without_a_picture_counts_as_declined():
    with pytest.raises(ProviderRefused):
        _provider(lambda request: httpx.Response(200, json={"result": {}})).render(SPEC)

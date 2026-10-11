"""Cloudflare Workers AI pictures: Black Forest Labs' FLUX.2 [klein] 4B.

The cheapest model that takes reference pictures, so a character drawn once
keeps the same face: about $0.0017 for a 1536 x 1024 picture, against about
$0.004 for gpt-image-1-mini at low quality, and fast (four fixed steps). Its
weights are open (Apache 2.0), so it could later run on hardware of our own.

A picture is asked for as a multipart form: the prompt, the size, and up to
four reference pictures (each under 512 x 512) as ``input_image_0`` to
``input_image_3``. The answer carries the picture as base64; it is kept as
WebP like every other picture. Errors are sorted like the other providers'.

Given a full-length portrait, the model copies its standing pose into every
scene; given head and shoulders, it takes the face and lets the scene set the
pose. And it can drift from the style in a dark scene (a pencil storm drawn in
blue), so the style is said again at the end of the prompt.
"""

import base64
import io
from collections.abc import Sequence

import httpx
from PIL import Image

from ..story.models import SketchSpec
from .image import REFERENCE_SIZE, Picture, Reference
from .openai import ProviderAccountError, ProviderBusy, ProviderRefused

API = "https://api.cloudflare.com/client/v4"
MODEL = "@cf/black-forest-labs/flux-2-klein-4b"
# US dollars per 512 x 512 tile of a picture drawn, and of a reference picture read (Cloudflare's list prices).
OUTPUT_TILE_USD, INPUT_TILE_USD = 0.000287, 0.000059
REFERENCE_PIXELS = 496  # references must be smaller than 512 x 512
HEAD = (0.2, 0.8, 0.6)  # the part of a full-length portrait sent: left, right, and bottom, as fractions
MAX_REFERENCES = 4
# The size of a picture by quality: "medium" (Colour, more detail) is drawn larger.
SIZES = {"low": "1536x1024", "medium": "1920x1280", "high": "1920x1280"}
REFERENCE_NOTE = (
    "The reference images show how these people look, in order: {names}. Draw {them} with exactly the same "
    "face, hair, build, and clothes as in the reference images, in this scene's poses, place, and light; do not "
    "copy the reference images' poses or plain backgrounds, and do not make anyone else look like {them}."
)
_REFUSED = ("nsfw", "safety", "flagged", "not allowed", "content policy")


def tiles(size: str) -> int:
    """512 x 512 tiles in a picture of this size ("1536x1024": 6)."""
    width, height = (int(n) for n in size.split("x"))
    return -(-width // 512) * -(-height // 512)


class CloudflareImageProvider:
    def __init__(
        self,
        account_id: str,
        api_token: str,
        model: str = MODEL,
        quality: str = "low",
        client: httpx.Client | None = None,
    ):
        self._account_id, self._api_token, self._client = account_id, api_token, client
        self.model, self.quality = model, quality
        self.size = SIZES.get(quality, SIZES["low"])
        self.id = f"cloudflare-{model.rsplit('/', 1)[-1]}-{self.size}-2"

    @property
    def client(self) -> httpx.Client:
        if self._client is None:  # created on first use, so building a provider is cheap
            self._client = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {self._api_token}"})
        return self._client

    def estimate_cost(self, spec: SketchSpec) -> float:
        return tiles(self.size) * OUTPUT_TILE_USD + len(spec.references) * INPUT_TILE_USD

    def reference_cost(self) -> float:
        return tiles(REFERENCE_SIZE) * OUTPUT_TILE_USD

    def render(self, spec: SketchSpec, references: Sequence[Reference] = (), size: str | None = None) -> Picture:
        width, height = (size or self.size).split("x")
        references = list(references)[:MAX_REFERENCES]
        prompt = flux_prompt(spec, references)
        # Every field is a part of a multipart form, which the model needs even without reference pictures.
        parts = [("prompt", (None, prompt)), ("width", (None, width)), ("height", (None, height))]
        parts += [
            (f"input_image_{number}", (f"reference-{number}.png", small_png(reference.picture), "image/png"))
            for number, reference in enumerate(references)
        ]
        response = self.client.post(f"/accounts/{self._account_id}/ai/run/{self.model}", files=parts, timeout=120)
        _check(response)
        body = response.json()
        image = (body.get("result") or {}).get("image")
        if not image:
            raise ProviderRefused("no image in the answer")
        return webp(base64.b64decode(image))


def flux_prompt(spec: SketchSpec, references: Sequence[Reference]) -> str:
    """The prompt as FLUX.2 [klein] keeps to it: the style said again at the end, and who each reference shows."""
    style = spec.prompt.split(" Scene: ", 1)[0].rstrip(".")
    prompt = f"{spec.prompt} Drawn entirely as: {style}." if " Scene: " in spec.prompt else spec.prompt
    if references:
        names = [reference.name for reference in references]
        listed = "; ".join(f"image {number}: {name}" for number, name in enumerate(names))
        prompt = f"{prompt} {REFERENCE_NOTE.format(names=listed, them=names[0] if len(names) == 1 else 'them')}"
    return prompt


def small_png(picture: Picture) -> bytes:
    """A reference portrait's head and shoulders, under Cloudflare's 512 x 512 limit."""
    image = Image.open(io.BytesIO(picture.data)).convert("RGB")
    width, height = image.size
    image = image.crop((int(width * HEAD[0]), 0, int(width * HEAD[1]), int(height * HEAD[2])))
    scale = REFERENCE_PIXELS / max(image.size)  # as large as allowed, as in the test that chose this
    image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def webp(data: bytes) -> Picture:
    image = Image.open(io.BytesIO(data)).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=82, method=4)
    return Picture(buffer.getvalue(), "image/webp", image.width, image.height)


def _check(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        errors = response.json().get("errors") or []
    except ValueError:
        errors = []
    text = " ".join(str(error.get("message", "")) for error in errors).lower()
    message = f"{response.status_code} {text[:120]}".strip()
    if response.status_code in (401, 403) or "authentication" in text or "neurons" in text:
        raise ProviderAccountError(message)  # a wrong token, or the free allocation used up without a paid plan
    if response.status_code == 429:
        raise ProviderBusy(message, min(max(float(response.headers.get("retry-after", 5) or 5), 1.0), 60.0))
    if response.status_code in (500, 502, 503, 504):
        raise ProviderBusy(message, 5.0)
    if any(word in text for word in _REFUSED):
        raise ProviderRefused(message)
    raise RuntimeError(f"Cloudflare request failed: {message}")

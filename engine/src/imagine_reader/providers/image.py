"""The contract every sketch provider implements, and a placeholder that needs no AI."""

import io
import random
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from PIL import Image, ImageDraw, ImageFont

from ..story.models import SketchSpec


@dataclass(frozen=True)
class Picture:
    data: bytes
    media_type: str  # e.g. "image/png"
    width: int
    height: int


@dataclass(frozen=True)
class Reference:
    """A picture of one character, sent with a request so the new picture draws them the same way."""

    name: str
    picture: Picture


REFERENCE_SIZE = "1024x1024"  # reference pictures are drawn square, then kept small


class ImageProvider(Protocol):
    id: str  # names the provider, model, and settings; part of every cache key

    def estimate_cost(self, spec: SketchSpec) -> float: ...  # US dollars, before the request; references included

    def reference_cost(self) -> float: ...  # US dollars for drawing one reference picture

    def render(self, spec: SketchSpec, references: Sequence[Reference] = (), size: str | None = None) -> Picture: ...


class MockImageProvider:
    """Draws a clearly labelled placeholder card: loose pencil strokes and the scene's description.

    It lets the reader show pictures changing with the story before an image
    model is connected. The same spec always produces the same picture.
    """

    id = "mock-sketch-2"
    width, height = 768, 512  # the shape of real sketches (1536 x 1024)
    paper = (245, 241, 232)

    def estimate_cost(self, spec: SketchSpec) -> float:
        return 0.0

    def reference_cost(self) -> float:
        return 0.0

    def render(self, spec: SketchSpec, references: Sequence[Reference] = (), size: str | None = None) -> Picture:
        rng = random.Random(spec.cache_key)
        width, height = (int(n) // 2 for n in size.split("x")) if size else (self.width, self.height)
        image = Image.new("RGB", (width, height), self.paper)
        draw = ImageDraw.Draw(image)

        horizon = rng.randint(height * 290 // 512, height * 350 // 512)
        for _ in range(3):  # a hand-drawn horizon
            draw.line(self._wobble(rng, 40, width - 40, horizon + rng.randint(-3, 3)), fill=(150, 145, 135))
        for _ in range(90):  # hatching below it
            x, y = rng.randint(40, width - 80), rng.randint(horizon + 10, height - 100)  # clear of the footer
            length = rng.randint(12, 40)
            shade = rng.randint(150, 200)
            draw.line([(x, y), (x + length, y - length // 2)], fill=(shade, shade - 4, shade - 12), width=1)
        for _ in range(rng.randint(1, 3)):  # a few simple shapes standing on it
            left = rng.randint(80, width - 200)
            top = rng.randint(horizon - 150, horizon - 60)
            draw.rectangle([left, top, left + rng.randint(60, 140), horizon], outline=(120, 115, 105), width=2)

        title = ImageFont.load_default(size=18)
        body = ImageFont.load_default(size=24)
        draw.text((40, 32), "PLACEHOLDER SKETCH · not AI art yet", font=title, fill=(150, 138, 120))
        y = 70
        drawable = all(ord(ch) < 0x0250 or 0x2000 <= ord(ch) <= 0x206F for ch in spec.caption)  # Latin, punctuation
        caption = spec.caption if drawable else "(the scene's words are in a script this placeholder cannot draw)"
        for line in textwrap.wrap(caption, width=52)[:5]:
            draw.text((40, y), line, font=body, fill=(70, 66, 60))
            y += 32
        details = [f"Setting: {spec.location}"] if spec.location else []
        if spec.characters:
            details.append(f"People: {', '.join(spec.characters)}")
        if references:
            details.append(f"Drawn like before: {', '.join(reference.name for reference in references)}")
        for row, line in enumerate(reversed(details)):  # bottom-up, so the last line sits at the edge
            draw.text((40, height - 44 - 26 * row), line, font=title, fill=(110, 100, 88))

        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return Picture(buffer.getvalue(), "image/png", width, height)

    @staticmethod
    def _wobble(rng: random.Random, start: int, end: int, y: int) -> list[tuple[int, int]]:
        return [(x, y + rng.randint(-2, 2)) for x in range(start, end, 24)]

"""The contract every narration provider implements.

Providers turn one passage of original text into audio. The engine measures
each clip's duration itself (``wav_duration``) rather than trusting the
provider, because playback timing is built from those measurements.
"""

import io
import wave
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Speech:
    audio: bytes
    media_type: str  # e.g. "audio/wav"
    duration_seconds: float


class SpeechProvider(Protocol):
    id: str  # names the provider, model, and settings; part of every cache key
    voices: tuple[str, ...]
    default_voice: str

    def estimate_cost(self, text: str) -> float: ...  # US dollars, before the request

    def synthesize(self, text: str, voice: str, style: str = "plain") -> Speech: ...  # style: "story" or "plain"


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as clip:
        return clip.getnframes() / clip.getframerate()

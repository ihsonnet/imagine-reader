"""Swappable AI providers. Each implements a small contract; mocks run without keys."""

from .audio import measure_duration, mp3_duration
from .image import REFERENCE_SIZE, ImageProvider, MockImageProvider, Picture, Reference
from .mock import MockSpeechProvider
from .speech import Speech, SpeechProvider, wav_duration

__all__ = [
    "REFERENCE_SIZE",
    "ImageProvider",
    "MockImageProvider",
    "MockSpeechProvider",
    "Picture",
    "Reference",
    "Speech",
    "SpeechProvider",
    "measure_duration",
    "mp3_duration",
    "wav_duration",
]

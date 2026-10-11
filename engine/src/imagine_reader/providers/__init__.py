"""Swappable AI providers. Each implements a small contract; mocks run without keys."""

from .audio import measure_duration, mp3_duration
from .checking import ClipChecker, misplaced_breaks
from .cloudflare import CloudflareImageProvider
from .gemini import GeminiSentenceTimer, GeminiSpeechProvider, ProviderQuotaExhausted
from .image import REFERENCE_SIZE, ImageProvider, MockImageProvider, Picture, Reference
from .mock import MockSpeechProvider
from .openai import (
    OpenAIImageProvider,
    OpenAISpeechProvider,
    OpenAITextModel,
    OpenAITranscriber,
    ProviderAccountError,
    ProviderBusy,
    ProviderRefused,
)
from .speech import Speech, SpeechProvider, wav_duration

__all__ = [
    "REFERENCE_SIZE",
    "ClipChecker",
    "CloudflareImageProvider",
    "GeminiSentenceTimer",
    "GeminiSpeechProvider",
    "ImageProvider",
    "MockImageProvider",
    "MockSpeechProvider",
    "OpenAIImageProvider",
    "OpenAISpeechProvider",
    "OpenAITextModel",
    "OpenAITranscriber",
    "Picture",
    "ProviderAccountError",
    "ProviderBusy",
    "ProviderQuotaExhausted",
    "ProviderRefused",
    "Reference",
    "Speech",
    "SpeechProvider",
    "measure_duration",
    "misplaced_breaks",
    "mp3_duration",
    "wav_duration",
]

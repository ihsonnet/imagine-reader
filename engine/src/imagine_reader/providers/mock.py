"""A placeholder narrator for development: no network, no cost, no model download.

It does not speak. Each clip is a quiet tick per word at a natural speaking
pace, so playback, highlighting, and timing can be built and tested before a
real voice is connected.
"""

import io
import math
import wave
from array import array

from .speech import Speech, wav_duration


class MockSpeechProvider:
    id = "mock-1"
    voices = ("placeholder",)
    default_voice = "placeholder"

    sample_rate = 8000
    words_per_minute = 165
    tick_seconds = 0.025
    tick_hz = 660
    tick_volume = 0.08

    def estimate_cost(self, text: str) -> float:
        return 0.0

    def synthesize(self, text: str, voice: str, style: str = "plain") -> Speech:
        if voice not in self.voices:
            raise ValueError(f"unknown voice {voice!r}")
        words = max(1, len(text.split()))
        word_seconds = 60 / (self.words_per_minute * (0.85 if style == "story" else 1))
        samples = array("h", bytes(2 * int((words * word_seconds + 0.1) * self.sample_rate)))
        tick = [
            int(self.tick_volume * 32767 * math.sin(2 * math.pi * self.tick_hz * n / self.sample_rate))
            for n in range(int(self.tick_seconds * self.sample_rate))
        ]
        for index in range(words):
            start = int(index * word_seconds * self.sample_rate)
            samples[start : start + len(tick)] = array("h", tick)

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as clip:
            clip.setnchannels(1)
            clip.setsampwidth(2)
            clip.setframerate(self.sample_rate)
            clip.writeframes(samples.tobytes())
        data = buffer.getvalue()
        return Speech(audio=data, media_type="audio/wav", duration_seconds=wav_duration(data))

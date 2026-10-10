"""Measure clip durations from the audio itself.

Playback timing is built from these measurements, so they come from the
file, not from a provider's claim. MP3 is measured by walking its frame
headers (or reading the Xing/Info frame count when the encoder wrote one).
"""

from .speech import wav_duration

_MPEG1_L3_KBPS = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
_MPEG2_L3_KBPS = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
_SAMPLE_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def measure_duration(audio: bytes, media_type: str) -> float:
    if media_type == "audio/wav":
        return wav_duration(audio)
    if media_type == "audio/mpeg":
        return mp3_duration(audio)
    raise ValueError(f"cannot measure {media_type}")


def mp3_duration(data: bytes) -> float:
    position = _skip_id3(data)
    seconds = 0.0
    first = True
    while position + 4 <= len(data):
        frame = _frame(data, position)
        if frame is None:
            position += 1  # resynchronize on the next frame header
            continue
        length, samples, rate, mono, mpeg1 = frame
        if first:
            total = _xing_frames(data, position, mono, mpeg1)
            if total is not None:
                return total * samples / rate
            first = False
        seconds += samples / rate
        position += length
    return seconds


def _skip_id3(data: bytes) -> int:
    if data[:3] != b"ID3" or len(data) < 10:
        return 0
    size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
    footer = 10 if data[5] & 0x10 else 0
    return 10 + size + footer


def _frame(data: bytes, position: int):
    header = int.from_bytes(data[position : position + 4], "big")
    if (header >> 21) & 0x7FF != 0x7FF:
        return None
    version = (header >> 19) & 3  # 3: MPEG-1, 2: MPEG-2, 0: MPEG-2.5
    layer = (header >> 17) & 3  # 1: Layer III
    bitrate_index = (header >> 12) & 0xF
    rate_index = (header >> 10) & 3
    if version == 1 or layer != 1 or bitrate_index in (0, 15) or rate_index == 3:
        return None
    mpeg1 = version == 3
    bitrate = (_MPEG1_L3_KBPS if mpeg1 else _MPEG2_L3_KBPS)[bitrate_index] * 1000
    rate = _SAMPLE_RATES[version][rate_index]
    padding = (header >> 9) & 1
    mono = (header >> 6) & 3 == 3
    length = (144 if mpeg1 else 72) * bitrate // rate + padding
    return length, (1152 if mpeg1 else 576), rate, mono, mpeg1


def _xing_frames(data: bytes, position: int, mono: bool, mpeg1: bool) -> int | None:
    """Frame count from a Xing/Info header in the first frame, if present."""
    offset = position + 4 + ((17 if mono else 32) if mpeg1 else (9 if mono else 17))
    tag = data[offset : offset + 4]
    if tag not in (b"Xing", b"Info"):
        return None
    flags = int.from_bytes(data[offset + 4 : offset + 8], "big")
    if not flags & 1:
        return None
    return int.from_bytes(data[offset + 8 : offset + 12], "big")

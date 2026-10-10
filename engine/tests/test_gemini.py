"""Gemini narration: the request, WAV kept as trimmed MP3, prices, and how its errors are sorted."""

import base64
import io
import json
import math
import struct
import wave
from datetime import date

import httpx
import pytest

from imagine_reader.providers import (
    ClipChecker,
    GeminiSentenceTimer,
    GeminiSpeechProvider,
    ProviderAccountError,
    ProviderBusy,
    ProviderQuotaExhausted,
    ProviderRefused,
    checking,
    misplaced_breaks,
    mp3_duration,
)

RATE = 24000


def _wav(sound_seconds: float, quiet_seconds: float) -> bytes:
    quiet = [0] * int(quiet_seconds * RATE)
    sound = [int(8000 * math.sin(i / 9)) for i in range(int(sound_seconds * RATE))]
    samples = quiet + sound + quiet
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buffer.getvalue()


def _provider(handler) -> GeminiSpeechProvider:
    client = httpx.Client(
        base_url="https://generativelanguage.googleapis.com/v1beta", transport=httpx.MockTransport(handler)
    )
    return GeminiSpeechProvider("g-test", client=client)


def _answer(audio: bytes) -> httpx.Response:
    data = base64.b64encode(audio).decode()
    return httpx.Response(200, json={"steps": [{"type": "model_output", "content": [{"type": "audio", "data": data}]}]})


def test_a_sentence_is_read_in_one_steady_style_and_kept_as_mp3():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _answer(_wav(2.0, 0.5))

    speech = _provider(handler).synthesize("পার্ব্বতী হাততালি দিয়া মাটিতে লুটাইয়া পড়িল।", "Charon", "story")
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1beta/interactions" and body["model"] == "gemini-3.8-flash-lite-tts"
    part = body["input"][0]["content"][0]
    assert part["text"].startswith("পার্ব্বতী")  # the words only, no directions in the transcript
    assert part["annotations"] == [{"type": "speech_metadata", "style": "a warm native Bengali storyteller, unhurried"}]
    assert body["generation_config"]["speech_config"] == [{"voice": "Charon"}]
    assert speech.media_type == "audio/mpeg" and speech.audio[:2] in (b"\xff\xf3", b"\xff\xf2", b"ID")
    assert speech.duration_seconds == pytest.approx(2.2, abs=0.01)  # the silence at each end is cut to a margin
    assert mp3_duration(speech.audio) == pytest.approx(speech.duration_seconds, abs=0.1)


def test_gemini_errors_are_sorted_like_openais():
    def answer(status, body):
        return _provider(lambda request: httpx.Response(status, json=body))

    per_day = {"error": {"status": "RESOURCE_EXHAUSTED", "details": [{"violations": [
        {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}  # fmt: skip
    with pytest.raises(ProviderAccountError):  # waiting does not help today
        answer(429, per_day).synthesize("আবার!", "Charon")
    # A paid tier says it in words, with when it resets.
    tier_1 = {"error": {"status": "RESOURCE_EXHAUSTED", "message": (
        "Rate limit exceeded for model gemini-3.8-flash-tts (limit: 100 requests per day on Tier 1). "
        "Please retry in 3h41m5s or upgrade your tier at https://ai.dev/rate-limit.")}}  # fmt: skip
    with pytest.raises(ProviderQuotaExhausted) as used_up:
        answer(429, tier_1).synthesize("আবার!", "Charon")
    assert used_up.value.retry_after == 3 * 3600 + 41 * 60 + 5
    per_minute_in_words = {"error": {"status": "RESOURCE_EXHAUSTED", "message": "(limit: 10 requests per minute "
        "on Tier 1). Please retry in 36s or upgrade your tier."}}  # fmt: skip
    with pytest.raises(ProviderBusy) as busy:
        answer(429, per_minute_in_words).synthesize("আবার!", "Charon")
    assert busy.value.retry_after == 36
    per_minute = {"error": {"status": "RESOURCE_EXHAUSTED", "details": [{"retryDelay": "12s"}]}}
    with pytest.raises(ProviderBusy) as busy:
        answer(429, per_minute).synthesize("আবার!", "Charon")
    assert busy.value.retry_after == 12
    with pytest.raises(ProviderAccountError):
        answer(403, {"error": {"status": "PERMISSION_DENIED"}}).synthesize("আবার!", "Charon")
    with pytest.raises(ProviderRefused):  # an answer without speech
        answer(200, {"steps": [{"type": "model_output", "content": []}]}).synthesize("আবার!", "Charon")
    with pytest.raises(ValueError):
        answer(200, {}).synthesize("আবার!", "marin")  # an OpenAI voice


def test_prices_double_in_2027():
    provider = GeminiSpeechProvider("g-test")
    text = "ক" * 120  # about ten seconds read aloud: 250 audio tokens
    this_year = provider.estimate_cost(text, date(2026, 10, 11))
    assert this_year == pytest.approx((60 * 0.50 + 250 * 6.00) / 1_000_000)
    assert provider.estimate_cost(text, date(2027, 1, 1)) == pytest.approx(2 * this_year)


class FakeTimer:
    usd_per_minute = 0.003

    def __init__(self, starts):
        self.starts, self.calls = starts, 0

    def sentence_starts(self, wav, texts):
        self.calls += 1
        return self.starts


def _run_wav(parts: list[tuple[float, float]]) -> bytes:
    """Sound and silence in turn: [(seconds of sound, seconds of silence after it)]."""
    samples: list[int] = []
    for sound, quiet in parts:
        samples += [int(8000 * math.sin(i / 9)) for i in range(int(sound * RATE))] + [0] * int(quiet * RATE)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buffer.getvalue()


def test_a_run_is_read_in_one_request_and_cut_at_the_pauses_the_timer_points_to():
    # Three sentences. The first has a long dash pause inside it (at 2.0 s), longer than the real break after
    # it (at 4.5 s); the timer hears the second sentence start at 5.1 s and the third at 9.1 s.
    audio = _run_wav([(2.0, 1.2), (1.3, 0.6), (3.5, 0.5), (3.5, 0.0)])
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _answer(audio)

    client = httpx.Client(
        base_url="https://generativelanguage.googleapis.com/v1beta", transport=httpx.MockTransport(handler)
    )
    timer = FakeTimer([5.1, 9.1])
    provider = GeminiSpeechProvider("g-test", timer=timer, client=client)
    texts = ["ক" * 10 + "—" + "খ" * 10 + "।", "গ" * 20 + "।", "ঘ" * 20 + "।"]
    clips = provider.synthesize_many(texts, "Charon", "story")
    assert len(seen) == 1 and timer.calls == 1
    assert seen[0]["input"][0]["content"][0]["text"] == "\n".join(texts)  # a sentence a line: a pause after each
    # Cut in the middle of the 0.6 s and 0.5 s pauses, not the 1.2 s one: the first clip runs through the dash
    # pause to half the real break, and the next start half way through the pause before them.
    assert [clip.duration_seconds for clip in clips] == pytest.approx([4.8, 4.05, 3.75], abs=0.06)
    assert provider.estimate_cost("ক" * 120) > GeminiSpeechProvider("g-test").estimate_cost("ক" * 120)


def test_without_word_timings_a_run_is_cut_by_the_sentences_lengths():
    audio = _run_wav([(2.0, 0.6), (2.0, 0.6), (2.0, 0.0)])
    provider = _provider(lambda request: _answer(audio))
    clips = provider.synthesize_many(["ক" * 20 + "।", "খ" * 20 + "।", "গ" * 20 + "।"], "Charon")
    assert [clip.duration_seconds for clip in clips] == pytest.approx([2.3, 2.6, 2.3], abs=0.06)


def test_an_unusable_timing_falls_back_to_the_sentences_lengths():
    audio = _run_wav([(2.0, 0.6), (2.0, 0.6), (2.0, 0.0)])
    texts = ["ক" * 20 + "।", "খ" * 20 + "।", "গ" * 20 + "।"]
    by_length = _provider(lambda request: _answer(audio)).synthesize_many(texts, "Charon")
    for starts in ([9.0, 1.0], [1.0], [1.0, 99.0]):  # out of order, too few, past the end
        client = httpx.Client(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            transport=httpx.MockTransport(lambda request: _answer(audio)),
        )
        clips = GeminiSpeechProvider("g-test", timer=FakeTimer(starts), client=client).synthesize_many(texts, "Charon")
        assert [c.duration_seconds for c in clips] == [c.duration_seconds for c in by_length]


def test_the_timer_asks_gemini_when_each_sentence_starts():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        answer = {"starts": [{"sentence": 3, "seconds": 9.1}, {"sentence": 2, "seconds": 5.1}]}
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}}]})

    client = httpx.Client(
        base_url="https://generativelanguage.googleapis.com/v1beta", transport=httpx.MockTransport(handler)
    )
    timer = GeminiSentenceTimer("g-test", client=client)
    assert timer.sentence_starts(b"RIFF", ["one", "two", "three"]) == [5.1, 9.1]
    parts = seen[0]["contents"][0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "audio/wav" and "1. one\n2. two\n3. three" in parts[1]["text"]


class FakeTranscriber:
    """Hears each clip as the text of the sentence given for its length (seconds of sound to text), or "?"."""

    usd_per_minute = 0.006

    def __init__(self, heard):
        self.heard, self.calls = heard, 0

    def transcribe(self, audio, media_type, language):
        self.calls += 1
        return self.heard(audio)


def test_a_run_with_too_few_pauses_is_read_again_in_halves():
    texts = ["ক" * 20 + "।", "খ" * 20 + "।", "গ" * 20 + "।", "ঘ" * 20 + "।"]
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        read = json.loads(request.content)["input"][0]["content"][0]["text"].split("\n")
        seen.append(len(read))
        if len(read) == 4:
            return _answer(_run_wav([(8.0, 0.0)]))  # four sentences without a breath: nowhere to cut
        return _answer(_run_wav([(2.0, 0.5)] * (len(read) - 1) + [(2.0, 0.0)]))

    clips = _provider(handler).synthesize_many(texts, "Charon")
    assert seen == [4, 2, 2] and len(clips) == 4  # never a cut inside a word
    assert [clip.duration_seconds for clip in clips] == pytest.approx([2.3, 2.3, 2.3, 2.3], abs=0.1)


def test_a_misplaced_cut_is_heard_and_that_stretch_read_again():
    texts = ["আমি বাড়ি যাব।", "পানি।", "আর ঠিক তখনই ট্রেন দুলে উঠল।", "চিত্রা জানালা দিয়ে তাকাল।"]
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        read = json.loads(request.content)["input"][0]["content"][0]["text"].split("\n")
        requests.append(read)
        return _answer(_run_wav([(1.0 + n, 0.5) for n in range(len(read) - 1)] + [(1.0 + len(read) - 1, 0.0)]))

    first_try = iter(range(100))

    def heard(audio: bytes) -> str:
        # In the first, four-sentence reading the second clip took the start of the third sentence; every clip of
        # a later reading is heard right.
        n = next(first_try)
        if n < 4:
            return ["আমি বাড়ি যাব।", "পানি। আর ঠিক তখনই", "ট্রেন দুলে উঠল।", "চিত্রা জানালা দিয়ে তাকাল।"][n]
        return "?"

    transcriber = FakeTranscriber(heard)
    client = httpx.Client(
        base_url="https://generativelanguage.googleapis.com/v1beta", transport=httpx.MockTransport(handler)
    )
    provider = GeminiSpeechProvider("g-test", checker=ClipChecker(transcriber, "bn", lanes=1), client=client)
    clips = provider.synthesize_many(texts, "Charon")
    assert requests == [texts, texts[1:3]]  # only the sentences around the misplaced break are read again
    assert len(clips) == 4 and provider.extra_cost() == pytest.approx(provider.estimate_cost(" ".join(texts[1:3])))
    assert provider.estimate_cost("ক" * 120) > GeminiSpeechProvider("g-test").estimate_cost("ক" * 120)  # hearing


def test_misplaced_breaks_are_found_by_lining_the_transcripts_up_with_the_text():
    texts = ["মাওলানার স্ত্রী গর্ভবতী।", "বোরকায় তাঁর বিশাল পেট ঢেকে রাখা যাচ্ছে না।", "বড় বড় নিঃশ্বাস নিচ্ছেন।"]
    right = ["মাওলানার স্ত্রী গর্ভবতী", "বোরকায় তার বিশাল পেট ঢেকে রাখা যাচ্ছে না", "বড় বড় নিশ্বাস নিচ্ছেন"]
    assert misplaced_breaks(texts, right) == []  # a recogniser's spelling does not move a break
    late = ["মাওলানার স্ত্রী গর্ভবতী। বোরকায় তার", "বিশাল পেট ঢেকে রাখা যাচ্ছে না", "বড় বড় নিশ্বাস নিচ্ছেন"]
    assert misplaced_breaks(texts, late) == [0]
    assert misplaced_breaks(texts, [right[0], "Borkay tar bishal pet", right[2]]) == []  # another script: unknown


def test_a_book_in_any_language_has_a_style_without_a_blank_where_its_language_would_be():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _answer(_wav(1.0, 0.2))

    client = httpx.Client(
        base_url="https://generativelanguage.googleapis.com/v1beta", transport=httpx.MockTransport(handler)
    )
    GeminiSpeechProvider("g-test", language="", client=client).synthesize("Bonjour.", "Charon", "story")
    style = seen[0]["input"][0]["content"][0]["annotations"][0]["style"]
    assert style == "a warm native storyteller, unhurried"


def test_clips_that_cannot_be_heard_are_not_kept_unchecked():
    texts = ["আমি বাড়ি যাব।", "পানি।"]

    def handler(request: httpx.Request) -> httpx.Response:
        return _answer(_run_wav([(1.0, 0.5), (1.0, 0.0)]))

    def no_credit(audio: bytes) -> str:
        raise ProviderAccountError("429 credit_balance_exhausted")

    client = httpx.Client(
        base_url="https://generativelanguage.googleapis.com/v1beta", transport=httpx.MockTransport(handler)
    )
    provider = GeminiSpeechProvider("g-test", checker=ClipChecker(FakeTranscriber(no_credit), "bn"), client=client)
    with pytest.raises(ProviderAccountError):  # the run waits for the account, rather than kept unheard
        provider.synthesize_many(texts, "Charon")
    requests = []
    client = httpx.Client(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        transport=httpx.MockTransport(lambda request: requests.append(request) or handler(request)),
    )
    again = GeminiSpeechProvider("g-test", checker=ClipChecker(FakeTranscriber(no_credit), "bn"), client=client)
    with pytest.raises(ProviderAccountError):  # for an hour, no run is read only to be thrown away
        again.synthesize_many(texts, "Charon")
    assert requests == []
    checking._unavailable_until = 0.0


def test_an_openai_account_without_credit_is_an_account_problem_not_a_busy_one():
    from imagine_reader.providers import OpenAITranscriber

    answer = {"error": {"code": "credit_balance_exhausted", "message": "Your credit balance is too low."}}
    client = httpx.Client(
        base_url="https://api.openai.com/v1",
        transport=httpx.MockTransport(lambda request: httpx.Response(429, json=answer)),
    )
    with pytest.raises(ProviderAccountError):
        OpenAITranscriber("sk-test", client=client).transcribe(b"ID3", "audio/mpeg", "bn")


def test_a_declined_run_is_read_in_parts_and_only_the_declined_sentence_is_left_out():
    garbled = "চ1010] ০৮৩৮০1৮৩ ঢ1000103177% 10001%%10"  # text garbled in the book itself
    texts = ["চিত্রা রিডার্স ডাইজেস্ট খুলল।", "পট ভর্তি চা।", garbled, "কেন এ রকম মনে হয় কে জানে।"]
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        read = json.loads(request.content)["input"][0]["content"][0]["text"].split("\n")
        asked.append(len(read))
        if garbled in read:
            message = "Request blocked for an unspecified policy reason. Please modify your input and retry."
            return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT", "message": message}})
        return _answer(_run_wav([(1.0, 0.5)] * (len(read) - 1) + [(1.0, 0.0)]))

    clips = _provider(handler).synthesize_many(texts, "Charon")
    assert [clip is None for clip in clips] == [False, False, True, False]
    assert asked == [4, 2, 2, 1, 1]  # the run, its halves, then the half with the garbled sentence in ones
    with pytest.raises(ProviderRefused):
        _provider(handler).synthesize(garbled, "Charon")

"""The narrator: Gemini's storyteller (Charon) reads every book, through OpenRouter or with a Gemini key; OpenAI's
narrator only when chosen; on a day Gemini's requests are used up, the reading waits for them, never another voice."""

from pathlib import Path

import pytest
from conftest import upload
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from imagine_reader.providers import (
    GeminiSpeechProvider,
    OpenAISpeechProvider,
    OpenRouterSpeechProvider,
    OpenRouterTranscriber,
    ProviderQuotaExhausted,
    Speech,
)
from imagine_reader_server import narration, reading
from imagine_reader_server.api import create_app
from imagine_reader_server.config import Settings
from imagine_reader_server.db import Book
from imagine_reader_server.jobs import process_next

DEVDAS = Path(__file__).resolve().parents[1] / "src" / "imagine_reader_server" / "samples" / "devdas.pdf"
MP3_FRAME = bytes([0xFF, 0xF3, 0x84, 0xC0]) + bytes(188)  # 24 ms of MPEG-2 Layer III


@pytest.fixture
def voices(monkeypatch):
    """Simulated narrators: records which one read each sentence."""
    heard = {"gemini": [], "openai": [], "runs": []}

    def recorder(name):
        def synthesize(self, text, voice, style="plain"):
            heard[name].append((voice, style))
            return Speech(MP3_FRAME * 40, "audio/mpeg", 0.96)

        return synthesize

    def synthesize_many(self, texts, voice, style="plain"):
        heard["runs"].append(len(texts))
        heard.setdefault("texts", []).extend(texts)
        heard["gemini"].extend((voice, style) for _ in texts)
        return [Speech(MP3_FRAME * 40, "audio/mpeg", 0.96) for _ in texts]

    monkeypatch.setattr(GeminiSpeechProvider, "synthesize", recorder("gemini"))
    monkeypatch.setattr(GeminiSpeechProvider, "synthesize_many", synthesize_many)
    monkeypatch.setattr(OpenAISpeechProvider, "synthesize", recorder("openai"))
    return heard


def ready(client, app, settings, name, data=None) -> str:
    book_id = upload(client, name, data).json()["id"]
    while process_next(app.state.engine, settings):
        pass
    assert client.get(f"/api/books/{book_id}").json()["status"] == "ready"
    return book_id


def narrate(client, app, settings, book_id, section):
    client.post(f"/api/books/{book_id}/narration", json={"section_id": section})
    while process_next(app.state.engine, settings):
        pass
    return client.get(f"/api/books/{book_id}/narration", params={"section_id": section}).json()


def test_one_openrouter_key_reads_bangla_and_english_in_geminis_storyteller_voice(tmp_path, voices):
    settings = Settings(
        _env_file=None, data_dir=tmp_path / "data", prepare_on_upload=False, openrouter_api_key="or-test"
    )  # fmt: skip
    app = create_app(settings)
    client = TestClient(app)
    assert client.get("/api/info").json()["services"] == {
        "planning": "openrouter",
        "voice": "openrouter",
        "pictures": "openrouter",
    }
    provider = narration.speech_provider(settings, "bn")
    assert isinstance(provider, OpenRouterSpeechProvider) and isinstance(
        provider.checker.transcriber, OpenRouterTranscriber
    )

    devdas = ready(client, app, settings, "devdas.pdf", DEVDAS.read_bytes())
    assert reading.load(settings, devdas)["language"] == "bn"
    manifest = narrate(client, app, settings, devdas, "sec-002")
    assert manifest["provider"] == "gemini-gemini-3.8-flash-lite-tts-3" and manifest["voice"] == "Charon"
    assert manifest["complete"] and voices["gemini"] and not voices["openai"]
    segments = manifest["segments"]
    assert len(voices["gemini"]) == len(segments)  # every sentence read once
    assert max(voices["runs"]) > 1 and sum(voices["runs"]) < len(segments)  # several at a time
    assert sum(segment["pause_after"] == 0 for segment in segments) > len(segments) // 3  # Gemini's own pauses

    story = ready(client, app, settings, "two-column.pdf")
    manifest = narrate(client, app, settings, story, "sec-001")
    assert manifest["provider"] == "gemini-gemini-3.8-flash-tts-3" and manifest["voice"] == "Charon"


def test_a_gemini_key_reads_with_googles_own_service(tmp_path, voices):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", prepare_on_upload=False, gemini_api_key="g")
    provider = narration.speech_provider(settings, "en")
    assert type(provider) is GeminiSpeechProvider and provider.checker is None  # no recogniser without a key for one
    app = create_app(settings)
    client = TestClient(app)
    assert client.get("/api/info").json()["services"]["voice"] == "gemini"
    story = ready(client, app, settings, "two-column.pdf")
    assert narrate(client, app, settings, story, "sec-001")["voice"] == "Charon"


def test_openais_narrator_only_when_chosen_or_its_key_is_the_only_one(tmp_path, voices):
    only = Settings(_env_file=None, data_dir=tmp_path / "a", openai_api_key="sk-test")
    assert isinstance(narration.speech_provider(only, "bn"), OpenAISpeechProvider)
    chosen = Settings(
        _env_file=None, data_dir=tmp_path / "b", openrouter_api_key="or", openai_api_key="sk", speech_provider="openai"
    )  # fmt: skip
    assert isinstance(narration.speech_provider(chosen, "bn"), OpenAISpeechProvider)
    assert narration.speech_provider(Settings(_env_file=None, data_dir=tmp_path / "c")).id.startswith("mock")


def test_when_geminis_requests_are_used_up_the_book_waits_for_them_never_another_voice(tmp_path, monkeypatch, voices):
    def used_up(self, texts, voice, style="plain"):
        voices["runs"].append(len(texts))
        raise ProviderQuotaExhausted("429 RESOURCE_EXHAUSTED daily quota", 3 * 3600)

    monkeypatch.setattr(GeminiSpeechProvider, "synthesize_many", used_up)
    monkeypatch.setattr(
        GeminiSpeechProvider, "synthesize", lambda self, text, voice, style="plain": used_up(self, [text], voice)
    )
    settings = Settings(
        _env_file=None, data_dir=tmp_path / "data", prepare_on_upload=False, openai_api_key="sk", gemini_api_key="g"
    )  # fmt: skip
    app = create_app(settings)
    client = TestClient(app)
    story = ready(client, app, settings, "two-column.pdf")
    manifest = narrate(client, app, settings, story, "sec-001")  # Gemini says no: the chapter waits for it
    assert len(voices["runs"]) <= settings.speech_lanes  # the lanes already asking, then not again today
    assert manifest["voice"] == "Charon" and manifest["error_code"] == "voice_quota" and not voices["openai"]
    assert {s["error_code"] for s in manifest["segments"]} == {"voice_quota"}
    assert narration.quota_used_up(settings, "gemini-3.8-flash-tts")
    assert not narration.quota_used_up(settings, "gemini-3.8-flash-lite-tts")  # Bangla's model still has some
    with Session(app.state.engine) as db:
        assert narration.voice_waits_today(settings, db.get(Book, story))


def test_books_in_any_language_are_read_by_gemini(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, openrouter_api_key="or-test")
    other = narration.speech_provider(settings, "")  # neither Bangla nor English
    assert isinstance(other, GeminiSpeechProvider) and other.language == "" and other.model == "gemini-3.8-flash-tts"

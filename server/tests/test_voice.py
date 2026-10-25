"""The narrator: Gemini's storyteller (Charon) reads every book, through OpenRouter; without a key, placeholders."""

from pathlib import Path

import pytest
from conftest import upload
from fastapi.testclient import TestClient

from imagine_reader.providers import GeminiSpeechProvider, OpenRouterSpeechProvider, OpenRouterTranscriber, Speech
from imagine_reader_server import narration, reading
from imagine_reader_server.api import create_app
from imagine_reader_server.config import Settings
from imagine_reader_server.jobs import process_next

DEVDAS = Path(__file__).resolve().parents[1] / "src" / "imagine_reader_server" / "samples" / "devdas.pdf"
MP3_FRAME = bytes([0xFF, 0xF3, 0x84, 0xC0]) + bytes(188)  # 24 ms of MPEG-2 Layer III


@pytest.fixture
def voices(monkeypatch):
    """A simulated narrator: records each sentence it reads, and how many it read at once."""
    heard = {"gemini": [], "runs": []}

    def synthesize(self, text, voice, style="plain"):
        heard["gemini"].append((voice, style))
        return Speech(MP3_FRAME * 40, "audio/mpeg", 0.96)

    def synthesize_many(self, texts, voice, style="plain"):
        heard["runs"].append(len(texts))
        heard.setdefault("texts", []).extend(texts)
        heard["gemini"].extend((voice, style) for _ in texts)
        return [Speech(MP3_FRAME * 40, "audio/mpeg", 0.96) for _ in texts]

    monkeypatch.setattr(GeminiSpeechProvider, "synthesize", synthesize)
    monkeypatch.setattr(GeminiSpeechProvider, "synthesize_many", synthesize_many)
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
    assert manifest["complete"] and voices["gemini"]
    segments = manifest["segments"]
    assert len(voices["gemini"]) == len(segments)  # every sentence read once
    assert max(voices["runs"]) > 1 and sum(voices["runs"]) < len(segments)  # several at a time
    assert sum(segment["pause_after"] == 0 for segment in segments) > len(segments) // 3  # Gemini's own pauses

    story = ready(client, app, settings, "two-column.pdf")
    manifest = narrate(client, app, settings, story, "sec-001")
    assert manifest["provider"] == "gemini-gemini-3.8-flash-tts-3" and manifest["voice"] == "Charon"


def test_without_a_key_new_books_get_placeholders_and_the_samples_keep_geminis_voice(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert narration.speech_provider(settings, "en").id.startswith("mock")
    samples = narration.speech_provider(settings, "bn", voice="Charon")  # recorded in the sample pack
    assert isinstance(samples, OpenRouterSpeechProvider) and samples.checker is None
    assert samples.id == "gemini-gemini-3.8-flash-lite-tts-3"


def test_books_in_any_language_are_read_by_gemini(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, openrouter_api_key="or-test")
    other = narration.speech_provider(settings, "")  # neither Bangla nor English
    assert isinstance(other, GeminiSpeechProvider) and other.language == "" and other.model == "gemini-3.8-flash-tts"

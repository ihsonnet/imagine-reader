import io
import json
import wave

import pytest
from conftest import upload
from fastapi.testclient import TestClient

from imagine_reader.narration import PAUSES
from imagine_reader.providers import MockSpeechProvider
from imagine_reader_server import storage
from imagine_reader_server.api import create_app
from imagine_reader_server.config import Settings
from imagine_reader_server.jobs import process_next


@pytest.fixture
def calls(monkeypatch):
    """Count (and optionally fail) synthesis calls to the placeholder narrator."""
    record = {"texts": [], "fail": set()}
    real = MockSpeechProvider.synthesize

    def synthesize(self, text, voice, style="plain"):
        record["texts"].append(text)
        if text in record["fail"]:
            raise RuntimeError("provider down")
        return real(self, text, voice, style)

    monkeypatch.setattr(MockSpeechProvider, "synthesize", synthesize)
    return record


def ready_book(client, app, settings, name="two-column.pdf") -> str:
    book_id = upload(client, name).json()["id"]
    process_next(app.state.engine, settings)
    return book_id


def narrate(client, app, settings, book_id, section="sec-001"):
    response = client.post(f"/api/books/{book_id}/narration", json={"section_id": section})
    assert response.status_code == 202
    while process_next(app.state.engine, settings):
        pass
    return client.get(f"/api/books/{book_id}/narration", params={"section_id": section}).json()


def test_narration_is_prepared_on_request_and_played_from_the_manifest(client, app, settings, calls):
    book_id = ready_book(client, app, settings)
    first = client.post(f"/api/books/{book_id}/narration", json={"section_id": "sec-001"}).json()
    assert first["complete"] is False and {s["status"] for s in first["segments"]} == {"pending"}
    assert calls["texts"] == []  # nothing is synthesized until the worker runs

    while process_next(app.state.engine, settings):
        pass
    manifest = client.get(f"/api/books/{book_id}/narration", params={"section_id": "sec-001"}).json()
    segments = manifest["segments"]
    assert manifest["complete"] and manifest["provider"] == "mock-1" and manifest["voice"] == "placeholder"
    assert [s["order"] for s in segments] == list(range(len(segments)))
    assert all(s["status"] == "ready" and s["duration"] > 0 for s in segments)
    assert segments[0]["pause_after"] == PAUSES["story"][2]  # after the title of a story
    assert manifest["characters_used"] == sum(len(text) for text in calls["texts"])

    audio = client.get(segments[1]["audio_url"])
    assert audio.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(audio.content)) as clip:
        assert clip.getnframes() / clip.getframerate() == pytest.approx(segments[1]["duration"])


def test_playing_again_reuses_every_clip(client, app, settings, calls):
    book_id = ready_book(client, app, settings)
    before = narrate(client, app, settings, book_id)
    synthesized = len(calls["texts"])
    after = narrate(client, app, settings, book_id)
    assert len(calls["texts"]) == synthesized
    assert after["characters_used"] == before["characters_used"]


def test_failed_clips_are_retried_without_repeating_successful_ones(client, app, settings, calls):
    book_id = ready_book(client, app, settings)
    calls["fail"].add("But the light was not lit.")
    manifest = narrate(client, app, settings, book_id)
    failed = [s for s in manifest["segments"] if s["status"] == "failed"]
    assert [s["error_code"] for s in failed] == ["provider_error"]
    assert calls["texts"].count("But the light was not lit.") == settings.max_clip_attempts
    assert client.get(f"/api/books/{book_id}").json()["status"] == "ready"  # narration never fails the book

    calls["fail"].clear()
    calls["texts"].clear()
    manifest = narrate(client, app, settings, book_id)
    assert calls["texts"] == ["But the light was not lit."]
    assert manifest["complete"] and all(s["status"] == "ready" for s in manifest["segments"])


def test_preparation_stops_at_the_character_budget(tmp_path, calls):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", narration_character_budget=300)
    app = create_app(settings)
    client = TestClient(app)
    book_id = ready_book(client, app, settings)
    manifest = narrate(client, app, settings, book_id)
    statuses = [s["status"] for s in manifest["segments"]]
    assert "ready" in statuses and statuses[-1] == "skipped"
    assert {s["error_code"] for s in manifest["segments"] if s["status"] == "skipped"} == {"budget_exceeded"}
    assert manifest["characters_used"] <= manifest["character_budget"] == 300


def test_cancelled_narration_does_not_run(client, app, settings, calls):
    book_id = ready_book(client, app, settings)
    client.post(f"/api/books/{book_id}/narration", json={"section_id": "sec-001"})
    assert client.post(f"/api/books/{book_id}/cancel").json() == {"cancelled": 1}
    assert not process_next(app.state.engine, settings)
    assert calls["texts"] == []


def test_unknown_sections_and_voices_are_rejected(client, app, settings):
    book_id = ready_book(client, app, settings)
    assert client.post(f"/api/books/{book_id}/narration", json={"section_id": "sec-999"}).status_code == 404
    response = client.post(f"/api/books/{book_id}/narration", json={"section_id": "sec-001", "voice": "x"})
    assert response.status_code == 422


def test_a_sentence_read_again_differently_is_recorded_again(client, app, settings, calls):
    """A book read again (a PDF-reading fix, say) keeps its clips, except for sentences whose text changed."""
    book_id = ready_book(client, app, settings)
    narrate(client, app, settings, book_id)
    path = storage.book_dir(settings, book_id) / storage.READING
    view = json.loads(path.read_text(encoding="utf-8"))
    first = next(s for s in view["sentences"] if s["text"] == "But the light was not lit.")
    first["text"] = "But the lamp was not lit."
    path.write_text(json.dumps(view), encoding="utf-8")
    calls["texts"].clear()
    manifest = narrate(client, app, settings, book_id)
    assert calls["texts"] == ["But the lamp was not lit."]
    assert manifest["complete"] and all(s["status"] == "ready" for s in manifest["segments"])

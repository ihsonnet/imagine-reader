import io
import json

import pytest
from conftest import upload
from fastapi.testclient import TestClient
from PIL import Image

from imagine_reader.providers import MockImageProvider
from imagine_reader.story import HeuristicStoryBrain
from imagine_reader_server import storage, twins
from imagine_reader_server.api import create_app
from imagine_reader_server.config import Settings
from imagine_reader_server.jobs import process_next


@pytest.fixture
def drawn(monkeypatch):
    """Record (and optionally fail) placeholder renders by caption."""
    record = {"captions": [], "fail": set()}
    real = MockImageProvider.render

    def render(self, spec, references=(), size=None):
        record["captions"].append(spec.caption)
        if spec.caption in record["fail"]:
            raise RuntimeError("image provider down")
        return real(self, spec, references, size)

    monkeypatch.setattr(MockImageProvider, "render", render)
    return record


def ready_book(client, app, settings) -> str:
    book_id = upload(client).json()["id"]
    process_next(app.state.engine, settings)
    return book_id


def sketch(client, app, settings, book_id, section="sec-001"):
    response = client.post(f"/api/books/{book_id}/sketches", json={"section_id": section})
    assert response.status_code == 202
    while process_next(app.state.engine, settings):
        pass
    return client.get(f"/api/books/{book_id}/sketches", params={"section_id": section}).json()


def test_sketches_are_planned_and_drawn_on_request(client, app, settings, drawn):
    book_id = ready_book(client, app, settings)
    first = client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"}).json()
    assert first["status"] == "planning" and first["scenes"] == []

    while process_next(app.state.engine, settings):
        pass
    manifest = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()
    scenes = manifest["scenes"]
    assert manifest["status"] == "complete" and manifest["provider"] == "mock-sketch-2"
    assert manifest["brain"] == "heuristic-4-w25"
    assert scenes[0]["first_sentence"] == 0 and scenes[-1]["last_sentence"] == 54
    assert all(b["first_sentence"] == a["last_sentence"] + 1 for a, b in zip(scenes, scenes[1:], strict=False))
    assert all(scene["status"] == "ready" for scene in scenes)
    assert manifest["sketches_used"] == len(scenes) == len(drawn["captions"])

    image = client.get(scenes[0]["image_url"])
    assert image.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(image.content)).size == (768, 512)
    bible = json.loads((storage.book_dir(settings, book_id) / storage.STORY).read_text())
    assert {c["name"] for c in bible["characters"]} == {"Mr. Hale", "Nora"}


def test_drawn_sketches_are_reused(client, app, settings, drawn):
    book_id = ready_book(client, app, settings)
    sketch(client, app, settings, book_id)
    count = len(drawn["captions"])
    again = sketch(client, app, settings, book_id)
    assert len(drawn["captions"]) == count
    assert again["sketches_used"] == count


def test_failed_sketches_are_retried_without_redrawing_the_rest(client, app, settings, drawn, monkeypatch):
    monkeypatch.setattr(twins, "share_from_twins", lambda *args: 0)  # the second copy draws its own pictures
    book_id = ready_book(client, app, settings)
    calls_before = sketch(client, app, settings, book_id)  # learn the captions first
    target = calls_before["scenes"][2]["caption"]

    # A second book with the same text plans the same scenes; fail one of its pictures.
    other = ready_book(client, app, settings)
    drawn["fail"].add(target)
    drawn["captions"].clear()
    manifest = sketch(client, app, settings, other)
    assert [s["status"] for s in manifest["scenes"]].count("failed") == 1
    assert drawn["captions"].count(target) == settings.max_clip_attempts
    assert client.get(f"/api/books/{other}").json()["status"] == "ready"  # sketches never fail the book

    drawn["fail"].clear()
    drawn["captions"].clear()
    manifest = sketch(client, app, settings, other)
    assert drawn["captions"] == [target]
    assert all(s["status"] == "ready" for s in manifest["scenes"])


def test_drawing_stops_at_the_picture_budget(tmp_path, drawn):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", sketch_budget=2)
    app = create_app(settings)
    client = TestClient(app)
    book_id = ready_book(client, app, settings)
    manifest = sketch(client, app, settings, book_id)
    statuses = [scene["status"] for scene in manifest["scenes"]]
    assert statuses[:2] == ["ready", "ready"] and set(statuses[2:]) == {"skipped"}
    assert {s["error_code"] for s in manifest["scenes"][2:]} == {"budget_exceeded"}
    assert manifest["sketches_used"] == 2


def test_a_scene_plan_that_does_not_fit_is_rejected(client, app, settings, monkeypatch):
    book_id = ready_book(client, app, settings)
    real = HeuristicStoryBrain.plan_scenes
    monkeypatch.setattr(HeuristicStoryBrain, "plan_scenes", lambda self, section, bible: real(self, section, bible)[1:])
    manifest = sketch(client, app, settings, book_id)
    assert (manifest["status"], manifest["error_code"], manifest["scenes"]) == ("failed", "invalid_scene_plan", [])


def test_cancel_stops_narration_and_sketches(client, app, settings, drawn):
    book_id = ready_book(client, app, settings)
    client.post(f"/api/books/{book_id}/narration", json={"section_id": "sec-001"})
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})
    assert client.post(f"/api/books/{book_id}/cancel").json() == {"cancelled": 2}
    assert not process_next(app.state.engine, settings)
    assert drawn["captions"] == []


def test_unknown_sections_are_rejected(client, app, settings):
    book_id = ready_book(client, app, settings)
    assert client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-999"}).status_code == 404

"""The same PDF added again reuses the voice and pictures made for it: nothing is paid for twice."""

import os

import pytest
from conftest import upload
from fastapi.testclient import TestClient

from imagine_reader.providers import MockImageProvider, MockSpeechProvider
from imagine_reader_server import storage
from imagine_reader_server.api import create_app
from imagine_reader_server.jobs import process_next


@pytest.fixture
def made(monkeypatch):
    """Counts what the placeholder providers are asked to make."""
    counts = {"clips": 0, "pictures": 0}
    speak, draw = MockSpeechProvider.synthesize, MockImageProvider.render

    def synthesize(self, *args, **kwargs):
        counts["clips"] += 1
        return speak(self, *args, **kwargs)

    def render(self, *args, **kwargs):
        counts["pictures"] += 1
        return draw(self, *args, **kwargs)

    monkeypatch.setattr(MockSpeechProvider, "synthesize", synthesize)
    monkeypatch.setattr(MockImageProvider, "render", render)
    return counts


def run(app, settings):
    while process_next(app.state.engine, settings):
        pass


def prepare(client, app, settings, name="two-column.pdf"):
    book_id = upload(client, name).json()["id"]
    run(app, settings)
    client.post(f"/api/books/{book_id}/narration", json={"section_id": "sec-001"})
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})
    run(app, settings)
    narration = client.get(f"/api/books/{book_id}/narration", params={"section_id": "sec-001"}).json()
    pictures = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()
    return book_id, narration, pictures


def test_a_second_copy_of_a_pdf_reuses_its_voice_and_pictures(settings, made):
    app = create_app(settings)
    first_reader, second_reader = TestClient(app), TestClient(app)  # two visitors, two sessions

    first, narration, pictures = prepare(first_reader, app, settings)
    paid = dict(made)
    assert narration["complete"] and pictures["status"] == "complete" and paid["clips"] and paid["pictures"]

    second, narration_again, pictures_again = prepare(second_reader, app, settings)
    assert second != first and made == paid  # nothing made again
    assert narration_again["complete"] and pictures_again["status"] == "complete"
    assert [s["first_sentence"] for s in pictures_again["scenes"]] == [s["first_sentence"] for s in pictures["scenes"]]
    images = storage.book_dir(settings, second) / storage.IMAGES
    original = storage.book_dir(settings, first) / storage.IMAGES
    some = next(images.iterdir())
    assert os.stat(some).st_ino == os.stat(original / some.name).st_ino  # linked, not copied

    assert first_reader.delete(f"/api/books/{first}").status_code == 204  # the first visitor deletes theirs
    url = pictures_again["scenes"][0]["image_url"]
    assert second_reader.get(url).status_code == 200  # the second keeps every picture
    assert first_reader.get(url.replace(second, first)).status_code == 404


def test_another_pdf_shares_nothing(settings, made):
    app = create_app(settings)
    prepare(TestClient(app), app, settings)
    paid = dict(made)
    prepare(TestClient(app), app, settings, "single-column.pdf")
    assert made["clips"] > paid["clips"] and made["pictures"] > paid["pictures"]


def test_a_copy_read_into_other_text_shares_nothing(settings, made):
    """An older copy read with an older reader (its letters out of order, say) keeps what it made to itself."""
    app = create_app(settings)
    first, _, _ = prepare(TestClient(app), app, settings)
    document = storage.book_dir(settings, first) / storage.DOCUMENT
    document.write_text(document.read_text(encoding="utf-8").replace("the", "teh"), encoding="utf-8")
    paid = dict(made)
    prepare(TestClient(app), app, settings)
    assert made["clips"] > paid["clips"] and made["pictures"] > paid["pictures"]

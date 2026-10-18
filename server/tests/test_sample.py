"""The samples: read from the PDFs that come with the reader, their voice and pictures from the sample pack, kept,
and not yours to change."""

import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

from conftest import upload
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from imagine_reader.providers import GeminiSpeechProvider, MockImageProvider, MockSpeechProvider
from imagine_reader_server import sample_pack
from imagine_reader_server.api import create_app
from imagine_reader_server.config import Settings
from imagine_reader_server.db import Job
from imagine_reader_server.jobs import process_next
from imagine_reader_server.sample import SAMPLE_BOOK_ID, SAMPLES, resume_samples
from imagine_reader_server.sample_pack import MARKER, install_sample_pack

BANGLA = SAMPLES[1].id
KABULIWALA = SAMPLES[2].id
TOOL = Path(__file__).resolve().parents[2] / "tools" / "make_sample_pack.py"


def run(app, settings):
    while process_next(app.state.engine, settings):
        pass


def sample_stack(tmp_path, monkeypatch, folder="data", **options):
    """A reader with the samples, its voice and pictures simulated (Gemini's voice needs no key here)."""
    calls = {"speech": 0, "pictures": 0}
    speak, draw = MockSpeechProvider.synthesize, MockImageProvider.render

    def synthesize_many(self, texts, voice, style="plain"):
        calls["speech"] += len(texts)
        mock = MockSpeechProvider()
        return [speak(mock, text, mock.default_voice, style) for text in texts]

    def render(self, spec, references=(), size=None):
        calls["pictures"] += 1
        return draw(self, spec)

    monkeypatch.setattr(GeminiSpeechProvider, "synthesize_many", synthesize_many)
    monkeypatch.setattr(MockImageProvider, "render", render)
    settings = Settings(
        _env_file=None, data_dir=tmp_path / folder, samples=True, story_brain="heuristic", sketch_words=0, **options
    )  # fmt: skip
    app = create_app(settings)
    run(app, settings)  # the samples are read when the reader starts
    return app, settings, calls


def jobs(app) -> int:
    with Session(app.state.engine) as db:
        return db.scalar(select(func.count()).select_from(Job).where(Job.kind != "ingest"))


def test_the_samples_are_read_listed_after_your_books_and_cannot_be_changed(tmp_path, monkeypatch):
    app, settings, calls = sample_stack(tmp_path, monkeypatch, sample_pack_url="")
    reader = TestClient(app)
    assert reader.get("/api/info").json()["sample_book_id"] == SAMPLE_BOOK_ID
    books = reader.get("/api/books").json()
    assert [book["id"] for book in books] == [SAMPLE_BOOK_ID, BANGLA, KABULIWALA]
    sample = books[0]
    assert sample["shared"] and not sample["mine"] and sample["status"] == "ready"
    assert "?v=" in sample["cover_url"] and "?v=" not in books[1]["cover_url"]  # its own cover; Devdas: page 1
    cover = reader.get(sample["cover_url"])
    assert cover.status_code == 200 and Image.open(io.BytesIO(cover.content)).size == (540, 756)
    assert jobs(app) == 0 and calls == {"speech": 0, "pictures": 0}  # nothing made on reading them
    again = create_app(settings)  # a restart
    assert not process_next(again.state.engine, settings)  # nothing to read again
    assert TestClient(again).get(f"/api/books/{SAMPLE_BOOK_ID}").json()["status"] == "ready"

    own = upload(reader).json()["id"]
    assert [book["id"] for book in reader.get("/api/books").json()] == [own, SAMPLE_BOOK_ID, BANGLA, KABULIWALA]
    assert reader.delete(f"/api/books/{SAMPLE_BOOK_ID}").status_code == 403
    assert reader.post(f"/api/books/{SAMPLE_BOOK_ID}/cancel").status_code == 403


def test_the_bangla_samples_open_at_their_first_chapter(tmp_path, monkeypatch):
    app, settings, _ = sample_stack(tmp_path, monkeypatch, sample_pack_url="")
    reader = TestClient(app)
    info = reader.get("/api/info").json()
    assert {"id": BANGLA, "title": "দেবদাস", "language": "bn"} in info["samples"]
    book = reader.get(f"/api/books/{BANGLA}").json()
    assert book["shared"] and book["status"] == "ready" and book["start_section"] == "sec-002"
    reading = reader.get(f"/api/books/{BANGLA}/reading").json()
    assert reading["narration_style"] == "story"
    assert [s["title"] for s in reading["sections"]][1:] == ["প্রথম পরিচ্ছেদ", "দ্বিতীয় পরিচ্ছেদ"]
    story = reader.get(f"/api/books/{KABULIWALA}").json()  # a short story: its title page, then the whole story
    assert story["shared"] and story["status"] == "ready" and story["start_section"] == "sec-002"
    assert [s["title"] for s in reader.get(f"/api/books/{KABULIWALA}/reading").json()["sections"]][1:] == ["কাবুলিওয়ালা"]


def test_the_samples_come_ready_made_in_a_pack_and_nothing_is_made_for_them_meanwhile(tmp_path, monkeypatch):
    # A reader that has prepared the Lantern Keeper (here, with the text rules and simulated voice and pictures)...
    maker, made_settings, made = sample_stack(tmp_path, monkeypatch, "maker", sample_pack_url="")
    client = TestClient(maker)
    client.post(f"/api/books/{SAMPLE_BOOK_ID}/sketches", json={"section_id": "sec-001"})
    client.post(f"/api/books/{SAMPLE_BOOK_ID}/narration", json={"section_id": "sec-001"})
    run(maker, made_settings)
    assert made["speech"] == 55 and made["pictures"] > 10
    pack = tmp_path / "samples.tar.gz"
    plan_end = "-wauto/pencil"  # the text rules at the Auto pace
    result = subprocess.run(
        [sys.executable, str(TOOL), str(made_settings.data_dir), str(pack), f"--plan-end={plan_end}"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert "The Lantern Keeper (sample).pdf: " in result.stdout and pack.exists()

    # A pack whose checksum is not the one expected (by default, the published pack's) is not installed...
    monkeypatch.setattr(sample_pack, "_last_try", 0.0)
    other, other_settings, _ = sample_stack(tmp_path, monkeypatch, "other", sample_pack_url=str(pack))
    assert not install_sample_pack(other.state.engine, other_settings)
    assert not (other_settings.data_dir / MARKER).exists()

    # ...but with its own, it is what another reader installs on first start, once its samples are read.
    monkeypatch.setattr(sample_pack, "_last_try", 0.0)
    digest = hashlib.sha256(pack.read_bytes()).hexdigest()
    app, settings, calls = sample_stack(
        tmp_path, monkeypatch, "reader", sample_pack_url=str(pack), sample_pack_sha256=digest
    )
    reader = TestClient(app)
    reader.post(f"/api/books/{SAMPLE_BOOK_ID}/narration", json={"section_id": "sec-001"})
    reader.post(f"/api/books/{SAMPLE_BOOK_ID}/sketches", json={"section_id": "sec-001"})
    assert jobs(app) == 0  # while the pack is on its way, no key pays for what it brings
    assert install_sample_pack(app.state.engine, settings)
    assert json.loads((settings.data_dir / MARKER).read_text())["samples"] == ["The Lantern Keeper"]

    narration = reader.post(f"/api/books/{SAMPLE_BOOK_ID}/narration", json={"section_id": "sec-001"}).json()
    pictures = reader.post(f"/api/books/{SAMPLE_BOOK_ID}/sketches", json={"section_id": "sec-001"}).json()
    run(app, settings)
    assert narration["complete"] and narration["voice"] == "Charon" and pictures["status"] == "complete"
    assert reader.get(narration["segments"][3]["audio_url"]).status_code == 200
    assert reader.get(pictures["scenes"][0]["image_url"]).headers["content-type"].startswith("image/")
    assert calls == {"speech": 0, "pictures": 0}  # nothing was made: all of it came in the pack
    # The two Bangla samples were not in this pack: once it is installed, they are prepared like any book.
    reader.post(f"/api/books/{BANGLA}/narration", json={"section_id": "sec-002"})
    assert jobs(app) == 1


def test_a_demo_may_make_every_style_and_pace_of_the_samples_while_idle(tmp_path, monkeypatch):
    app, settings, calls = sample_stack(tmp_path, monkeypatch, sample_pack_url="", prepare_samples=True)
    reader = TestClient(app)
    upload(reader)  # one of your books waits to be read: the samples wait for it
    resume_samples(app.state.engine, settings)
    with Session(app.state.engine) as db:
        queued = db.scalars(select(Job.book_id).where(Job.state == "queued")).all()
    assert len(queued) == 1 and queued[0] not in {sample.id for sample in SAMPLES}
    run(app, settings)

    for _ in range(400):  # the worker asks about once a minute; here, until nothing is left
        resume_samples(app.state.engine, settings)
        if not process_next(app.state.engine, settings):
            break
        run(app, settings)
    for style in ("pencil", "color", "color-hd", "ghibli"):
        for words in (0, 1, 25, 60, 120):
            manifest = reader.get(
                f"/api/books/{SAMPLE_BOOK_ID}/sketches",
                params={"section_id": "sec-001", "style": style, "words": words},
            ).json()
            assert manifest["status"] == "complete", (style, words)
    made = dict(calls)
    assert (
        reader.post(f"/api/books/{SAMPLE_BOOK_ID}/sketches", json={"section_id": "sec-001", "words": 1}).status_code
        == 202
    )
    run(app, settings)
    assert calls == made  # all made before: nothing is drawn again

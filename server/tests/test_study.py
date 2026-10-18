"""The study build: its schedule, prepared passages, sessions with fixed pictures, and the interaction log."""

import csv
import hashlib
import io
import json
import shutil

import pytest
from conftest import FIXTURES, upload
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from imagine_reader.providers import MockImageProvider
from imagine_reader.providers.image import Picture
from imagine_reader_server import study
from imagine_reader_server.api import create_app
from imagine_reader_server.config import Settings
from imagine_reader_server.db import Book, make_engine
from imagine_reader_server.jobs import process_next


def tiny(self, spec):
    """A placeholder picture small enough to draw hundreds of quickly."""
    out = io.BytesIO()
    Image.new("RGB", (4, 3), (200, 200, 200)).save(out, "PNG")
    return Picture(out.getvalue(), "image/png", 4, 3)


# The schedule.


def test_the_first_block_follows_the_published_schedule():
    first = [study.condition(1, session) for session in range(1, 5)]
    last = [study.condition(4, session) for session in range(1, 5)]
    assert [(c.words, c.style, c.passage) for c in first] == [
        (1, "pencil", "A"),
        (25, "color-hd", "D"),
        (60, "animated", "B"),
        (120, "color", "C"),
    ]
    assert [(c.words, c.style, c.passage) for c in last] == [
        (120, "animated", "D"),
        (60, "color", "A"),
        (25, "pencil", "C"),
        (1, "color-hd", "B"),
    ]


def test_even_blocks_run_the_sessions_backwards():
    for participant in range(1, 5):
        for session in range(1, 5):
            odd, even = study.condition(participant, 5 - session), study.condition(participant + 4, session)
            assert (even.passage, even.style, even.words) == (odd.passage, odd.style, odd.words)
            assert (even.block, even.slot) == (2, odd.slot)


@pytest.mark.parametrize("block", [1, 2, 7])
def test_every_block_balances_position_and_pairings(block):
    participants = range(4 * block - 3, 4 * block + 1)
    for session in range(1, 5):
        same_time = [study.condition(p, session) for p in participants]
        assert {c.passage for c in same_time} == set(study.PASSAGES)
        assert {c.style for c in same_time} == set(study.STYLES)
        assert {c.words for c in same_time} == set(study.FREQUENCIES)
    cells = [study.condition(p, s) for p in participants for s in range(1, 5)]
    for one, other in (("passage", "style"), ("passage", "words"), ("style", "words")):
        assert len({(getattr(c, one), getattr(c, other)) for c in cells}) == 16  # each pairing exactly once
    for participant in participants:
        own = [study.condition(participant, s) for s in range(1, 5)]
        assert {c.passage for c in own} == set(study.PASSAGES) and {c.style for c in own} == set(study.STYLES)


def test_participants_and_sessions_are_checked():
    with pytest.raises(ValueError):
        study.condition(0, 1)
    with pytest.raises(ValueError):
        study.condition(1, 5)


def test_the_schedule_prints_for_the_experimenter(capsys):
    assert study.main(["schedule", "--participants", "8"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 9 and lines[1].startswith("P001") and lines[5].startswith("P005")
    assert "A · every sentence · pencil" in lines[1] and lines[5].split()[1:3] == ["2", "1"]


# A prepared study: passages A and B, with C and D not yet in the folder.


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    folder = tmp_path_factory.mktemp("study")
    passages = folder / "passages"
    passages.mkdir()
    shutil.copy(FIXTURES / "single-column.pdf", passages / "A.pdf")
    shutil.copy(FIXTURES / "two-column.pdf", passages / "B.pdf")
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("OPENAI_API_KEY", raising=False)  # never a real AI service, whatever the environment holds
        patch.delenv("IR_OPENAI_API_KEY", raising=False)
        patch.setattr(MockImageProvider, "render", tiny)
        settings = Settings(_env_file=None, data_dir=folder / "data", study_dir=passages, samples=False)
        app = create_app(settings)
        study.drain(app.state.engine, settings)  # read the passages
        assert study.prepare(app.state.engine, settings) == ["C", "D"]
        study.drain(app.state.engine, settings)
        yield app, settings


def test_every_frequency_and_style_of_each_passage_is_prepared(stack):
    app, settings = stack
    client = TestClient(app)
    overview = client.get("/api/study").json()
    a, b, c, d = overview["passages"]
    assert c["status"] == d["status"] == "missing"
    for passage in (a, b):
        assert passage["narration"]["complete"]
        assert len(passage["plans"]) == 16 and all(plan["complete"] for plan in passage["plans"])
    scenes = {(plan["style"], plan["words"]): plan["scenes"] for plan in a["plans"]}
    assert scenes["pencil", 1] > scenes["pencil", 25] > scenes["pencil", 60] > scenes["pencil", 120]
    practice = overview["practice"]
    assert (practice["style"], practice["words"]) == ("pencil", 25) and practice["plans"][0]["complete"]
    assert overview["build"] == "dev" and overview["pictures"].startswith("mock")

    info = client.get("/api/info").json()
    assert info["study"] and info["picture_styles"] == ["pencil", "color", "color-hd", "animated"]
    assert study.prepare(app.state.engine, settings) == ["C", "D"]
    assert not process_next(app.state.engine, settings)  # asking again finds nothing left to do


def test_a_study_session_is_fixed_by_the_schedule(stack):
    app, _ = stack
    client = TestClient(app)
    started = client.post("/api/study/sessions", json={"kind": "study", "participant": 1, "session": 1})
    assert started.status_code == 201
    session = started.json()
    assert (session["passage"], session["style"], session["words"]) == ("A", "pencil", 1)
    assert session["locked"] and session["attempt"] == 1 and session["book_id"] == study.PASSAGE_IDS["A"]
    assert client.get(f"/api/study/sessions/{session['id']}").json() == session

    again = client.post("/api/study/sessions", json={"kind": "study", "participant": 1, "session": 1}).json()
    assert again["attempt"] == 2 and again["id"] != session["id"]
    missing = client.post("/api/study/sessions", json={"kind": "study", "participant": 1, "session": 2})
    assert missing.status_code == 409  # passage D is not prepared
    assert client.post("/api/study/sessions", json={"kind": "study", "participant": 1}).status_code == 422
    practice = client.post("/api/study/sessions", json={"kind": "practice", "participant": 1}).json()
    assert (practice["passage"], practice["style"], practice["words"]) == ("practice", "pencil", 25)
    schedule = client.get("/api/study/schedule", params={"participant": 1}).json()
    third = {"session": 3, "passage": "B", "style": "animated", "words": 60, "block": 1, "slot": 1, "column": 3}
    assert schedule[2] == third
    assert client.get(f"/api/study/sessions/{'0' * 24}").status_code == 404


def test_events_are_logged_with_the_session_they_belong_to(stack):
    app, settings = stack
    client = TestClient(app)
    session = client.post("/api/study/sessions", json={"kind": "study", "participant": 2, "session": 1}).json()
    events = [
        {"type": "session_start", "narration_ms": 0, "at": "2026-10-10T09:00:00Z", "data": {"sentences": 55}},
        {"type": "picture_shown", "narration_ms": 4300, "at": "2026-10-10T09:00:05Z", "data": {"scene": "s-2"}},
    ]
    posted = client.post(f"/api/study/sessions/{session['id']}/events", json={"events": events})
    assert posted.status_code == 204
    path = settings.data_dir / "study" / "events" / "P002.jsonl"
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line["event"] for line in lines] == ["session_start", "picture_shown"]
    first = lines[0]
    assert (first["participant"], first["session"], first["passage"], first["style"], first["words"]) == (
        2,
        1,
        "B",
        "color",
        25,
    )
    assert first["build"] == "dev" and first["models"]["pictures"].startswith("mock")
    assert first["data"] == {"sentences": 55} and first["received_at"] and first["session_id"] == session["id"]
    assert lines[1]["narration_ms"] == 4300

    made_up = [{"type": "made_up", "narration_ms": 0, "at": "2026-10-10T09:00:00Z"}]
    assert client.post(f"/api/study/sessions/{session['id']}/events", json={"events": made_up}).status_code == 422
    heavy = [{"type": "pause", "narration_ms": 0, "at": "2026-10-10T09:00:00Z", "data": {"x": "y" * 3000}}]
    assert client.post(f"/api/study/sessions/{session['id']}/events", json={"events": heavy}).status_code == 422
    assert client.post(f"/api/study/sessions/{'0' * 24}/events", json={"events": []}).status_code == 404
    assert len(path.read_text().splitlines()) == 2  # nothing refused was written


def test_a_benchmark_session_may_change_its_pictures(stack):
    app, settings = stack
    client = TestClient(app)
    body = {"kind": "benchmark", "book_id": study.PRACTICE.id, "style": "color", "words": 60, "label": "ch 2, run 1"}
    run = client.post("/api/study/sessions", json=body).json()
    assert not run["locked"] and run["participant"] is None and (run["style"], run["words"]) == ("color", 60)
    change = {"type": "setting_change", "narration_ms": 9000, "at": "2026-10-10T09:00:09Z", "data": {"to": "pencil"}}
    client.post(f"/api/study/sessions/{run['id']}/events", json={"events": [change]})
    assert (settings.data_dir / "study" / "events" / "benchmark-ch-2-run-1.jsonl").exists()
    elsewhere = client.post("/api/study/sessions", json={**body, "book_id": "f" * 32})
    assert elsewhere.status_code == 404  # not a book this browser may read
    studio = client.post("/api/study/sessions", json={**body, "style": "ghibli"})
    assert studio.status_code == 422  # not offered in the study build


def test_pictures_are_exported_for_screening_and_can_be_drawn_again(stack, tmp_path):
    app, settings = stack
    engine = app.state.engine
    copied = study.export(engine, settings, tmp_path / "out")
    rows = list(csv.DictReader((tmp_path / "out" / "pictures.csv").open(encoding="utf-8")))
    assert copied == len(rows) and {row["passage"] for row in rows} == {"practice", "A", "B"}
    assert (tmp_path / "out" / "A" / "visual-bible.json").exists()
    record = json.loads((tmp_path / "out" / "study.json").read_text())
    assert [p["passage"] for p in record["passages"]] == ["practice", "A", "B"]
    row = next(r for r in rows if (r["passage"], r["style"], r["words"]) == ("A", "pencil", "120"))
    assert (tmp_path / "out" / row["file"]).exists() and row["text"] and row["prompt"].startswith("Monochrome")

    assert study.redraw(engine, settings, "A", "pencil", 120, [row["scene"]]) == 1
    with pytest.raises(ValueError):
        study.redraw(engine, settings, "A", "pencil", 120, ["no-such-scene"])
    study.drain(engine, settings)
    passage_a = TestClient(app).get("/api/study").json()["passages"][0]
    assert all(plan["complete"] for plan in passage_a["plans"])


def test_clearing_pictures_spares_the_study_books(stack):
    app, settings = stack
    engine = app.state.engine
    with pytest.raises(ValueError):
        study.clear_pictures(engine, settings, study.PASSAGE_IDS["A"])
    client = TestClient(app)
    own = upload(client).json()["id"]
    study.drain(engine, settings)
    client.post(f"/api/books/{own}/sketches", json={"section_id": "sec-001"})
    study.drain(engine, settings)
    assert study.clear_pictures(engine, settings, own) > 0
    after = client.get(f"/api/books/{own}/sketches", params={"section_id": "sec-001"}).json()
    assert after["scenes"] == []


def test_a_changed_passage_is_read_again(tmp_path):
    passages = tmp_path / "passages"
    passages.mkdir()
    shutil.copy(FIXTURES / "single-column.pdf", passages / "A.pdf")
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", study_dir=passages, samples=False)
    engine = make_engine(settings)
    assert study.install(engine, settings) == ["B", "C", "D"]
    shutil.copy(FIXTURES / "two-column.pdf", passages / "A.pdf")  # the final version replaces a draft
    study.install(engine, settings)
    with Session(engine) as db:
        book = db.get(Book, study.PASSAGE_IDS["A"])
        assert book.sha256 == hashlib.sha256((passages / "A.pdf").read_bytes()).hexdigest()
        assert book.status == "queued"


def test_the_command_line_needs_the_study_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("IR_STUDY_DIR", raising=False)
    assert study.main(["status"]) == 2
    monkeypatch.setenv("IR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("IR_STUDY_DIR", str(tmp_path / "passages"))
    assert study.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Build dev" in out and "A         missing" in out

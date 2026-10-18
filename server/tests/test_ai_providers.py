"""Paid AI providers in the server: selection, spending, refusals, and account problems.

OpenAI is simulated: the connectors' network calls are replaced, so these
tests check what the server does with each outcome.
"""

import io
import re

import pytest
from conftest import upload
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from imagine_reader.providers import (
    OpenAIImageProvider,
    OpenAISpeechProvider,
    OpenAITextModel,
    Picture,
    ProviderAccountError,
    ProviderRefused,
    Speech,
)
from imagine_reader_server import sketches
from imagine_reader_server.api import create_app
from imagine_reader_server.config import Settings
from imagine_reader_server.db import Spend
from imagine_reader_server.jobs import process_next

MP3_FRAME = bytes([0xFF, 0xF3, 0x84, 0xC0]) + bytes(188)  # 24 ms of MPEG-2 Layer III


@pytest.fixture
def openai(monkeypatch):
    """Simulated OpenAI: records requests; texts or captions in ``refuse`` / ``account`` fail that way."""
    calls = {"speech": [], "images": [], "prompts": [], "readings": 0, "refuse": set(), "account": False}
    calls.update(boards=[], references=[], drawn_with=[])
    calls["cast"] = {
        "setting": "A rocky coast in the 1900s.",
        "characters": [
            {
                "name": "Mr. Hale",
                "aliases": [],
                "role": "main",
                "gender": "male",
                "age": "old",
                "species": "human",
                "look": "Old man with a grey beard and a dark oilskin coat.",
                "look_from_text": False,
            }
        ],
        "things": [],
        "places": [],
    }

    def synthesize(self, text, voice, style="plain"):
        calls["speech"].append(text)
        if calls["account"]:
            raise ProviderAccountError("401 invalid_api_key")
        if text in calls["refuse"]:
            raise ProviderRefused("400 moderation_blocked")
        audio = MP3_FRAME * 50
        return Speech(audio, "audio/mpeg", 1.2)

    def read(self, instructions, text, schema, name):
        if name == "storyboard":  # a shot every third sentence, with Mr. Hale once he is in the cast
            calls["boards"].append(text)
            if calls.get("board_error"):
                raise calls["board_error"]
            people = ["chr-001"] if "- chr-001:" in text else []
            starts = [int(n) for n in re.findall(r"^\[(\d+)\]", text, re.MULTILINE)][::3]
            shot = {"caption": "", "characters": people, "place": None, "objects": [], "mood": "calm"}
            return {"shots": [{**shot, "start": n, "picture": f"Storyboard picture {n}."} for n in starts]}, 0.01
        calls["readings"] += 1
        if calls.get("cast_error"):
            raise calls["cast_error"]
        return calls["cast"], 0.03

    def render(self, spec, references=(), size=None):
        if size is not None:  # a reference picture
            calls["references"].append(spec.characters[0])
            buffer = io.BytesIO()
            Image.new("RGB", (64, 64), "white").save(buffer, format="WEBP")
            return Picture(buffer.getvalue(), "image/webp", 64, 64)
        calls["drawn_with"].append([reference.name for reference in references])
        calls["images"].append(spec.caption)
        calls["prompts"].append(spec.prompt)
        buffer = io.BytesIO()
        Image.new("RGB", (48, 32), "white").save(buffer, format="WEBP")
        return Picture(buffer.getvalue(), "image/webp", 48, 32)

    monkeypatch.setattr(OpenAISpeechProvider, "synthesize", synthesize)
    monkeypatch.setattr(OpenAIImageProvider, "render", render)
    monkeypatch.setattr(OpenAITextModel, "json", read)
    return calls


def stack(tmp_path, **settings):
    configured = Settings(_env_file=None, data_dir=tmp_path / "data", prepare_on_upload=False, **settings)
    app = create_app(configured)
    client = TestClient(app)
    book_id = upload(client).json()["id"]
    process_next(app.state.engine, configured)
    return app, client, configured, book_id


def run(app, settings):
    while process_next(app.state.engine, settings):
        pass


def services(tmp_path, name, **keys) -> dict:
    settings = Settings(_env_file=None, data_dir=tmp_path / name, **keys)
    return TestClient(create_app(settings)).get("/api/info").json()["services"]


def test_the_landing_page_learns_which_services_are_used(tmp_path):
    assert services(tmp_path, "none") == {"planning": "rules", "voice": "placeholders", "pictures": "placeholders"}
    assert services(tmp_path, "openai", openai_api_key="sk-test") == {
        "planning": "openai",
        "voice": "openai",
        "pictures": "openai",
    }
    assert services(tmp_path, "openrouter", openrouter_api_key="sk-or-test") == {
        "planning": "openrouter",
        "voice": "openrouter",  # Gemini's storyteller, through OpenRouter
        "pictures": "openrouter",
    }
    cheaper = services(
        tmp_path, "both", openrouter_api_key="sk-or-test", cloudflare_account_id="acct", cloudflare_api_token="tok"
    )
    assert cheaper["pictures"] == "cloudflare"
    info = TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path / "limits"))).get("/api/info").json()
    assert (info["max_upload_mb"], info["max_pages"], info["uploads"]) == (100, 1000, True)


def test_an_empty_key_means_placeholders(tmp_path):
    assert services(tmp_path, "empty", openai_api_key="", openrouter_api_key="")["voice"] == "placeholders"


def test_openai_narration_is_stored_as_mp3_and_its_cost_recorded(tmp_path, openai):
    app, client, settings, book_id = stack(tmp_path, openai_api_key="sk-test")
    manifest = client.post(f"/api/books/{book_id}/narration", json={"section_id": "sec-001"}).json()
    assert manifest["voice"] == "marin" and manifest["preparing"]
    run(app, settings)
    manifest = client.get(f"/api/books/{book_id}/narration", params={"section_id": "sec-001"}).json()
    assert manifest["complete"] and not manifest["preparing"] and manifest["error_code"] is None
    assert manifest["segments"][0]["duration"] == pytest.approx(1.2)  # measured from the MP3 itself
    audio = client.get(manifest["segments"][0]["audio_url"])
    assert audio.headers["content-type"] == "audio/mpeg"
    with Session(app.state.engine) as db:
        spent = db.scalar(select(func.sum(Spend.usd)))
    assert spent == pytest.approx(sum(len(text) for text in openai["speech"]) * 17 / 1_000_000)


def test_the_daily_spending_limit_stops_preparation_and_says_why(tmp_path, openai):
    app, client, settings, book_id = stack(tmp_path, openai_api_key="sk-test", daily_spend_limit_usd=0.002)
    client.post(f"/api/books/{book_id}/narration", json={"section_id": "sec-001"})
    run(app, settings)
    manifest = client.get(f"/api/books/{book_id}/narration", params={"section_id": "sec-001"}).json()
    statuses = [segment["status"] for segment in manifest["segments"]]
    assert "ready" in statuses and statuses[-1] == "skipped"
    assert manifest["error_code"] == "daily_limit" and not manifest["preparing"]
    with Session(app.state.engine) as db:
        assert db.scalar(select(func.sum(Spend.usd))) <= 0.002
    assert client.get(f"/api/books/{book_id}").json()["status"] == "ready"


def test_refused_text_is_not_sent_again(tmp_path, openai):
    app, client, settings, book_id = stack(tmp_path, openai_api_key="sk-test")
    openai["refuse"].add("But the light was not lit.")
    client.post(f"/api/books/{book_id}/narration", json={"section_id": "sec-001"})
    run(app, settings)
    client.post(f"/api/books/{book_id}/narration", json={"section_id": "sec-001"})  # asking again
    run(app, settings)
    assert openai["speech"].count("But the light was not lit.") == 1
    manifest = client.get(f"/api/books/{book_id}/narration", params={"section_id": "sec-001"}).json()
    refused = [s for s in manifest["segments"] if s["error_code"] == "refused"]
    assert len(refused) == 1 and refused[0]["status"] == "failed"


def test_an_account_problem_stops_work_without_using_up_retries(tmp_path, openai):
    app, client, settings, book_id = stack(tmp_path, openai_api_key="sk-test")
    openai["account"] = True
    client.post(f"/api/books/{book_id}/narration", json={"section_id": "sec-001"})
    run(app, settings)
    assert len(openai["speech"]) <= settings.speech_lanes  # stopped at the first refusals of the key
    manifest = client.get(f"/api/books/{book_id}/narration", params={"section_id": "sec-001"}).json()
    assert manifest["error_code"] == "provider_account"
    assert {s["status"] for s in manifest["segments"]} == {"pending"}

    openai["account"] = False  # the key was fixed
    client.post(f"/api/books/{book_id}/narration", json={"section_id": "sec-001"})
    run(app, settings)
    manifest = client.get(f"/api/books/{book_id}/narration", params={"section_id": "sec-001"}).json()
    assert manifest["complete"] and manifest["error_code"] is None


def test_adding_a_key_redraws_placeholder_sketches(tmp_path, openai):
    app, client, settings, book_id = stack(tmp_path)
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})
    run(app, settings)
    placeholder = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()
    assert placeholder["provider"] == "mock-sketch-2" and placeholder["status"] == "complete"

    keyed = Settings(_env_file=None, data_dir=settings.data_dir, openai_api_key="sk-test", prepare_on_upload=False)
    keyed_app = create_app(keyed)
    keyed_client = TestClient(keyed_app, cookies=client.cookies)
    keyed_client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})
    run(keyed_app, keyed)
    drawn = keyed_client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()
    assert drawn["provider"].startswith("openai-gpt-image-1-mini") and drawn["status"] == "complete"
    assert len(openai["images"]) == len(drawn["scenes"])
    image = keyed_client.get(drawn["scenes"][0]["image_url"])
    assert image.headers["content-type"] == "image/webp"


def test_pictures_drawn_side_by_side_stay_within_the_daily_limit(tmp_path, openai):
    app, client, settings, book_id = stack(
        tmp_path, openai_api_key="sk-test", daily_spend_limit_usd=0.009, story_brain="heuristic"
    )
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})
    run(app, settings)  # four lanes, $0.0038 a picture: only two fit
    manifest = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()
    assert len(openai["images"]) == 2 and manifest["error_code"] == "daily_limit"
    with Session(app.state.engine) as db:
        assert db.scalar(select(func.sum(Spend.usd))) == pytest.approx(0.0076)


def test_the_book_is_read_once_and_every_picture_draws_its_people_the_same_way(tmp_path, openai):
    app, client, settings, book_id = stack(tmp_path, openai_api_key="sk-test")
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001", "style": "color"})
    run(app, settings)
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})  # another plan, same reading
    run(app, settings)
    manifest = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()
    assert manifest["brain"] == "board-4-openai-gpt-5.4-mini-1-s2-w25" and manifest["status"] == "complete"
    assert openai["readings"] == 1 and len(openai["boards"]) == 1  # one storyboard for both styles
    with_hale = [prompt for prompt in openai["prompts"] if "Mr. Hale" in prompt]
    assert with_hale and all("Old man with a grey beard and a dark oilskin coat" in prompt for prompt in with_hale)
    with Session(app.state.engine) as db:
        assert db.scalar(select(func.sum(Spend.usd)).where(Spend.kind == "text")) == pytest.approx(0.04)


def test_pictures_wait_for_the_books_reading_never_drawn_by_the_text_rules(tmp_path, openai):
    openai["cast_error"] = RuntimeError("text model down")
    app, client, settings, book_id = stack(tmp_path, openai_api_key="sk-test")
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})
    run(app, settings)
    manifest = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()
    assert (manifest["status"], manifest["error_code"]) == ("failed", "plan_failed") and not openai["prompts"]

    openai["cast_error"] = None  # it works again: asking again reads the book, and the pictures are drawn
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})
    run(app, settings)
    manifest = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()
    assert manifest["status"] == "complete" and openai["readings"] == 2
    assert any("grey beard" in prompt for prompt in openai["prompts"])


def test_visitors_learn_when_this_sites_ai_is_used_up_and_why_a_book_stopped(tmp_path, openai):
    # Room to start (5 cents), not to finish the chapter's voice (about 6 cents) and pictures.
    app, client, settings, book_id = stack(tmp_path, openai_api_key="sk-test", daily_spend_limit_usd=0.06)
    assert client.get("/api/info").json()["ai_status"] == "ok"
    client.post(f"/api/books/{book_id}/narration", json={"section_id": "sec-001"})
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})
    run(app, settings)
    assert client.get("/api/info").json()["ai_status"] == "daily_limit"
    preparation = client.get(f"/api/books/{book_id}").json()["preparation"]
    assert preparation["stopped"] == "daily_limit" and not preparation["active"]


def test_a_rejected_key_makes_this_sites_ai_unavailable_for_a_while(tmp_path, openai):
    app, client, settings, book_id = stack(tmp_path, openai_api_key="sk-test")
    openai["account"] = True
    client.post(f"/api/books/{book_id}/narration", json={"section_id": "sec-001"})
    run(app, settings)
    assert client.get("/api/info").json()["ai_status"] == "unavailable"
    assert client.get(f"/api/books/{book_id}").json()["preparation"]["stopped"] == "provider_account"


def test_each_chapter_is_planned_as_a_storyboard_and_its_people_drawn_from_one_reference(tmp_path, openai):
    app, client, settings, book_id = stack(tmp_path, openai_api_key="sk-test")
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})
    run(app, settings)
    manifest = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()
    scenes = manifest["scenes"]
    assert manifest["status"] == "complete" and len(openai["boards"]) == 1
    assert [scene["first_sentence"] for scene in scenes] == list(range(0, 55, 3))  # where the storyboard cut
    assert scenes[1]["caption"] == "Storyboard picture 3."  # no caption given: the picture's description
    assert all("Scene: Storyboard picture" in prompt for prompt in openai["prompts"])
    assert openai["references"] == ["Mr. Hale"]  # drawn once, though four lanes needed him at once
    assert all(names == ["Mr. Hale"] for names in openai["drawn_with"])  # sent with every picture of him

    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001", "style": "color"})
    run(app, settings)
    assert len(openai["boards"]) == 1  # the same storyboard in another style
    assert openai["references"] == ["Mr. Hale", "Mr. Hale"]  # and a reference in that style


def test_a_storyboard_waits_for_the_daily_limit(tmp_path, openai):
    app, client, settings, book_id = stack(tmp_path, openai_api_key="sk-test", daily_spend_limit_usd=0.02)
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})
    run(app, settings)  # reading the book used the day's money
    manifest = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()
    assert manifest["status"] == "failed" and manifest["error_code"] == "daily_limit" and manifest["scenes"] == []
    assert openai["boards"] == [] and openai["images"] == []


def test_a_failed_storyboard_waits_and_is_planned_when_asked_again(tmp_path, openai):
    openai["board_error"] = RuntimeError("text model down")
    app, client, settings, book_id = stack(tmp_path, openai_api_key="sk-test")
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})
    run(app, settings)
    manifest = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()
    assert (manifest["status"], manifest["error_code"]) == ("failed", "plan_failed") and not manifest["scenes"]
    assert not openai["prompts"]  # nothing drawn from the text rules meanwhile

    openai["board_error"] = None
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})
    run(app, settings)
    manifest = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()
    assert manifest["status"] == "complete" and all("Storyboard picture" in prompt for prompt in openai["prompts"])


def test_auto_is_offered_first_and_lets_the_story_set_the_pace(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", prepare_on_upload=False, sketch_words=0)
    app = create_app(settings)
    client = TestClient(app)
    info = client.get("/api/info").json()
    assert info["picture_words"] == [0, 1, 25, 60, 120] and info["default_words"] == 0
    book_id = upload(client).json()["id"]
    process_next(app.state.engine, settings)
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001", "words": 0})
    run(app, settings)
    manifest = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001", "words": 0}).json()
    assert manifest["brain"] == "heuristic-4-wauto" and manifest["words"] == 0 and manifest["status"] == "complete"
    assert 5 <= len(manifest["scenes"]) < 20  # fewer than every 25 words: a picture per paragraph or so
    study = Settings(_env_file=None, data_dir=tmp_path / "study", study_dir=tmp_path)
    assert sketches.offered_words(study) == [1, 25, 60, 120] and sketches.default_words(study) == 25


def test_pictures_are_drawn_by_cloudflare_when_its_account_is_set(tmp_path, openai, monkeypatch):
    from imagine_reader.providers import CloudflareImageProvider

    drawn = []

    def render(self, spec, references=(), size=None):
        drawn.append((self.size, size, [reference.name for reference in references]))
        buffer = io.BytesIO()
        Image.new("RGB", (64, 48), "white").save(buffer, format="WEBP")
        return Picture(buffer.getvalue(), "image/webp", 64, 48)

    monkeypatch.setattr(CloudflareImageProvider, "render", render)
    app, client, settings, book_id = stack(
        tmp_path, openai_api_key="sk-test", cloudflare_account_id="acct", cloudflare_api_token="tok"
    )
    assert client.get("/api/info").json()["services"]["pictures"] == "cloudflare"
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})
    run(app, settings)
    manifest = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()
    assert manifest["status"] == "complete" and manifest["provider"].startswith("cloudflare-")
    assert openai["images"] == []  # OpenAI drew nothing
    assert ("1536x1024", "1024x1024", []) in drawn  # Mr. Hale's reference portrait, square
    assert any(names == ["Mr. Hale"] for _, _, names in drawn)  # and sent with his pictures
    study = Settings(_env_file=None, data_dir=tmp_path / "s", study_dir=tmp_path, openai_api_key="sk-test",
                     cloudflare_account_id="acct", cloudflare_api_token="tok")  # fmt: skip
    assert sketches.image_provider(study).id.startswith("openai-")  # the study keeps the paper's model

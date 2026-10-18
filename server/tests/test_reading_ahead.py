"""Getting ahead of the reader: where the story starts, preparing on upload, and drawing nearest first."""

import json

from conftest import FIXTURES, upload
from fastapi.testclient import TestClient

from imagine_reader.providers import MockImageProvider, ProviderBusy
from imagine_reader_server import storage
from imagine_reader_server.api import create_app
from imagine_reader_server.config import Settings
from imagine_reader_server.jobs import process_next


def run(app, settings):
    while process_next(app.state.engine, settings):
        pass


def drawing_order(monkeypatch, busy_once=False):
    drawn, real = [], MockImageProvider.render

    def render(self, spec, references=(), size=None):
        if busy_once and not drawn:
            drawn.append(None)
            raise ProviderBusy("429 rate_limit_exceeded", retry_after=0.01)
        drawn.append(spec.scene_id)
        return real(self, spec, references, size)

    monkeypatch.setattr(MockImageProvider, "render", render)
    return drawn


def test_the_reading_says_where_the_story_starts_and_how_to_read_it(client, app, settings):
    book_id = upload(client).json()["id"]
    process_next(app.state.engine, settings)
    reading = client.get(f"/api/books/{book_id}/reading").json()
    assert reading["start_section"] == "sec-001" and reading["narration_style"] == "story"

    path = storage.book_dir(settings, book_id) / storage.READING  # a book read before these existed
    older = json.loads(path.read_text())
    del older["start_section"], older["narration_style"]
    path.write_text(json.dumps(older))
    assert client.get(f"/api/books/{book_id}/reading").json()["start_section"] == "sec-001"


def test_the_opening_chapter_is_prepared_as_soon_as_the_pdf_is_read(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")  # prepare_on_upload is on by default
    app = create_app(settings)
    client = TestClient(app)
    book_id = upload(client).json()["id"]
    process_next(app.state.engine, settings)  # reads the PDF and queues the preparation
    book = client.get(f"/api/books/{book_id}").json()
    assert book["start_section"] == "sec-001"
    assert book["preparation"]["active"] and book["preparation"]["pictures_ready"] == 0

    run(app, settings)
    preparation = client.get(f"/api/books/{book_id}").json()["preparation"]
    assert not preparation["active"]
    assert preparation["pictures_ready"] == preparation["pictures_planned"] > 10  # a picture every sentence or two
    assert preparation["clips_ready"] == preparation["clips_planned"] == 55


def test_pictures_are_drawn_from_the_reader_onward(client, app, settings, monkeypatch):
    settings.image_lanes = 1  # one lane, so the order is exact
    drawn = drawing_order(monkeypatch)
    book_id = upload(client).json()["id"]
    process_next(app.state.engine, settings)
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001", "from_sentence": 30})
    run(app, settings)
    scenes = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()["scenes"]
    by_id = {scene["scene_id"]: scene for scene in scenes}
    first = by_id[drawn[0]]
    assert first["first_sentence"] <= 30 <= first["last_sentence"]
    later = [by_id[scene_id]["first_sentence"] for scene_id in drawn if by_id[scene_id]["last_sentence"] >= 30]
    assert later == sorted(later) and drawn.index(scenes[0]["scene_id"]) == len(later)  # then the start


def test_several_pictures_at_once_draw_each_scene_once_and_wait_out_a_busy_service(client, app, settings, monkeypatch):
    settings.image_lanes = 4
    drawn = drawing_order(monkeypatch, busy_once=True)
    book_id = upload(client).json()["id"]
    process_next(app.state.engine, settings)
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})
    run(app, settings)
    manifest = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()
    pictures = [scene_id for scene_id in drawn if scene_id is not None]
    assert manifest["status"] == "complete" and drawn[0] is None  # the busy answer came first
    assert sorted(pictures) == sorted(scene["scene_id"] for scene in manifest["scenes"])  # each exactly once
    assert manifest["sketches_used"] == len(manifest["scenes"])


def test_changing_the_scene_length_plans_the_scenes_again(client, app, settings):
    book_id = upload(client).json()["id"]
    process_next(app.state.engine, settings)
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})
    run(app, settings)
    short = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()

    longer = Settings(**{**settings.model_dump(), "sketch_words": 120})
    longer_app = create_app(longer)
    longer_client = TestClient(longer_app, cookies=client.cookies)
    longer_client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001"})
    run(longer_app, longer)
    replanned = longer_client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()
    assert replanned["brain"] == "heuristic-4-w120" and replanned["status"] == "complete"
    assert len(replanned["scenes"]) < len(short["scenes"])


def test_each_picture_style_is_kept_side_by_side(client, app, settings, monkeypatch):
    drawn = drawing_order(monkeypatch)
    book_id = upload(client).json()["id"]
    process_next(app.state.engine, settings)

    def pictures(**choice):
        client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001", **choice})
        run(app, settings)
        return client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001", **choice}).json()

    pencil = pictures()
    color = pictures(style="color")
    assert pencil["style"] == "pencil" and color["style"] == "color" and color["status"] == "complete"
    assert {s["image_url"] for s in color["scenes"]}.isdisjoint(s["image_url"] for s in pencil["scenes"])
    count = len(drawn)
    assert pictures()["scenes"] == pencil["scenes"] and len(drawn) == count  # switching back draws nothing

    often = pictures(style="color", words=1)  # a picture every sentence: your key, your choice
    assert often["words"] == 1 and len(often["scenes"]) > len(color["scenes"])
    bad = client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001", "style": "oil"})
    assert bad.status_code == 422
    assert client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001", "words": 7}).status_code == 422


def test_an_upload_is_prepared_with_the_pictures_chosen(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    app = create_app(settings)
    client = TestClient(app)
    content = (FIXTURES / "two-column.pdf").read_bytes()
    book_id = client.post(
        "/api/books",
        files={"file": ("story.pdf", content, "application/pdf")},
        data={"style": "color-hd", "words": "60"},
    ).json()["id"]
    run(app, settings)
    preparation = client.get(f"/api/books/{book_id}").json()["preparation"]
    chosen = client.get(
        f"/api/books/{book_id}/sketches", params={"section_id": "sec-001", "style": "color-hd", "words": 60}
    )
    assert chosen.json()["status"] == "complete"
    assert preparation["pictures_ready"] == preparation["pictures_planned"] == len(chosen.json()["scenes"])
    default = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001"}).json()
    assert default["scenes"] == []  # only the chosen pictures were made
    rejected = client.post("/api/books", files={"file": ("x.pdf", content, "application/pdf")}, data={"style": "oil"})
    assert rejected.status_code == 422


def test_declined_studio_ghibli_pictures_are_drawn_another_way(client, app, settings, monkeypatch):
    from imagine_reader.providers import ProviderRefused

    tried, real = [], MockImageProvider.render

    def render(self, spec, references=(), size=None):
        tried.append((spec.scene_id, spec.prompt.split(":")[0].split(",")[0]))
        declined_by_name = spec.prompt.startswith("Studio Ghibli")
        declined_again = spec.prompt.startswith("Hand-painted") and spec.scene_id.endswith("2")
        if declined_by_name or declined_again:
            raise ProviderRefused("400 moderation_blocked")
        return real(self, spec, references, size)

    monkeypatch.setattr(MockImageProvider, "render", render)
    book_id = upload(client).json()["id"]
    process_next(app.state.engine, settings)
    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001", "style": "ghibli"})
    run(app, settings)
    manifest = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001", "style": "ghibli"}).json()
    assert manifest["status"] == "complete" and {s["status"] for s in manifest["scenes"]} == {"ready"}  # no gaps
    assert ("sec-001-scn-001", "Hand-painted Japanese animated film still") in tried  # the look, without the name
    assert ("sec-001-scn-002", "Colored storybook illustration in soft watercolor and colored pencil") in tried
    rendered = {scene["scene_id"]: scene["rendered_style"] for scene in manifest["scenes"]}
    assert rendered["sec-001-scn-001"] == "animated" and rendered["sec-001-scn-002"] == "color"  # what was shown
    assert all(scene["drawn_at"] and scene["draw_seconds"] is not None for scene in manifest["scenes"])


def test_a_site_can_offer_the_hand_painted_look_without_naming_a_studio(tmp_path, monkeypatch):
    from imagine_reader.providers import ProviderRefused

    prompts, real = [], MockImageProvider.render

    def render(self, spec, references=(), size=None):
        prompts.append(spec.prompt)
        if spec.prompt.startswith("Hand-painted") and spec.scene_id.endswith("3"):
            raise ProviderRefused("400 moderation_blocked")
        return real(self, spec, references, size)

    monkeypatch.setattr(MockImageProvider, "render", render)
    settings = Settings(
        _env_file=None, data_dir=tmp_path / "data", prepare_on_upload=False, picture_styles="pencil,color,animated"
    )
    app = create_app(settings)
    client = TestClient(app)
    assert client.get("/api/info").json()["picture_styles"] == ["pencil", "color", "animated"]
    book_id = upload(client).json()["id"]
    process_next(app.state.engine, settings)
    studio = client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001", "style": "ghibli"})
    assert studio.status_code == 422  # not offered here

    client.post(f"/api/books/{book_id}/sketches", json={"section_id": "sec-001", "style": "animated"})
    run(app, settings)
    manifest = client.get(f"/api/books/{book_id}/sketches", params={"section_id": "sec-001", "style": "animated"})
    scenes = manifest.json()["scenes"]
    assert {scene["status"] for scene in scenes} == {"ready"}
    rendered = {scene["scene_id"]: scene["rendered_style"] for scene in scenes}
    assert rendered["sec-001-scn-003"] == "color" and rendered["sec-001-scn-001"] == "animated"
    assert prompts and not any("Ghibli" in prompt for prompt in prompts)

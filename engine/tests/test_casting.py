"""Casting: a language model's reading of the book, turned into looks every picture shares."""

import json
import re
from pathlib import Path

import httpx
import pytest

from imagine_reader.ingestion import ingest_pdf
from imagine_reader.providers import OpenAITextModel, ProviderRefused
from imagine_reader.story import CastingStoryBrain, bible_from_cast, sections_from_document, sketch_spec

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "pdfs" / "two-column.pdf"

CAST = {
    "setting": "A rocky coast in the 1900s, with an old stone lighthouse.",
    "characters": [
        {
            "name": "Nora",
            "aliases": ["child", "the girl"],  # plain words are no use for finding her
            "role": "protagonist",
            "gender": "female",
            "age": "eleven",
            "species": "human",
            "look": "Girl of eleven with short dark hair and a red wool coat.",
            "look_from_text": False,
        },
        {
            "name": "Mr. Hale",
            "aliases": ["the keeper"],
            "role": "main",
            "gender": "male",
            "age": "old",
            "species": "human",
            "look": "Old man with a grey beard and a dark oilskin coat.",
            "look_from_text": False,
        },
        {
            "name": "Captain Ward",  # never named in the text
            "aliases": [],
            "role": "supporting",
            "gender": "male",
            "age": "",
            "species": "human",
            "look": "A tall sailor.",
            "look_from_text": False,
        },
    ],
    "things": [{"name": "the brass lantern", "aliases": [], "look": "A dented brass lantern.", "look_from_text": True}],
    "places": [{"name": "Gull Point", "aliases": [], "look": "A rocky headland.", "look_from_text": True}],
}


class FakeReader:
    id = "fake-reader-1"

    def __init__(self, answer=CAST):
        self.answer, self.texts = answer, []

    def estimate_cost(self, characters: int) -> float:
        return characters / 1_000_000

    def json(self, instructions, text, schema, name):
        self.texts.append(text)
        return self.answer, 0.01


@pytest.fixture(scope="module")
def sections():
    return sections_from_document(ingest_pdf(FIXTURE))


def test_the_cast_starts_where_the_text_first_names_each_one(sections):
    bible = bible_from_cast(CAST, sections, "cast-test")
    people = {c.name: c for c in bible.characters}
    assert set(people) == {"Nora", "Mr. Hale"}  # Captain Ward is never named, so never drawn
    assert people["Mr. Hale"].first_mention == 1 and people["Nora"].first_mention == 5
    assert people["Nora"].aliases == []  # "child" would match any child; "the girl" too
    assert people["Nora"].look.startswith("Girl of eleven") and people["Nora"].role == "protagonist"
    assert [t.name for t in bible.things] == ["the brass lantern"]  # "a brass lantern" in the text
    assert [loc.name for loc in bible.locations] == ["Gull Point"] and bible.setting.startswith("A rocky coast")


def test_scenes_keep_the_person_the_story_follows(sections):
    brain = CastingStoryBrain(FakeReader(), sketch_words=25)
    bible = brain.build_bible(sections)
    assert brain.last_cost == 0.01 and brain.id == "cast-1-fake-reader-1-w25"
    text = {sentence.order: sentence.text for sentence in sections[0].sentences}
    for scene in brain.plan_scenes(sections[0], bible):
        passage = " ".join(text[order] for order in range(scene.first_sentence, scene.last_sentence + 1))
        names_nobody = "Nora" not in passage and "Hale" not in passage
        if names_nobody and re.search(r"\b(she|her)\b", passage, re.IGNORECASE):
            assert scene.character_ids == ["chr-001"], passage  # "She thought about it…": Nora
        if names_nobody and not re.search(r"\b(she|her|he|him|his)\b", passage, re.IGNORECASE):
            assert scene.character_ids == [], passage  # "In August a storm arrived.": nobody


def test_every_picture_describes_its_people_and_objects_the_same_way(sections):
    brain = CastingStoryBrain(FakeReader(), sketch_words=25)
    bible = brain.build_bible(sections)
    scenes = brain.plan_scenes(sections[0], bible)
    with_hale = [sketch_spec(s, bible, "p", "color") for s in scenes if "chr-002" in s.character_ids]
    assert len(with_hale) >= 2
    for spec in with_hale:
        assert "Mr. Hale (old, male): Old man with a grey beard and a dark oilskin coat" in spec.prompt
        assert (
            "the same in every picture" in spec.prompt and "Time and place of the story: A rocky coast" in spec.prompt
        )
    lantern = next(sketch_spec(s, bible, "p") for s in scenes if s.thing_ids)
    assert "the brass lantern: A dented brass lantern" in lantern.prompt
    first = sketch_spec(scenes[0], bible, "p")
    assert "Nora" not in first.prompt  # not met yet


def test_openai_reads_the_book_into_json_and_reports_its_cost():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(CAST), "refusal": None}}],
                "usage": {"prompt_tokens": 40_000, "completion_tokens": 1_000},
            },
        )

    client = httpx.Client(base_url="https://api.openai.com/v1", transport=httpx.MockTransport(handler))
    reader = OpenAITextModel("sk-test", "gpt-5.4-mini", client=client)
    answer, cost = reader.json("Read this.", "Once upon a time.", {"type": "object"}, "cast")
    assert answer == CAST and cost == pytest.approx((40_000 * 0.75 + 1_000 * 4.5) / 1_000_000)
    request = seen[0]
    assert request["model"] == "gpt-5.4-mini" and request["response_format"]["json_schema"]["strict"] is True
    assert request["messages"][1]["content"] == "Once upon a time." and request["reasoning_effort"] == "low"
    assert reader.estimate_cost(160_000) == pytest.approx((40_000 * 0.75 + 3_000 * 4.5) / 1_000_000)

    def refusing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "refusal": "No."}}], "usage": {}})

    client = httpx.Client(base_url="https://api.openai.com/v1", transport=httpx.MockTransport(refusing))
    with pytest.raises(ProviderRefused):
        OpenAITextModel("sk-test", client=client).json("Read this.", "text", {"type": "object"}, "cast")

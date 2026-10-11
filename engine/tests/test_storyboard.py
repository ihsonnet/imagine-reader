"""Storyboards: a language model plans each chapter's pictures, checked against the text and the cast."""

import json
import re
from pathlib import Path

import httpx
import pytest
from test_casting import CAST

from imagine_reader.ingestion import ingest_pdf
from imagine_reader.providers import OpenAIImageProvider, Picture, Reference
from imagine_reader.story import (
    AUTO,
    SectionText,
    SentenceText,
    StoryboardStoryBrain,
    check_scene_plan,
    reference_spec,
    sections_from_document,
    sketch_spec,
)
from imagine_reader.story.storyboard import UnusableStoryboard, reading_seconds

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "pdfs" / "two-column.pdf"
BOOK = {**CAST, "chapters": [{"id": "sec-001", "summary": "Nora lights the lighthouse lamp in a storm."}]}


class FakeReader:
    """Answers the book reading with BOOK, and each storyboard with ``shots(sentence numbers)``; a picture held
    too long, planned again as several, with ``again(sentence numbers)`` when given."""

    id = "fake-reader-1"

    def __init__(self, shots=None, again=None):
        self.shots = shots or (lambda numbers: [_shot(n) for n in numbers[::4]])
        self.again = again
        self.requests: list[tuple[str, str]] = []

    def estimate_cost(self, characters: int, answer_tokens: int | None = None) -> float:
        return (characters / 4 * 0.75 + (answer_tokens or 3000) * 4.5) / 1_000_000

    def json(self, instructions, text, schema, name):
        self.requests.append((name, text))
        if name == "book":
            return BOOK, 0.02
        numbers = [int(n) for n in re.findall(r"^\[(\d+)\]", text, re.MULTILINE)]
        shots = self.again if self.again and "THE ONE PICTURE PLANNED" in text else self.shots
        return {"shots": shots(numbers)}, 0.01


def _shot(start, **changes):
    shot = {
        "start": start,
        "picture": f"Wide view at sentence {start}.",
        "caption": f"Picture {start}",
        "characters": ["chr-001"],
        "place": "loc-001",
        "objects": ["obj-001"],
        "mood": "calm",
    }
    return {**shot, **changes}


@pytest.fixture(scope="module")
def sections():
    return sections_from_document(ingest_pdf(FIXTURE))


def test_the_book_is_read_once_with_a_note_on_each_chapter(sections):
    reader = FakeReader()
    brain = StoryboardStoryBrain(reader, 25)
    bible = brain.build_bible(sections)
    assert brain.id == "board-4-fake-reader-1-s2-w25" and brain.bible_id == "board-4-fake-reader-1"
    assert StoryboardStoryBrain(reader).id == "board-4-fake-reader-1-s2-wauto2"  # Auto's second pace: planned anew
    assert [(note.section_id, note.first_sentence) for note in bible.chapters] == [("sec-001", 0)]
    name, text = reader.requests[0]
    assert name == "book" and text.startswith("=== [sec-001] ")  # chapters are marked so each gets a note
    assert {c.name for c in bible.characters} == {"Nora", "Mr. Hale"} and brain.last_cost == 0.02


def test_a_bangla_cast_is_found_by_the_names_the_text_writes():
    lines = ["ট্রেন কিছুক্ষণের মধ্যেই ছাড়বে।", "চিত্রাকে সিদ্ধান্ত নিতে হবে।", "সেখানে বুড়ো ভদ্রলোক আধশোয়া হয়ে আছেন।",
             "স্টেশনের বুক স্টল থেকে এক কপি রিডার্স ডাইজেস্ট কিনেছে।", "আশহাব বলল, আসুন।", "তাঁর নাম রশীদ।"]  # fmt: skip
    sentences = [SentenceText(f"s-{n}", n, f"p-{n}", text) for n, text in enumerate(lines)]
    section = SectionText(id="sec-001", title="১", sentences=sentences)
    person = {"aliases": [], "gender": "male", "age": "old", "species": "human", "look": "x", "look_from_text": False}
    rashid = {"name": "Professor Rashid", "in_text": ["রশীদ"], "before_named": ["বুড়ো ভদ্রলোক"], "role": "main"}
    book = {
        "setting": "Bangladesh, a night train.",
        "characters": [
            {**person, "name": "Chitra", "in_text": ["চিত্রা"], "role": "protagonist", "gender": "female"},
            {**person, **rashid},  # met at sentence 2, named at 5
            {**person, "name": "Ashhab", "in_text": ["আশহাব"], "role": "main"},
            {**person, "name": "Lily", "in_text": [], "role": "supporting"},  # not in this text: left out
        ],
        "things": [
            {
                "name": "Reader's Digest",
                "aliases": [],
                "in_text": ["রিডার্স ডাইজেস্ট"],
                "look": "x",
                "look_from_text": False,
            }
        ],
        "places": [{"name": "the train", "aliases": [], "in_text": ["ট্রেন"], "look": "x", "look_from_text": False}],
        "chapters": [],
    }
    reader = FakeReader()
    reader.json = lambda instructions, text, schema, name: (book, 0.02)
    brain = StoryboardStoryBrain(reader)
    bible = brain.build_bible([section])
    found = {c.name: (c.first_mention, c.named_from, c.called) for c in bible.characters}
    assert found == {
        "Chitra": (1, 1, ""),
        "Professor Rashid": (2, 5, "বুড়ো ভদ্রলোক"),  # in the cast from where he is met, before he is named
        "Ashhab": (4, 4, ""),
    }
    rashid = next(c for c in bible.characters if c.name == "Professor Rashid")
    assert rashid.known_as(3) == "বুড়ো ভদ্রলোক" and rashid.known_as(5) == "Professor Rashid"
    assert [(t.name, t.first_mention) for t in bible.things] == [("Reader's Digest", 3)]
    assert [(p.name, p.first_mention) for p in bible.locations] == [("the train", 0)]

    asked = []
    reader.json = lambda instructions, text, schema, name: (asked.append(text) or {"shots": [_shot(0)]}, 0.01)
    brain.plan_scenes(SectionText(id="sec-001", title="১", sentences=sentences[:4]), bible)
    assert "বুড়ো ভদ্রলোক (main character, male, old, not yet named in the story" in asked[0]
    assert "Professor Rashid" not in asked[0]  # no caption can give his name away before the story does


def test_shots_become_scenes_that_cover_the_chapter(sections):
    section = sections[0]
    framed = FakeReader(lambda numbers: [_shot(n, framing="close-up" if n else "wide") for n in numbers[::4]])
    scenes = StoryboardStoryBrain(framed, 25).plan_scenes(
        section, StoryboardStoryBrain(framed, 25).build_bible(sections)
    )
    assert [s.framing for s in scenes[:2]] == ["wide", "close-up"]
    brain = StoryboardStoryBrain(FakeReader(), 25)
    bible = brain.build_bible(sections)
    scenes = brain.plan_scenes(section, bible)
    check_scene_plan(scenes, section, bible)  # in order, once each, nothing left out
    assert [scene.first_sentence for scene in scenes] == list(range(0, 55, 4))
    assert scenes[0].action == "Wide view at sentence 0." and scenes[0].caption == "Picture 0"
    assert scenes[0].character_ids == ["chr-001"] and scenes[0].location_id == "loc-001"
    assert scenes[2].id == "sec-001-scn-003" and brain.last_cost == 0.01


def test_a_storyboard_is_checked_against_the_text_and_the_cast(sections):
    def careless(numbers):
        return [
            _shot(numbers[2], characters=["chr-001", "chr-099"], place="loc-099", objects=["obj-001", "nothing"]),
            _shot(numbers[2]),  # the same start twice: the first is kept
            _shot(9999),  # past the chapter
            _shot(numbers[10], picture="  "),  # nothing to draw
            _shot(numbers[20]),
        ]

    section = sections[0]
    brain = StoryboardStoryBrain(FakeReader(careless), 25)
    bible = brain.build_bible(sections)
    scenes = brain.plan_scenes(section, bible)
    check_scene_plan(scenes, section, bible)
    assert [scene.first_sentence for scene in scenes] == [0, 20]  # the first picture starts with the chapter
    assert scenes[0].character_ids == ["chr-001"] and scenes[0].location_id is None
    assert scenes[0].thing_ids == ["obj-001"]


def test_a_passage_without_a_usable_storyboard_is_asked_again_never_planned_by_the_text_rules(sections):
    section = sections[0]
    answers = iter([[], [_shot(section.sentences[0].order)]])  # nothing usable, then a usable answer
    reader = FakeReader(lambda numbers: next(answers))
    brain = StoryboardStoryBrain(reader, 25)
    bible = brain.build_bible(sections)
    scenes = brain.plan_scenes(section, bible)
    assert len(scenes) == 1 and len([name for name, _ in reader.requests if name == "storyboard"]) == 2
    with pytest.raises(UnusableStoryboard):  # nothing usable twice: the planning waits, and is asked again later
        StoryboardStoryBrain(FakeReader(lambda numbers: []), 25).plan_scenes(section, bible)


def test_long_chapters_are_planned_in_passages_with_the_story_so_far(sections):
    section = sections[0]
    sentences = [s._replace(text=" ".join([s.text] * 8)) for s in section.sentences]  # about 5,000 words
    long = SectionText(id="sec-002", title="Two", sentences=sentences, heading_paragraphs=section.heading_paragraphs)
    reader = FakeReader()
    brain = StoryboardStoryBrain(reader, AUTO)
    bible = brain.build_bible(sections)
    first_sentence = sentences[0].order
    bible.chapters[0].first_sentence = first_sentence - 1  # a chapter before this one
    scenes = brain.plan_scenes(long, bible)
    check_scene_plan(scenes, long, bible)
    boards = [text for name, text in reader.requests if name == "storyboard"]
    assert len(boards) >= 3
    assert all("Nora lights the lighthouse lamp in a storm." in text for text in boards)
    assert "JUST BEFORE THE PASSAGE" not in boards[0] and "JUST BEFORE THE PASSAGE" in boards[1]
    assert re.match(r"Pace: this passage takes about \d minutes to read aloud\. Plan about \d+ shots", boards[0])
    first = StoryboardStoryBrain(reader, 25).estimate_planning_cost(long, bible)
    assert 0 < first < 0.2
    assert 0 < brain.estimate_planning_cost(long, bible) < 0.2


def test_auto_is_timed_by_the_narration_and_asks_for_a_number_of_shots(sections):
    section = sections[0]
    reader = FakeReader()
    brain = StoryboardStoryBrain(reader, AUTO)
    bible = brain.build_bible(sections)
    brain.plan_scenes(section, bible)
    text = reader.requests[1][1]
    planned = {int(n) for n in re.findall(r"^\[(\d+)\]", text, re.MULTILINE)}
    seconds = sum(reading_seconds(s.text) for s in section.sentences if s.order in planned)
    assert f"Plan about {round(seconds / 13)} shots for it" in text.splitlines()[0]  # a picture about every 13 s
    assert reading_seconds("a" * 130) == 10  # English: about 13 characters a second
    assert reading_seconds("আমি বাড়ি যাব।" * 10) == pytest.approx(len("আমি বাড়ি যাব।") * 10 / 10.5)  # Bangla: slower


def test_a_picture_held_too_long_is_planned_again_as_several(sections):
    section = sections[0]

    def lazy(numbers):  # a model that plans one picture for a whole passage
        return [_shot(numbers[0], picture="One wide view.")]

    reader = FakeReader(lazy, again=lambda numbers: [_shot(n) for n in numbers[::2]])
    brain = StoryboardStoryBrain(reader, AUTO)
    bible = brain.build_bible(sections)
    scenes = brain.plan_scenes(section, bible)
    check_scene_plan(scenes, section, bible)
    assert len(scenes) > 2 and scenes[0].first_sentence == section.sentences[0].order
    again = [text for name, text in reader.requests if name == "storyboard"][-1]
    assert "THE ONE PICTURE PLANNED FOR THE PASSAGE SO FAR" in again and "One wide view." in again
    assert re.search(r"Plan \d+ shots for it", again)  # an exact number this time
    assert brain.last_cost == pytest.approx(0.01 * (len(reader.requests) - 1))  # both rounds are paid for

    def down(numbers):
        raise RuntimeError("the model is down")

    brain = StoryboardStoryBrain(FakeReader(lazy, again=down), AUTO)
    scenes = brain.plan_scenes(section, brain.build_bible(sections))
    assert {scene.action for scene in scenes} == {"One wide view."}  # kept when it cannot be planned again


def test_the_cast_given_to_a_passage_is_only_who_the_reader_has_met(sections):
    section = sections[0]
    reader = FakeReader()
    brain = StoryboardStoryBrain(reader, 1)
    bible = brain.build_bible(sections)
    opening = SectionText(id="sec-001", title=section.title, sentences=section.sentences[:1])
    brain.plan_scenes(opening, bible)
    text = reader.requests[-1][1]
    assert "Mr. Hale" not in text and "Nora" not in text  # neither is named in the first sentence
    assert text.startswith("Pace: a new picture at every sentence.")
    assert "The passage is at the start of the story." in text


def test_main_characters_go_with_their_pictures_as_references(sections):
    brain = StoryboardStoryBrain(FakeReader(), 25)
    bible = brain.build_bible(sections)
    scene = brain.plan_scenes(sections[0], bible)[-1]
    scene = scene.model_copy(update={"character_ids": ["chr-002", "chr-001"]})
    spec = sketch_spec(scene, bible, "p", "pencil", references=True)
    assert spec.references == ["chr-001", "chr-002"]  # the protagonist first
    assert sketch_spec(scene, bible, "p", "pencil").references == []  # only when asked
    for framing, portraits in (("close-up", True), ("medium", False), ("wide", False), ("detail", False)):
        framed = scene.model_copy(update={"framing": framing})  # FLUX copies a portrait's pose into any shot
        assert bool(sketch_spec(framed, bible, "p", "pencil", references=True).references) == portraits
    reference = reference_spec(bible.characters[0], bible, "p", "color")
    assert "Character reference: one full-length figure of Nora" in reference.prompt
    assert "plain empty background" in reference.prompt and reference.prompt.startswith("Colored storybook")


def test_openai_draws_with_references_as_an_edit():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": _webp_b64()}]})

    client = httpx.Client(base_url="https://api.openai.com/v1", transport=httpx.MockTransport(handler))
    artist = OpenAIImageProvider("sk-test", client=client)
    brain = StoryboardStoryBrain(FakeReader(), 25)
    sections = sections_from_document(ingest_pdf(FIXTURE))
    bible = brain.build_bible(sections)
    scene = brain.plan_scenes(sections[0], bible)[-1]
    spec = sketch_spec(scene, bible, artist.id, "pencil", references=True)
    assert artist.estimate_cost(spec) == pytest.approx(0.0038 + 0.001)
    assert artist.reference_cost() == pytest.approx(0.00278)  # drawn square, once

    portrait = Picture(b"RIFF....WEBPVP8 ", "image/webp", 512, 512)
    artist.render(spec, [Reference("Nora", portrait)])
    edit = seen[-1]
    assert edit.url.path == "/v1/images/edits"
    body = edit.content.decode("latin-1")
    assert 'name="image[]"; filename="reference-1.webp"' in body
    assert "Reference pictures, in order: 1. Nora. Draw Nora with exactly the same face" in body

    artist.render(spec)
    generation = seen[-1]
    assert generation.url.path == "/v1/images/generations"
    assert json.loads(generation.content)["prompt"] == spec.prompt


def _webp_b64() -> str:
    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="WEBP")
    return base64.b64encode(buffer.getvalue()).decode()

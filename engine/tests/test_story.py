import io
from pathlib import Path

import pytest
from PIL import Image

from imagine_reader.ingestion import ingest_pdf
from imagine_reader.providers import MockImageProvider
from imagine_reader.story import (
    Character,
    Fact,
    HeuristicStoryBrain,
    InvalidScenePlan,
    Location,
    Scene,
    SectionText,
    SentenceText,
    StoryBible,
    check_scene_plan,
    sections_from_document,
    sketch_spec,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "pdfs" / "two-column.pdf"


@pytest.fixture(scope="module")
def sections():
    return sections_from_document(ingest_pdf(FIXTURE))


def test_heuristic_brain_finds_recurring_names_and_places(sections):
    bible = HeuristicStoryBrain().build_bible(sections)
    characters = {c.name: c for c in bible.characters}
    assert set(characters) == {"Mr. Hale", "Nora"}
    assert characters["Mr. Hale"].first_mention == 1  # "Every evening, ... old Mr. Hale climbed ..."
    assert characters["Nora"].first_mention == 5  # "Nora watched him from the bottom of the stairs."
    assert [loc.name for loc in bible.locations] == ["Gull Point"]


def test_heuristic_scene_plan_covers_the_section_in_order(sections):
    brain = HeuristicStoryBrain()
    bible = brain.build_bible(sections)
    scenes = brain.plan_scenes(sections[0], bible)
    check_scene_plan(scenes, sections[0], bible)  # raises if it does not
    assert 15 <= len(scenes) <= 30  # a picture every sentence or two of a 670-word story
    assert scenes[0].first_sentence == 0  # the title opens the first scene
    assert scenes[0].action.startswith("Every evening")  # described by its first told sentence
    assert scenes[0].location_id == "loc-001"


def _section(count: int) -> SectionText:
    return SectionText(
        id="sec-001",
        title="Story",
        sentences=[SentenceText(f"s-{i}", i, f"p-{i // 3}", f"Sentence {i}.") for i in range(count)],
    )


def _scene(scene_id: str, first: int, last: int, **fields) -> Scene:
    return Scene(
        id=scene_id,
        section_id="sec-001",
        first_sentence=first,
        last_sentence=last,
        sentence_ids=[f"s-{i}" for i in range(first, last + 1)],
        action="Something happens.",
        **fields,
    )


@pytest.mark.parametrize(
    "scenes",
    [
        [_scene("a", 0, 4), _scene("b", 6, 9)],  # gap
        [_scene("a", 0, 5), _scene("b", 5, 9)],  # overlap
        [_scene("a", 0, 4)],  # stops early
        [_scene("a", 0, 12)],  # runs past the end
        [_scene("a", 0, 9, character_ids=["chr-999"])],  # unknown character
        [_scene("a", 0, 9).model_copy(update={"sentence_ids": ["s-0"]})],  # wrong sentence list
    ],
)
def test_scene_plans_that_do_not_fit_the_section_are_rejected(scenes):
    with pytest.raises(InvalidScenePlan):
        check_scene_plan(scenes, _section(10), StoryBible(brain="test"))


def test_sketches_only_use_what_the_reader_has_heard():
    bible = StoryBible(
        brain="test",
        characters=[
            Character(id="hale", name="Mr. Hale", first_mention=1),
            Character(id="stranger", name="the stranger", first_mention=50),
        ],
        locations=[Location(id="cove", name="Smugglers' Cove", first_mention=60)],
        facts=[
            Fact(subject_id="hale", text="grey beard", kind="appearance", evidence=["s-2"], first_reveal=2),
            Fact(
                subject_id="hale", text="a scar across his cheek", kind="appearance", evidence=["s-40"], first_reveal=40
            ),
        ],
    )
    # A brain that wrongly tags the early scene with later characters, facts, and places.
    early = _scene("early", 0, 9, character_ids=["hale", "stranger"], location_id="cove")
    spec = sketch_spec(early, bible, "mock")
    assert spec.characters == ["Mr. Hale"] and spec.location is None
    assert "grey beard" in spec.prompt
    for spoiler in ("scar", "stranger", "Cove"):
        assert spoiler not in spec.prompt

    late = _scene("late", 55, 65, character_ids=["hale", "stranger"], location_id="cove")
    late_spec = sketch_spec(late, bible, "mock")
    assert late_spec.characters == ["Mr. Hale", "the stranger"] and late_spec.location == "Smugglers' Cove"
    assert "scar across his cheek" in late_spec.prompt


def test_sketch_cache_keys_follow_the_prompt_and_provider():
    bible = StoryBible(brain="test")
    scene = _scene("a", 0, 9)
    assert sketch_spec(scene, bible, "mock").cache_key == sketch_spec(scene, bible, "mock").cache_key
    assert sketch_spec(scene, bible, "mock").cache_key != sketch_spec(scene, bible, "other").cache_key
    changed = scene.model_copy(update={"action": "Something else happens."})
    assert sketch_spec(changed, bible, "mock").cache_key != sketch_spec(scene, bible, "mock").cache_key


def test_placeholder_sketches_are_stable_labelled_pictures(sections):
    brain = HeuristicStoryBrain()
    bible = brain.build_bible(sections)
    spec = sketch_spec(brain.plan_scenes(sections[0], bible)[0], bible, MockImageProvider.id)
    picture = MockImageProvider().render(spec)
    assert picture.media_type == "image/png" and (picture.width, picture.height) == (768, 512)
    assert Image.open(io.BytesIO(picture.data)).size == (768, 512)
    assert MockImageProvider().render(spec).data == picture.data


def test_pictures_can_be_drawn_in_color():
    scene = _scene("s1", 0, 2)
    bible = StoryBible(brain="test")
    pencil, color = sketch_spec(scene, bible, "p"), sketch_spec(scene, bible, "p", "color")
    assert pencil.prompt.startswith("Monochrome pencil sketch") and "no color" in pencil.prompt
    assert color.prompt.startswith("Colored storybook illustration") and color.cache_key != pencil.cache_key


def test_a_declined_picture_can_be_asked_for_in_another_style():
    from imagine_reader.story import restyle

    scene = _scene("s1", 0, 2)
    bible = StoryBible(brain="test")
    ghibli = sketch_spec(scene, bible, "p", "ghibli")
    assert ghibli.prompt.startswith("Studio Ghibli style")
    animated = restyle(ghibli, "ghibli", "animated", "p")
    assert animated.prompt.startswith("Hand-painted Japanese animated film") and "Studio Ghibli" not in animated.prompt
    assert animated.prompt.endswith(ghibli.prompt.split(".", 1)[1].strip()) or scene.action in animated.prompt
    assert animated.cache_key != ghibli.cache_key and animated.caption == ghibli.caption
    assert restyle(ghibli, "pencil", "color", "p") is None  # not a pencil request

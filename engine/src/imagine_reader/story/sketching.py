"""From a planned scene to a sketch request, without spoilers.

The story bible may know the ending. A sketch request is built only from
what the reader has heard by the end of its scene: characters, places, and
facts first revealed later are left out, and the prompt says not to invent
people or events.
"""

import hashlib

from .models import Character, Scene, SectionText, SketchSpec, StoryBible

STYLES = {
    "pencil": (
        "Monochrome pencil sketch on off-white paper, loose and restrained, light hatching, "
        "simple background, no color, no text or lettering"
    ),
    "color": (
        "Colored storybook illustration in soft watercolor and colored pencil, warm natural colors, "
        "gentle light, simple background, no text or lettering"
    ),
    "ghibli": (
        "Studio Ghibli style animated film still: hand-painted watercolour backgrounds with lush natural detail, "
        "warm gentle light, simple clean character outlines, calm nostalgic mood, no text or lettering"
    ),
    # The same look without the studio's name, for a second try when a provider declines the first.
    "animated": (
        "Hand-painted Japanese animated film still, soft watercolour backgrounds with lush natural detail, "
        "warm gentle light, characters with simple clean outlines and expressive faces, calm nostalgic mood, "
        "no text or lettering"
    ),
}
STYLE = STYLES["pencil"]
EXCLUSIONS = (
    "Show only what this passage states or clearly implies. Do not add people, objects, or events "
    "that it does not mention, and nothing from later in the story."
)


class InvalidScenePlan(ValueError):
    """A scene plan that does not match its section's sentences or the story bible."""


def visible_bible(bible: StoryBible, boundary: int) -> StoryBible:
    """The part of the bible a reader knows after hearing sentence ``boundary``."""
    return bible.model_copy(
        update={
            "characters": [c for c in bible.characters if c.first_mention <= boundary],
            "locations": [loc for loc in bible.locations if loc.first_mention <= boundary],
            "things": [t for t in bible.things if t.first_mention <= boundary],
            "facts": [f for f in bible.facts if f.first_reveal <= boundary],
        }
    )


SAME_EACH_TIME = "Draw each person exactly as described, the same in every picture."


def _with_look(name: str, look: str) -> str:
    return f"{name}: {look.rstrip('.')}" if look else name


def _person(character: Character, facts: list[str], order: int) -> str:
    """How to draw one person, the same in every picture: "Alice (about seven, female): a small girl…"; named as the
    reader knows them by this sentence."""
    kind = [character.age, "" if character.gender == "unknown" else character.gender]
    kind.append("" if character.species.lower() == "human" else character.species)
    looks = [look for look in (character.look.rstrip("."), *facts) if look]
    name = character.known_as(order)
    label = f"{name} ({', '.join(k for k in kind if k)})" if any(kind) else name
    return f"{label}: {'; '.join(looks)}" if looks else label


MAX_REFERENCES = 3  # reference pictures sent with one picture: the people who matter most in it
_ROLE_RANK = {"protagonist": 0, "main": 1, "supporting": 2}


def sketch_spec(
    scene: Scene, bible: StoryBible, provider_id: str, style: str = "pencil", references: bool = False
) -> SketchSpec:
    """The request for one scene's picture. With ``references``, it names the people whose reference pictures
    should go with it, so the image model draws them as it drew them before: in a close-up only, when the shot
    says its framing, since FLUX.2 [klein] copies a portrait's front-on pose into any shot it is given with (on
    কিছুক্ষণ, a hand holding a ticket and a woman leaning out of a window both came out as her portrait)."""
    known = visible_bible(bible, scene.last_sentence)
    characters = [c for c in known.characters if c.id in scene.character_ids]
    things = [t for t in known.things if t.id in scene.thing_ids]
    location = next((loc for loc in known.locations if loc.id == scene.location_id), None)
    described = [
        _person(
            c, [f.text for f in known.facts if f.subject_id == c.id and f.kind == "appearance"], scene.last_sentence
        )
        for c in characters
    ]

    parts = [f"{STYLES[style]}.", f"Scene: {scene.action}"]
    if known.setting:
        parts.append(f"Time and place of the story: {known.setting.rstrip('.')}.")
    if location:
        parts.append(f"Setting: {_with_look(location.name, location.look)}.")
    if described:
        same = any(c.look for c in characters)
        parts.append(f"People: {'; '.join(described)}." + (" " + SAME_EACH_TIME if same else ""))
    if things:
        parts.append(
            f"Objects, drawn the same in every picture: {'; '.join(_with_look(t.name, t.look) for t in things)}."
        )
    if scene.facts:
        parts.append(f"Stated details: {'; '.join(scene.facts)}.")
    if scene.inferred:
        parts.append(f"Unstated details you may use: {'; '.join(scene.inferred)}.")
    parts.append(f"Mood: {scene.mood}.")
    parts.append(EXCLUSIONS)
    prompt = " ".join(parts)
    drawn_before = sorted((c for c in characters if c.look), key=lambda c: _ROLE_RANK.get(c.role, 3))
    faces = scene.framing in (None, "close-up")
    return SketchSpec(
        scene_id=scene.id,
        reveal_boundary=scene.last_sentence,
        prompt=prompt,
        caption=scene.caption or scene.action,
        characters=[c.known_as(scene.last_sentence) for c in characters],
        location=location.name if location else None,
        cache_key=hashlib.sha256(f"{provider_id}\0{prompt}".encode()).hexdigest()[:32],
        references=[c.id for c in drawn_before[:MAX_REFERENCES]] if references and faces else [],
    )


def reference_spec(character: Character, bible: StoryBible, provider_id: str, style: str = "pencil") -> SketchSpec:
    """A reference picture of one character: drawn once per style, then shown to the image model with every
    picture of them. Only the look they have when first met, so it gives nothing away."""
    person = _person(character, [], character.first_mention)
    parts = [
        f"{STYLES[style]}.",
        f"Character reference: one full-length figure of {person.rstrip('.')}, standing and facing the viewer "
        "with empty hands, holding nothing, on a plain empty background, with nothing else in the picture.",
    ]
    if bible.setting:
        parts.append(f"Time and place of the story: {bible.setting.rstrip('.')}.")
    prompt = " ".join(parts)
    return SketchSpec(
        scene_id=f"reference-{character.id}",
        reveal_boundary=character.first_mention,
        prompt=prompt,
        caption=f"{character.name}, as drawn in every picture",
        characters=[character.name],
        location=None,
        cache_key=hashlib.sha256(f"{provider_id}\0{prompt}".encode()).hexdigest()[:32],
    )


def restyle(spec: SketchSpec, style: str, to_style: str, provider_id: str) -> SketchSpec | None:
    """The same request in another style, for when a provider declines the first; None if it cannot be restyled."""
    head = f"{STYLES[style]}."
    if not spec.prompt.startswith(head):
        return None
    prompt = f"{STYLES[to_style]}.{spec.prompt[len(head) :]}"
    key = hashlib.sha256(f"{provider_id}\0{prompt}".encode()).hexdigest()[:32]
    return spec.model_copy(update={"prompt": prompt, "cache_key": key})


def check_scene_plan(scenes: list[Scene], section: SectionText, bible: StoryBible) -> None:
    """Reject a plan unless its scenes cover the section's sentences in order, once each.

    Scene plans can come from a language model, so they are checked before use.
    """
    orders = [sentence.order for sentence in section.sentences]
    if not orders:
        if scenes:
            raise InvalidScenePlan("scenes for a section without sentences")
        return
    expected = orders[0]
    known_characters = {c.id for c in bible.characters}
    known_locations = {loc.id for loc in bible.locations}
    by_order = {sentence.order: sentence.id for sentence in section.sentences}
    for scene in scenes:
        if scene.section_id != section.id:
            raise InvalidScenePlan(f"{scene.id} belongs to another section")
        if scene.first_sentence != expected or scene.last_sentence < scene.first_sentence:
            raise InvalidScenePlan(f"{scene.id} leaves a gap or overlaps the previous scene")
        if scene.last_sentence > orders[-1]:
            raise InvalidScenePlan(f"{scene.id} runs past the end of the section")
        span = range(scene.first_sentence, scene.last_sentence + 1)
        if scene.sentence_ids != [by_order[order] for order in span]:
            raise InvalidScenePlan(f"{scene.id} lists the wrong sentences")
        if not set(scene.character_ids) <= known_characters:
            raise InvalidScenePlan(f"{scene.id} names characters the story bible does not have")
        if scene.location_id is not None and scene.location_id not in known_locations:
            raise InvalidScenePlan(f"{scene.id} names a place the story bible does not have")
        expected = scene.last_sentence + 1
    if expected != orders[-1] + 1:
        raise InvalidScenePlan("the scenes stop before the end of the section")

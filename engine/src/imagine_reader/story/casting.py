"""Casting: a language model reads the whole book once and fixes how its people, things, and places look.

Pictures are drawn a sentence or two at a time, by an image model that
remembers nothing between pictures. To keep Alice the same girl in every
picture, each request carries the same description of her. This module asks
a text model for those descriptions (the protagonist and other main
characters, the main objects and places, and the story's time and place)
and turns the answer into a story bible. Where each one is first mentioned
is found in the text, not taken from the model, so a picture never shows
someone the reader has not met yet.
"""

import re
from typing import Protocol

from .brain import SKETCH_WORDS, HeuristicStoryBrain
from .models import Character, Location, Scene, SectionText, StoryBible, Thing
from .names import has_cases, name_pattern

INSTRUCTIONS = """You prepare a story for an illustrator who draws a picture every sentence or two and must draw \
the same people, things, and places the same way every time.

From the whole text, list:
- characters: the protagonist and the other main characters, at most 8, the ones who appear most. For each: the \
name the text uses most; other names the text uses for them (aliases: only names and titles that identify them, \
such as "the Rabbit" or "Mr. Hale", never plain words like "child"); role; gender and age as the text gives them \
(gender "unknown" if the text never says); species (human, rabbit, cat...); and a fixed look: face, hair, build, \
clothes, and anything they always carry.
- things: up to 8 objects that recur or matter to the story (a key, a bottle, a lantern), each with a fixed look.
- places: up to 6 places where much of the story happens, each with a fixed look.
- setting: when and where the story takes place, for drawing it (period, country, kind of places).

Looks: use what the text says. Where it says nothing, choose one plausible, simple look that fits the period and \
setting, and set look_from_text to false. Describe everyone and everything as when first met: nothing revealed \
later, no plot, and no objects a character only picks up later in the story. Write looks as short visual \
descriptions, without names of real artists, films, or other books."""

_ENTRY = {
    "name": {"type": "string"},
    "aliases": {"type": "array", "items": {"type": "string"}},
    "look": {"type": "string"},
    "look_from_text": {"type": "boolean"},
}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["setting", "characters", "things", "places"],
    "properties": {
        "setting": {"type": "string"},
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [*_ENTRY, "role", "gender", "age", "species"],
                "properties": {
                    **_ENTRY,
                    "role": {"type": "string", "enum": ["protagonist", "main", "supporting"]},
                    "gender": {"type": "string", "enum": ["female", "male", "unknown"]},
                    "age": {"type": "string"},
                    "species": {"type": "string"},
                },
            },
        },
        "things": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": False, "required": list(_ENTRY), "properties": _ENTRY},
        },
        "places": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": False, "required": list(_ENTRY), "properties": _ENTRY},
        },
    },
}

_ARTICLE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
MAX_CHARACTERS, MAX_THINGS, MAX_PLACES = 8, 8, 6


class TextModel(Protocol):
    id: str

    def estimate_cost(self, characters: int, answer_tokens: int | None = None) -> float: ...  # US dollars, before

    def json(self, instructions: str, text: str, schema: dict, name: str) -> tuple[dict, float]: ...  # answer, cost


class CastingStoryBrain:
    """The heuristic brain's scenes, with a cast fixed by a language model that has read the whole book."""

    def __init__(self, reader: TextModel, sketch_words: int = SKETCH_WORDS):
        self.reader = reader
        self.scenes = HeuristicStoryBrain(sketch_words)
        self.bible_id = f"cast-1-{reader.id}"
        self.id = f"{self.bible_id}-w{sketch_words}"
        self.last_cost = 0.0  # what the last analysis cost, for the spending ledger

    def estimate_cost(self, sections: list[SectionText]) -> float:
        return self.reader.estimate_cost(len(book_text(sections)))

    def build_bible(self, sections: list[SectionText]) -> StoryBible:
        cast, self.last_cost = self.reader.json(INSTRUCTIONS, book_text(sections), SCHEMA, "cast")
        return bible_from_cast(cast, sections, self.bible_id)

    def plan_scenes(self, section: SectionText, bible: StoryBible) -> list[Scene]:
        return self.scenes.plan_scenes(section, bible)


def book_text(sections: list[SectionText]) -> str:
    """The book's sentences, a paragraph to a line."""
    paragraphs: list[list[str]] = []
    last = None
    for section in sections:
        for sentence in section.sentences:
            if sentence.paragraph_id != last:
                paragraphs.append([])
                last = sentence.paragraph_id
            paragraphs[-1].append(sentence.text)
    return "\n\n".join(" ".join(paragraph) for paragraph in paragraphs)


def bible_from_cast(
    cast: dict, sections: list[SectionText], brain_id: str, max_characters: int = MAX_CHARACTERS
) -> StoryBible:
    """A story bible from the model's answer; each entry starts where the text first names it.

    Characters must be named with a capital letter (in scripts that have
    them), so a stray "child" does not match every child. A character the text never names is left out,
    except the protagonist, who is known from the start.
    """
    sentences = [sentence for section in sections for sentence in section.sentences]
    start = sentences[0].order if sentences else 0
    characters: list[Character] = []
    for entry in cast.get("characters", [])[:max_characters]:
        names = _names(entry, proper=True)
        # What the text calls them before it names them (বুড়ো ভদ্রলোক), each found where it first appears.
        before = [name.strip() for name in entry.get("before_named", []) if len(_ARTICLE.sub("", name.strip())) >= 3]
        found = {
            name: at for name in before if (at := _first_mention(sentences, [name], ignore_case=False)) is not None
        }
        named_from = _first_mention(sentences, names, ignore_case=False)
        early = min(found.values(), default=None)
        first = min((at for at in (named_from, early) if at is not None), default=None)
        if first is None and entry["role"] != "protagonist":
            continue
        called = min(found, key=found.__getitem__) if found else ""
        characters.append(
            Character(
                id=f"chr-{len(characters) + 1:03d}",
                name=entry["name"].strip(),
                aliases=[name for name in [*names, *before] if name != entry["name"].strip()],
                called=called if early is not None and (named_from is None or early < named_from) else "",
                named_from=named_from,
                first_mention=start if first is None else first,
                role=entry["role"],
                gender=entry["gender"],
                age=entry["age"].strip(),
                species=entry["species"].strip() or "human",
                look=entry["look"].strip(),
            )
        )
    things = [
        Thing(
            id=f"obj-{index:03d}",
            name=entry["name"].strip(),
            aliases=[name for name in names if name != entry["name"].strip()],
            first_mention=first,
            look=entry["look"],
        )
        for index, (entry, names, first) in enumerate(_found(cast.get("things", [])[:MAX_THINGS], sentences), start=1)
    ]
    places = [
        Location(
            id=f"loc-{index:03d}",
            name=entry["name"].strip(),
            aliases=[name for name in names if name != entry["name"].strip()],
            first_mention=first,
            look=entry["look"],
        )
        for index, (entry, names, first) in enumerate(_found(cast.get("places", [])[:MAX_PLACES], sentences), start=1)
    ]
    return StoryBible(
        brain=brain_id, setting=cast.get("setting", "").strip(), characters=characters, locations=places, things=things
    )


def _found(entries: list[dict], sentences) -> list[tuple[dict, list[str], int]]:
    """Objects and places the text names, in any case, with where it first does."""
    found = []
    for entry in entries:
        names = _names(entry, proper=False)
        first = _first_mention(sentences, names, ignore_case=True)
        if first is not None:
            found.append((entry, names, first))
    return found


def _names(entry: dict, proper: bool) -> list[str]:
    """The entry's name, then its usable aliases, then the names as the text writes them (a storyboard's reading
    gives those, in the text's own script: they may be plain words, such as "বুড়ো ভদ্রলোক"), without duplicates."""
    names: list[str] = []
    given = [(name, proper) for name in [entry["name"], *entry.get("aliases", [])]]
    for name, must_be_proper in [*given, *((name, False) for name in entry.get("in_text", []))]:
        name = name.strip()
        bare = _ARTICLE.sub("", name)
        capitalized = any(ch.isupper() for ch in bare) or not has_cases(bare)  # Bangla has no capitals
        if len(bare) < 3 or (must_be_proper and not capitalized):
            continue
        if bare.lower() not in {_ARTICLE.sub("", n).lower() for n in names}:
            names.append(name)
    return names


def _first_mention(sentences, names: list[str], ignore_case: bool) -> int | None:
    if not names:
        return None
    flags = re.IGNORECASE if ignore_case else 0
    pattern = re.compile("|".join(name_pattern(_ARTICLE.sub("", n)) for n in names), flags)
    return next((sentence.order for sentence in sentences if pattern.search(sentence.text)), None)

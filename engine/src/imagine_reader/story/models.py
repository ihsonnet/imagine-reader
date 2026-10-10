"""Contracts for story analysis and scene planning.

Analysis may know the whole book. Every character, place, and fact records
the sentence where the reader first learns it (``first_mention`` or
``first_reveal``, a sentence order), so a sketch can be limited to what the
reader has heard so far.
"""

from datetime import datetime
from typing import Literal, NamedTuple

from pydantic import BaseModel, Field

BIBLE_VERSION = "story-bible-0.2"


class SentenceText(NamedTuple):
    id: str
    order: int
    paragraph_id: str
    text: str


class SectionText(BaseModel):
    """The text a story brain reads: one section's sentences, in order."""

    id: str
    title: str
    sentences: list[SentenceText]
    heading_paragraphs: set[str] = Field(default_factory=set)


class Character(BaseModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    first_mention: int  # sentence order
    role: Literal["protagonist", "main", "supporting"] = "supporting"
    gender: Literal["female", "male", "unknown"] = "unknown"
    age: str = ""  # as the text gives it, e.g. "about seven"
    species: str = "human"
    look: str = ""  # how to draw them, the same in every picture
    # Someone met before the text names them: what it calls them until then, and where it first names them (None:
    # never by a name). Captions call them that until the reader learns the name.
    called: str = ""
    named_from: int | None = None

    def known_as(self, order: int) -> str:
        """What the reader knows them as at this sentence: their name, or what the text has called them so far."""
        if self.called and (self.named_from is None or order < self.named_from):
            return self.called
        return self.name


class Location(BaseModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    first_mention: int
    look: str = ""


class Thing(BaseModel):
    """An object that matters to the story, drawn the same way whenever it appears."""

    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    first_mention: int
    look: str = ""


class Fact(BaseModel):
    subject_id: str  # a character or location id
    text: str  # e.g. "carries a brass lantern"
    kind: Literal["appearance", "trait", "event", "identity"]
    evidence: list[str]  # sentence ids that support it
    first_reveal: int  # sentence order where the reader learns it


class ChapterNote(BaseModel):
    """What happens in one section, for planning the pictures of the sections after it."""

    section_id: str
    first_sentence: int  # where the section starts, so a plan reads only the notes before it
    summary: str


class StoryBible(BaseModel):
    schema_version: str = BIBLE_VERSION
    brain: str  # which brain produced it
    setting: str = ""  # when and where the story takes place, for drawing it
    characters: list[Character] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    things: list[Thing] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    chapters: list[ChapterNote] = Field(default_factory=list)  # the story so far, section by section
    uncertain: list[str] = Field(default_factory=list)  # things the brain flagged as unsure


class Scene(BaseModel):
    """A run of adjacent sentences shown with one sketch."""

    id: str
    section_id: str
    first_sentence: int  # sentence order, inclusive
    last_sentence: int  # inclusive; also the reveal boundary for its sketch
    sentence_ids: list[str]
    location_id: str | None = None
    character_ids: list[str] = Field(default_factory=list)
    thing_ids: list[str] = Field(default_factory=list)
    action: str  # what is visibly happening: the text itself, or a storyboard's description of the picture
    framing: str | None = None  # a storyboard's: wide, medium, close-up, or detail (None: not said)
    caption: str = ""  # a plain description for those who cannot see the picture; the action when empty
    mood: str = "quiet"
    facts: list[str] = Field(default_factory=list)  # stated in the passage
    inferred: list[str] = Field(default_factory=list)  # reasonable but unstated details, kept separate


class SketchSpec(BaseModel):
    """What an image provider is asked to draw for one scene."""

    scene_id: str
    reveal_boundary: int
    prompt: str
    caption: str  # a plain description of the scene, also the image's alt text
    characters: list[str]  # names the sketch may show
    location: str | None
    cache_key: str
    references: list[str] = Field(default_factory=list)  # ids of the characters whose reference pictures go with it


SKETCH_MANIFEST_VERSION = "sketches-0.1"


class SceneReference(BaseModel):
    """A reference picture of one of a scene's characters, for a reader's own key to draw the scene with the same
    faces as this site's pictures: this site's own, when it has drawn one, or else how to draw it."""

    name: str
    image_url: str | None = None  # this site's picture of them, when it has drawn one
    prompt: str = ""  # how this site describes it, for a reader's own key to draw it the same way
    key: str = ""  # the same for every reader of the book in this style, to keep a picture drawn once


class SceneImage(BaseModel):
    scene_id: str
    first_sentence: int
    last_sentence: int
    status: Literal["pending", "ready", "failed", "skipped"]
    image_url: str | None
    caption: str
    error_code: str | None = None
    prompt: str = ""  # what the image model is asked for, so a reader's own key can draw it
    rendered_style: str | None = None  # the drawing style accepted: another than the plan's when it was declined
    drawn_at: datetime | None = None  # when the picture was drawn (UTC)
    draw_seconds: float | None = None  # how long the image model took, declined tries included
    references: list[SceneReference] = Field(default_factory=list)  # its characters' reference pictures, as kept


class SketchManifest(BaseModel):
    """The sketches of one section, for a player to show as narration reaches each scene."""

    schema_version: str = SKETCH_MANIFEST_VERSION
    book_id: str
    section_id: str
    brain: str
    provider: str
    style: str = "pencil"  # the picture style of this set of pictures
    words: int = 25  # a new picture after about this many words
    status: Literal["idle", "planning", "drawing", "complete", "failed"]
    error_code: str | None = None  # why planning failed, when status is "failed"
    scenes: list[SceneImage]
    sketches_used: int  # across the whole book, against the budget
    sketch_budget: int
    request: dict | None = None  # how to draw a missing picture with the reader's own key: model, quality, size

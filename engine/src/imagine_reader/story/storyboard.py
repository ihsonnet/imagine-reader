"""Storyboards: a language model plans each chapter's pictures the way a storyboard artist plans a film.

The text rules cut a chapter every so many words and describe each picture by
its opening sentences. Neighbouring pictures can then repeat each other, and
"She ran after it" says nothing about who she is, where, or after what. This
brain reads the whole book once, as casting does (how its people, objects and
places look), and also notes what happens in each chapter. It then plans each
chapter as a sequence of shots: the sentence where each picture appears and
what it shows, written knowing the story so far. Pictures change at the
story's moments, each shows something new, and people keep their looks and
whatever the story has done to them.

A chapter's plan is one text request (a long chapter is split into a few,
sent together). Pictures are drawn from it later, a few ahead of the reader.
Each shot is checked against the chapter's sentences and the cast before use,
and a passage whose answer cannot be used is asked again, then refused.

``sketch_words`` sets the pace: about so many words a picture, or ``AUTO``,
where the story decides: quick in action, slower in calm passages. Auto is
timed by the narration, and the model is told how many shots each passage
needs, since it plans too few when only given a rate. A picture it still holds
too long is planned again as several.
"""

import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from .brain import AUTO, _clip, words_label
from .casting import INSTRUCTIONS as CAST_INSTRUCTIONS
from .casting import SCHEMA as CAST_SCHEMA
from .casting import TextModel, bible_from_cast, book_text
from .models import ChapterNote, Character, Scene, SectionText, SentenceText, StoryBible
from .sketching import visible_bible

_CARRIED = "clothes, and anything they always carry"
assert _CARRIED in CAST_INSTRUCTIONS
BOOK_CHARACTERS = 12  # the people a whole book's reading lists (a novel has more than the study's 8)
_EIGHT = "at most 8, the ones who appear most"
assert _EIGHT in CAST_INSTRUCTIONS
BOOK_INSTRUCTIONS = (
    CAST_INSTRUCTIONS.replace(  # objects are drawn only in the pictures that show them, not in every picture
        _CARRIED, "and clothes. Leave out things they carry or hold: objects are listed on their own"
    ).replace(_EIGHT, f"at most {BOOK_CHARACTERS}, the ones who appear most, after reading the whole book")
    + """

Names: write name and aliases in English letters, for the illustrator. In in_text, copy every name, title, or \
phrase the text itself uses for them, exactly as the text spells it, in the text's own script and language (in a \
Bangla text, in Bangla letters: চিত্রা, not Chitra), without case endings. List people the text never names but \
who appear in several scenes too, such as an old man sharing a compartment: a short English description as their \
name, and what the text calls them in in_text (বুড়ো ভদ্রলোক, the old gentleman). Give places and objects their \
in_text names the same way.

Many people appear before the text first gives their name: an old man in the compartment revealed chapters later \
as Professor Rashid, a wife called only "his wife" until her name comes up, an attendant named only when he speaks \
again. For every character, look back from where the text first names them: wherever they appear earlier, under a \
description, copy those descriptions into before_named exactly as the text spells them (বুড়ো এক ভদ্রলোক, মাওলানার \
স্ত্রী, রেলের একজন এটেনডেন্ট), so they are drawn the same from the moment they appear. Leave before_named empty \
only for someone named the first time they appear.

Also write chapters: one entry for every chapter of the text, which is marked like === [sec-003] Title ===, with its \
id and a summary of two or three sentences: what happens in it and where, and any lasting change to how someone or \
something looks (grows smaller, is hurt, changes clothes, loses a hat). Illustrators of the later chapters read it to \
keep their pictures true to the story so far."""
)


def _with_names_in_text(entry_schema: dict, *more: str) -> dict:
    """An entry of the cast, with the names as the text writes them (any script), to find them in it."""
    items = entry_schema["items"]
    added = {name: {"type": "array", "items": {"type": "string"}} for name in ("in_text", *more)}
    properties = {**items["properties"], **added}
    return {**entry_schema, "items": {**items, "required": [*items["required"], *added], "properties": properties}}


BOOK_SCHEMA = {
    **CAST_SCHEMA,
    "required": [*CAST_SCHEMA["required"], "chapters"],
    "properties": {
        **CAST_SCHEMA["properties"],
        "characters": _with_names_in_text(CAST_SCHEMA["properties"]["characters"], "before_named"),
        **{kind: _with_names_in_text(CAST_SCHEMA["properties"][kind]) for kind in ("things", "places")},
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "summary"],
                "properties": {"id": {"type": "string"}, "summary": {"type": "string"}},
            },
        },
    },
}

STORYBOARD_INSTRUCTIONS = """You are the storyboard artist for an illustrated audiobook. While a narrator reads the \
book aloud, pictures appear one after another, each staying on screen until the next, so that together they tell \
the story as it unfolds, like the frames of an animated film. You plan those pictures for one passage.

You get the story's cast (people, places, and objects, with ids and looks), the story so far, and the passage, one \
sentence a line with its number in brackets. Plan shots for the numbered sentences only.

For each shot:
- start: the number of the sentence where its picture appears. It stays until the next shot starts. The first shot \
starts at the first numbered sentence, and starts go up.
- picture: what to draw, for an illustrator who has not read the book: who is where, doing what, their poses and \
expressions, what is around them, the framing (wide, medium, close-up, from above...), and the light. Two to four \
sentences, in English. Call listed characters by their names; their looks are added for you, so do not describe \
them. Describe anyone not on the list briefly (age, gender, clothes) every time they appear.
- framing: wide (the place, with any people small in it), medium (people from the knees or waist up), close-up (a \
face, or head and shoulders, filling much of the picture), or detail (hands or an object, filling it).
- caption: one plain sentence saying what the picture shows, for someone who cannot see it, in the language of the \
passage.
- characters, place, objects: ids of the listed people, the place, and the objects that can be seen in the picture.
- mood: a few words.

Make the pictures follow the story:
- Change the picture at the moments that matter: a new place, someone arriving or leaving, an action, a discovery, \
a turn in feeling.
- Never draw the same picture twice in a row. Each picture shows something visibly new: a new moment, a new \
subject, or a different framing. In a long conversation, move between the speakers, their reactions, and details \
of the place, instead of repeating one view.
- Keep continuity: people keep the clothes, injuries, size, and things they carry that the story has given them, \
and places keep their look and time of day until the story changes them.
- Draw what the sentences are about at that moment: the person, place, or thing they describe. Put a listed \
character in a shot only when the text shows them in that moment. Never add the protagonist as an onlooker, at the \
edge or in the background of a shot about someone or something else.
- Vary the shots as a film does: wide views of the place, the other people, close-ups of faces, hands, and objects, \
views over a shoulder. Never centre the same person facing the viewer in two shots in a row.
- A preface, foreword, dedication, or author's note is the author speaking, not the story: draw what it talks about \
(a train, a city), without the story's characters.
- A shot shows only what the text has said by its last sentence (the one before the next shot starts), never what \
comes later.
- No words, letters, captions, or speech bubbles in the pictures. No names of real artists, studios, or films."""

PACES = {1: "Pace: a new picture at every sentence. A very short sentence may share a picture with the next one."}
PACE = (
    "Pace: a new picture about every {words} words of narration, at the nearest moment where the picture should "
    "change. Keep to this pace: when little happens, change the framing or show a detail rather than repeat a picture."
)
AUTO_PACE = (
    "Pace: this passage takes about {time} to read aloud. Plan {shots} shots for it, a new picture about every "
    "{each} words on average: quicker in action and when much happens (every sentence or two), slower in calm "
    "description, reflection, or a long conversation in one place. Never hold one picture for more than about {most} "
    "words: in a calm stretch, show the place, another person, or a telling detail instead."
)
# Auto's pace, in seconds of narration: a new picture about every 13 seconds (4 to 5 a minute), and none held much
# longer than 25. A picture held longer than LONGEST is planned again as several.
AUTO_SECONDS_EACH, AUTO_SECONDS_MOST, AUTO_SECONDS_LONGEST = 13, 25, 30
AUTO_PASSAGE_SECONDS = 240  # an auto-paced chapter is planned in passages of about four minutes, side by side
# How fast the site's storyteller voice reads, in characters a second with its pauses, as measured on its books.
CHARACTERS_A_SECOND, BENGALI_CHARACTERS_A_SECOND = 13.0, 10.5
_BENGALI = re.compile(r"[ঀ-৿]")

_SHOT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["start", "picture", "framing", "caption", "characters", "place", "objects", "mood"],
    "properties": {
        "start": {"type": "integer"},
        "picture": {"type": "string"},
        "framing": {"type": "string", "enum": ["wide", "medium", "close-up", "detail"]},
        "caption": {"type": "string"},
        "characters": {"type": "array", "items": {"type": "string"}},
        "place": {"type": ["string", "null"]},
        "objects": {"type": "array", "items": {"type": "string"}},
        "mood": {"type": "string"},
    },
}
STORYBOARD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["shots"],
    "properties": {"shots": {"type": "array", "items": _SHOT}},
}

PASSAGE_WORDS = 1500  # a longer chapter is planned in passages of about this many words, side by side
SENTENCE_PASSAGE_WORDS = 500  # at a picture every sentence the answer is long, so passages are shorter
BEFORE_WORDS = 120  # the end of the previous passage, given for context
STORY_SO_FAR_CHARACTERS = 6000  # the latest chapter notes that fit
SHOT_TOKENS, PLANNING_TOKENS = 140, 1000  # tokens of answer per shot, and for the model's own reasoning
MAX_PARALLEL = 4
# A pace's plans made anew since its first version, under a new id, so chapters planned before are planned again as
# they are read. 2: Auto timed by the narration (2026-10-16).
PACE_VERSIONS = {AUTO: "2"}
# Every pace's plans, likewise. 2: each shot says its framing, and only close-ups carry a portrait (2026-10-16).
SHOTS_VERSION = 2
FRAMINGS = ("wide", "medium", "close-up", "detail")
_ROLE = {"protagonist": "the protagonist", "main": "main character", "supporting": "supporting"}


class UnusableStoryboard(Exception):
    """The model's storyboard for a passage could not be used, asked twice."""


class StoryboardStoryBrain:
    """Reads the whole book once, then plans each chapter's pictures as a storyboard."""

    references = True  # each main character is drawn once and shown to the image model with every picture of them

    def __init__(self, reader: TextModel, sketch_words: int = AUTO):
        self.reader = reader
        self.sketch_words = sketch_words
        # 2: names as the text writes them, so a Bangla cast is found. 3: up to 12 people, each found from where
        # the story meets them, even before it names them. 4: that looked for in every character, not only one.
        self.bible_id = f"board-4-{reader.id}"
        self.id = f"{self.bible_id}-s{SHOTS_VERSION}-w{words_label(sketch_words)}{PACE_VERSIONS.get(sketch_words, '')}"
        self.last_cost = 0.0  # what the last reading or plan cost, for the spending ledger
        self._lock = threading.Lock()

    # Reading the whole book.

    def estimate_cost(self, sections: list[SectionText]) -> float:
        return self.reader.estimate_cost(len(marked_book_text(sections)), 3000 + 120 * len(sections))

    def build_bible(self, sections: list[SectionText]) -> StoryBible:
        answer, self.last_cost = self.reader.json(BOOK_INSTRUCTIONS, marked_book_text(sections), BOOK_SCHEMA, "book")
        bible = bible_from_cast(answer, sections, self.bible_id, max_characters=BOOK_CHARACTERS)
        starts = {section.id: section.sentences[0].order for section in sections if section.sentences}
        notes = {}
        for chapter in answer.get("chapters", []):
            if chapter["id"] in starts and chapter["summary"].strip():
                notes.setdefault(chapter["id"], chapter["summary"].strip())
        bible.chapters = [ChapterNote(section_id=id_, first_sentence=starts[id_], summary=notes[id_]) for id_ in notes]
        bible.chapters.sort(key=lambda note: note.first_sentence)
        return bible

    # Planning a chapter.

    def estimate_planning_cost(self, section: SectionText, bible: StoryBible) -> float:
        return sum(
            self.reader.estimate_cost(len(request), self._answer_tokens(passage))
            for passage, request in self._requests(section, bible)
        )

    def plan_scenes(self, section: SectionText, bible: StoryBible) -> list[Scene]:
        requests = self._requests(section, bible)
        if not requests:
            return []
        with ThreadPoolExecutor(min(MAX_PARALLEL, len(requests))) as pool:
            answers = list(pool.map(self._board, (request for _, request in requests)))
        cost = sum(cost for _, cost in answers)
        scenes: list[Scene] = []
        for (passage, request), (answer, _) in zip(requests, answers, strict=True):
            found = _shots(section, passage, answer, bible)
            if not found:  # asked once more; the text rules never stand in
                answer, more = self._board(request)
                cost += more
                found = _shots(section, passage, answer, bible)
            if not found:
                raise UnusableStoryboard(f"no usable shots for sentences {passage[0].order}-{passage[-1].order}")
            scenes += found
        if self.sketch_words == AUTO:
            scenes, more = self._shorten(section, scenes, bible)
            cost += more
        with self._lock:
            self.last_cost = cost
        return [scene.model_copy(update={"id": f"{section.id}-scn-{n:03d}"}) for n, scene in enumerate(scenes, 1)]

    def _board(self, request: str) -> tuple[dict, float]:
        return self.reader.json(STORYBOARD_INSTRUCTIONS, request, STORYBOARD_SCHEMA, "storyboard")

    def _shorten(self, section: SectionText, scenes: list[Scene], bible: StoryBible) -> tuple[list[Scene], float]:
        """Pictures held too long, each planned again as several in one more request; the cost of those requests.

        A picture whose request fails, or whose answer cannot be used, stays as it was.
        """
        at = {sentence.order: index for index, sentence in enumerate(section.sentences)}
        stretches = {}
        for index, scene in enumerate(scenes):
            stretch = section.sentences[at[scene.first_sentence] : at[scene.last_sentence] + 1]
            if len(stretch) > 1 and _reading_time(stretch) > AUTO_SECONDS_LONGEST:
                before = _tail(section.sentences[: at[scene.first_sentence]], BEFORE_WORDS)
                request = self._request(section, stretch, bible, before, planned=scene.action)
                stretches[index] = (stretch, request)
        if not stretches:
            return scenes, 0.0

        def board(request: str) -> tuple[dict, float]:
            try:
                return self._board(request)
            except Exception:
                return {}, 0.0

        with ThreadPoolExecutor(min(MAX_PARALLEL, len(stretches))) as pool:
            answers = dict(zip(stretches, pool.map(board, (request for _, request in stretches.values())), strict=True))
        shortened: list[Scene] = []
        for index, scene in enumerate(scenes):
            if index in stretches:
                shortened += _shots(section, stretches[index][0], answers[index][0], bible) or [scene]
            else:
                shortened.append(scene)
        return shortened, sum(cost for _, cost in answers.values())

    def _requests(self, section: SectionText, bible: StoryBible) -> list[tuple[list[SentenceText], str]]:
        if self.sketch_words == AUTO:
            passages = _passages(section.sentences, AUTO_PASSAGE_SECONDS, _seconds)
        else:
            passages = _passages(section.sentences, SENTENCE_PASSAGE_WORDS if self.sketch_words == 1 else PASSAGE_WORDS)
        requests = []
        for index, passage in enumerate(passages):
            before = _tail(passages[index - 1], BEFORE_WORDS) if index else ""
            requests.append((passage, self._request(section, passage, bible, before)))
        return requests

    def _request(
        self, section: SectionText, passage: list[SentenceText], bible: StoryBible, before: str, planned: str = ""
    ) -> str:
        """A passage to plan; or, with ``planned``, a stretch planned as one picture (that one) to plan as several."""
        known = visible_bible(bible, passage[-1].order)  # nobody the reader has not met by the passage's end
        if self.sketch_words == AUTO:
            pace = _auto_pace(passage, exact=bool(planned))
        else:
            pace = PACES.get(self.sketch_words) or PACE.format(words=self.sketch_words)
        lines = [pace, ""]
        if known.setting:
            lines += ["SETTING", known.setting, ""]
        if known.characters:
            end = passage[-1].order
            lines += ["CHARACTERS", *(f"- {c.id}: {_character(c, end)}" for c in known.characters), ""]
        if known.locations:
            lines += ["PLACES", *(f"- {p.id}: {p.name}{_look(p.look)}" for p in known.locations), ""]
        if known.things:
            lines += ["OBJECTS", *(f"- {t.id}: {t.name}{_look(t.look)}" for t in known.things), ""]
        lines += ["THE STORY SO FAR", _story_so_far(bible, section), ""]
        if before:
            lines += ["JUST BEFORE THE PASSAGE (for context; do not plan it)", before, ""]
        if planned:
            lines += ["THE ONE PICTURE PLANNED FOR THE PASSAGE SO FAR (keep it as the first shot, then go on)"]
            lines += [planned, ""]
        lines.append(f'THE PASSAGE, from "{section.title}"')
        for sentence in passage:
            heading = "(heading) " if sentence.paragraph_id in section.heading_paragraphs else ""
            lines.append(f"[{sentence.order}] {heading}{sentence.text}")
        return "\n".join(lines)

    def _answer_tokens(self, passage: list[SentenceText]) -> int:
        if self.sketch_words == AUTO:
            shots = _auto_shots(passage)
        else:
            shots = len(passage) if self.sketch_words == 1 else _words_in(passage) / self.sketch_words
        return int(PLANNING_TOKENS + SHOT_TOKENS * min(len(passage), max(1.0, shots)))


def marked_book_text(sections: list[SectionText]) -> str:
    """The book's text with each section marked by its id, so a reader can write a note per chapter."""
    return "\n\n".join(f"=== [{section.id}] {section.title} ===\n\n{book_text([section])}" for section in sections)


def _shots(section: SectionText, passage: list[SentenceText], answer: dict, bible: StoryBible) -> list[Scene]:
    """Scenes from a storyboard answer: starts inside the passage, in order, once each; ids from the cast only."""
    orders = [sentence.order for sentence in passage]
    inside = set(orders)
    shots: dict[int, dict] = {}
    for shot in answer.get("shots", []):
        start = shot.get("start")
        if start in inside and start not in shots and shot.get("picture", "").strip():
            shots[start] = shot
    if not shots:
        return []
    if orders[0] not in shots:  # the passage's first picture starts with it
        shots[orders[0]] = shots.pop(min(shots))
    people = {c.id for c in bible.characters}
    places = {p.id for p in bible.locations}
    objects = {t.id for t in bible.things}
    starts = sorted(shots)
    ids = {sentence.order: sentence.id for sentence in passage}
    scenes = []
    for index, start in enumerate(starts):
        last = starts[index + 1] - 1 if index + 1 < len(starts) else orders[-1]
        shot = shots[start]
        picture = shot["picture"].strip()
        scenes.append(
            Scene(
                id="",
                section_id=section.id,
                first_sentence=start,
                last_sentence=last,
                sentence_ids=[ids[order] for order in range(start, last + 1)],
                location_id=shot["place"] if shot.get("place") in places else None,
                character_ids=list(dict.fromkeys(c for c in shot.get("characters", []) if c in people)),
                thing_ids=list(dict.fromkeys(t for t in shot.get("objects", []) if t in objects))[:4],
                action=_clip(picture, 800),
                framing=shot.get("framing") if shot.get("framing") in FRAMINGS else None,
                caption=_clip(shot.get("caption", "").strip() or picture, 300),
                mood=_clip(shot.get("mood", "").strip() or "quiet", 60),
            )
        )
    return scenes


def reading_seconds(text: str) -> float:
    """About how long the site's storyteller voice takes to read this aloud."""
    bengali = len(_BENGALI.findall(text)) * 3 > len(text)
    return len(text) / (BENGALI_CHARACTERS_A_SECOND if bengali else CHARACTERS_A_SECOND)


def _seconds(sentence: SentenceText) -> float:
    return reading_seconds(sentence.text)


def _words(sentence: SentenceText) -> float:
    return len(sentence.text.split())


def _reading_time(sentences: list[SentenceText]) -> float:
    return sum(_seconds(sentence) for sentence in sentences)


def _words_in(sentences: list[SentenceText]) -> float:
    return sum(_words(sentence) for sentence in sentences)


def _auto_shots(passage: list[SentenceText]) -> int:
    """How many pictures Auto shows for a passage: about one every AUTO_SECONDS_EACH, at most one a sentence."""
    return max(1, min(len(passage), round(_reading_time(passage) / AUTO_SECONDS_EACH)))


def _auto_pace(passage: list[SentenceText], exact: bool = False) -> str:
    """Auto's pace for a passage: how many shots, and in its own words (which are longer in some languages)."""
    seconds, words, shots = _reading_time(passage), _words_in(passage), _auto_shots(passage)
    if exact:  # a stretch planned again as several pictures
        shots = max(2, shots)
    time = f"{round(seconds / 60)} minutes" if seconds >= 90 else f"{round(seconds)} seconds"
    each = max(1, round(words / shots))
    most = max(each, round(words * AUTO_SECONDS_MOST / seconds)) if seconds else each
    return AUTO_PACE.format(time=time, shots=shots if exact else f"about {shots}", each=each, most=most)


def _passages(
    sentences: list[SentenceText], limit: float, size: Callable[[SentenceText], float] = _words
) -> list[list[SentenceText]]:
    """Runs of whole sentences of about ``limit`` (words, or as ``size`` measures), ending at a paragraph's end
    where one is near."""
    passages: list[list[SentenceText]] = []
    amount = 0.0
    for sentence in sentences:
        new_paragraph = bool(passages) and sentence.paragraph_id != passages[-1][-1].paragraph_id
        if not passages or amount >= limit * 1.5 or (new_paragraph and amount >= limit):
            passages.append([])
            amount = 0.0
        passages[-1].append(sentence)
        amount += size(sentence)
    if len(passages) > 1 and amount < limit / 4:  # a short end joins the passage before it
        end = passages.pop()
        passages[-1] += end
    return passages


def _tail(passage: list[SentenceText], words: int) -> str:
    kept: list[str] = []
    for sentence in reversed(passage):
        kept.insert(0, sentence.text)
        if sum(len(text.split()) for text in kept) >= words:
            break
    return " ".join(kept)


def _story_so_far(bible: StoryBible, section: SectionText) -> str:
    """The notes on the chapters before this one, the latest that fit."""
    start = section.sentences[0].order if section.sentences else 0
    notes = [note.summary for note in bible.chapters if note.first_sentence < start]
    kept: list[str] = []
    for note in reversed(notes):
        if sum(len(n) for n in kept) + len(note) > STORY_SO_FAR_CHARACTERS:
            break
        kept.insert(0, note)
    if not kept:
        return "The passage is at the start of the story." if not notes else "(earlier chapters left out)"
    return "\n".join(f"- {note}" for note in kept)


def _character(character: Character, order: int) -> str:
    """A cast member as the planner is told of them by this sentence: by name, or (before the story names them)
    by what it calls them, so no caption gives a name away early."""
    kind = [_ROLE.get(character.role, ""), "" if character.gender == "unknown" else character.gender, character.age]
    known = character.known_as(order)
    if known != character.name:
        kind.append("not yet named in the story: call them only this")
    if character.species.lower() != "human":
        kind.append(character.species)
    label = f"{known} ({', '.join(k for k in kind if k)})"
    return f"{label}{_look(character.look)}"


def _look(look: str) -> str:
    return f": {look.rstrip('.')}" if look else ""

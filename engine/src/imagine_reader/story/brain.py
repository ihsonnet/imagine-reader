"""Story brains: whole-book analysis and per-section scene planning.

A brain implements the ``StoryBrain`` contract. ``HeuristicStoryBrain`` uses
plain text rules and no AI: it finds recurring names and groups sentences into
short scenes, a sentence or two each, so the pictures change about as often as
an illustrated film strip. It exists so the pipeline runs without keys; a
language-model brain implements the same contract.
"""

import re
from collections import Counter
from typing import Protocol

from .models import Character, Location, Scene, SectionText, StoryBible
from .names import name_pattern

SKETCH_WORDS = 25  # a new picture every sentence or two, about ten seconds of narration
AUTO = 0  # as a number of words: the story sets the pace
# The text rules' auto pace: a new picture at a new paragraph after this many words, and after the most at any
# sentence end. A language model's storyboard paces by what happens instead.
AUTO_PARAGRAPH_WORDS, AUTO_MOST_WORDS = 40, 90


def words_label(words: int) -> str:
    """How a brain's id names its pace: "w25", or "wauto"."""
    return "auto" if words == AUTO else str(words)


_HONORIFICS = {"Mr", "Mrs", "Miss", "Ms", "Dr", "Sir", "Lady", "Lord", "Captain", "Uncle", "Aunt"}
_PLACE_WORDS = {
    "Point", "Street", "Road", "Lane", "Hall", "House", "Castle", "River", "Hill", "Park", "Bay",
    "Town", "City", "Square", "Bridge", "Island", "Forest", "Wood", "Mountain", "Lake", "Inn",
}  # fmt: skip
_PLACE_PREPOSITIONS = {"at", "in", "to", "from", "near", "toward", "towards", "into", "across", "through"}
_NOT_NAMES = {
    "I", "The", "A", "An", "And", "But", "Or", "So", "Then", "When", "Now", "Here", "There", "This", "That",
    "He", "She", "It", "We", "They", "You", "His", "Her", "My", "Our", "Your", "Their", "Oh", "O", "Yes", "No",
    "Chapter", "God", "Heaven", "Christmas", "Christian", "English", "French", "Sunday", "Monday", "Tuesday",
    "Wednesday", "Thursday", "Friday", "Saturday", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
}  # fmt: skip
_WORD = re.compile(r"[A-Za-z][A-Za-z’'-]*\.?")
_ARTICLE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
_SHE = re.compile(r"\b(she|her|herself)\b", re.IGNORECASE)
_HE = re.compile(r"\b(he|him|his|himself)\b", re.IGNORECASE)
_ANYONE = re.compile(r"\b(she|her|herself|he|him|his|himself|they|them)\b", re.IGNORECASE)
# Bangla pronouns do not say whether they mean a man or a woman: সে, তাহার, তিনি...
_BANGLA_PERSON = re.compile(r"(?<![\w\u0980-\u09FF])(সে|তাহার|তাহাকে|তার|তাকে|তিনি|তাঁহার|তাঁর|তাঁকে|তাঁহাকে)(?![\w\u0980-\u09FF])")
_LOWER_WORD = re.compile(r"\b[a-z][a-z’'-]*")


class StoryBrain(Protocol):
    id: str  # names the brain and its settings; part of every scene plan
    bible_id: str  # names the whole-book analysis, which settings like scene length do not change

    def build_bible(self, sections: list[SectionText]) -> StoryBible: ...

    def plan_scenes(self, section: SectionText, bible: StoryBible) -> list[Scene]: ...


class HeuristicStoryBrain:
    bible_id = "heuristic-4"

    def __init__(self, sketch_words: int = SKETCH_WORDS):
        self.sketch_words = sketch_words  # a scene ends at the first sentence boundary after this many words
        self.id = f"heuristic-4-w{words_label(sketch_words)}"

    def build_bible(self, sections: list[SectionText]) -> StoryBible:
        """Recurring capitalized names; a name is never also an ordinary lowercase word in the book."""
        sentences = [sentence for section in sections for sentence in section.sentences]
        lowercase = {word for sentence in sentences for word in _LOWER_WORD.findall(sentence.text)}
        mid_counts: Counter[str] = Counter()
        totals: Counter[str] = Counter()
        place_votes: Counter[str] = Counter()
        first: dict[str, int] = {}
        for sentence in sentences:
            for name, at_start, preposition in _name_phrases(sentence.text, lowercase):
                totals[name] += 1
                mid_counts[name] += not at_start
                place_votes[name] += preposition
                first.setdefault(name, sentence.order)

        least = 3 if len(sentences) > 2000 else 2  # long books repeat stray capitals more often
        names = [n for n in totals if totals[n] >= least and (mid_counts[n] >= 1 or _has_honorific(n))]
        characters, locations = [], []
        for name in sorted(names, key=lambda n: first[n]):
            if name.split()[-1] in _PLACE_WORDS or place_votes[name] * 2 > totals[name]:
                locations.append(Location(id=f"loc-{len(locations) + 1:03d}", name=name, first_mention=first[name]))
            else:
                characters.append(Character(id=f"chr-{len(characters) + 1:03d}", name=name, first_mention=first[name]))
        return StoryBible(brain=self.bible_id, characters=characters, locations=locations)

    def plan_scenes(self, section: SectionText, bible: StoryBible) -> list[Scene]:
        groups: list[list] = []
        words = 0  # told words in the current scene; a heading joins the scene that follows it
        for sentence in section.sentences:
            if not groups or self._cut(words, sentence.paragraph_id != groups[-1][-1].paragraph_id):
                groups.append([])
                words = 0
            groups[-1].append(sentence)
            if sentence.paragraph_id not in section.heading_paragraphs:
                words += len(sentence.text.split())

        scenes = []
        place = None
        people = {c.id: [c.name, *c.aliases] for c in bible.characters}
        genders = {c.id: c.gender for c in bible.characters}
        protagonist = next((c.id for c in bible.characters if c.role == "protagonist"), None)
        previous: list[str] = []
        for number, group in enumerate(groups, start=1):
            text = " ".join(sentence.text for sentence in group)
            present = _present(text, people)
            if not present:  # "she ran after it": the person the story is following
                carried = [id_ for id_ in previous if _pronoun_fits(text, genders[id_])]
                present = carried or (
                    [protagonist] if protagonist and _pronoun_fits(text, genders[protagonist]) else []
                )
            previous = present or previous
            mentioned = _present(text, {loc.id: [loc.name, *loc.aliases] for loc in bible.locations}, ignore_case=True)
            place = mentioned[-1] if mentioned else place  # a scene stays where the story last was
            things = _present(text, {t.id: [t.name, *t.aliases] for t in bible.things}, ignore_case=True)[:3]
            told = [s for s in group if s.paragraph_id not in section.heading_paragraphs] or group
            scenes.append(
                Scene(
                    id=f"{section.id}-scn-{number:03d}",
                    section_id=section.id,
                    first_sentence=group[0].order,
                    last_sentence=group[-1].order,
                    sentence_ids=[sentence.id for sentence in group],
                    location_id=place,
                    character_ids=present,
                    thing_ids=things,
                    action=_opening(told, 300),
                )
            )
        return scenes

    def _cut(self, words: int, new_paragraph: bool) -> bool:
        """Whether the next sentence starts a new scene, after this many told words."""
        if self.sketch_words == AUTO:
            return words >= AUTO_MOST_WORDS or (new_paragraph and words >= AUTO_PARAGRAPH_WORDS)
        return words >= self.sketch_words


def _name_phrases(text: str, lowercase: set[str]):
    """Capitalized names in a sentence: (name, at sentence start, after a place preposition).

    Names are runs of capitalized words separated only by single spaces, so
    "Estella, Pip" is two names. Contractions ("Don't", "I'll") are skipped,
    possessives lose their "'s", and words the book also uses in lowercase
    ("If", "Well", "Wife") are trimmed from the ends of a run.
    """
    matches = list(_WORD.finditer(text))
    starts = _sentence_starts(text)
    index = 0
    while index < len(matches):
        word = _bare_word(matches[index].group(0))
        joined = index + 1 < len(matches) and text[matches[index].end() : matches[index + 1].start()] == " "
        honorific = word in _HONORIFICS and joined and matches[index + 1].group(0)[:1].isupper()
        if not (honorific or _could_be_name(word)):
            index += 1
            continue
        begin = index
        run = [matches[index].group(0).rstrip(",;:") if honorific else word]  # "Mr." keeps its point; "Miss" has none
        index += 1
        while (
            index < len(matches)
            and text[matches[index - 1].end() : matches[index].start()] == " "
            and _could_be_name(_bare_word(matches[index].group(0)))
            and (  # a full stop or possessive ends a name, except the title's own point: "Mr. Hale"
                (honorific and index - 1 == begin) or not matches[index - 1].group(0).endswith((".", "’s", "'s"))
            )
        ):
            run.append(_bare_word(matches[index].group(0)))
            index += 1
        first_name = 1 if honorific else 0
        while len(run) > first_name and run[first_name].lower() in lowercase:
            run.pop(first_name)
            begin += 1
        while len(run) > first_name and run[-1].lower() in lowercase and run[-1] not in _PLACE_WORDS:
            run.pop()
        if len(run) <= first_name:
            continue
        before = matches[begin - 1].group(0).lower() if begin > 0 else ""
        yield " ".join(run), begin in starts, before in _PLACE_PREPOSITIONS


def _could_be_name(word: str) -> bool:
    return (
        bool(word)
        and word[0].isupper()
        and word not in _NOT_NAMES
        and word not in _HONORIFICS
        and "’" not in word
        and "'" not in word
    )


def _bare_word(token: str) -> str:
    """'Joe’s' -> 'Joe'; 'Point.' -> 'Point'."""
    token = token.rstrip(".")
    for suffix in ("’s", "'s"):
        if token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _sentence_starts(text: str) -> set[int]:
    """Token positions that begin a sentence or a quotation, where any word is capitalized."""
    starts, position = {0}, 0
    for match in _WORD.finditer(text):
        preceding = text[: match.start()].rstrip()
        if preceding.endswith(("“", "‘", '"', ":", ".", "!", "?", "—")):
            starts.add(position)
        position += 1
    return starts


def _has_honorific(name: str) -> bool:
    words = name.split()
    return len(words) > 1 and words[0].rstrip(".") in _HONORIFICS


def _present(text: str, entities: dict[str, list[str]], ignore_case: bool = False) -> list[str]:
    """Ids of the entities a text mentions, longest names first, so "Mrs. Joe" does not also count as "Joe".

    A leading "the", "a", or "an" is not needed to match: "the golden key" matches "a little golden key".
    """
    flags = re.IGNORECASE if ignore_case else 0
    names = sorted(
        ((bare, entity) for entity, names in entities.items() for name in names if (bare := _ARTICLE.sub("", name))),
        key=lambda n: -len(n[0]),
    )
    found = set()
    for name, entity in names:
        pattern = name_pattern(name)
        if re.search(pattern, text, flags):
            found.add(entity)
            text = re.sub(pattern, " ", text, flags=flags)
    return [entity for entity in entities if entity in found]


def _pronoun_fits(text: str, gender: str) -> bool:
    """Whether the text refers to someone by a pronoun that fits this gender."""
    pattern = {"female": _SHE, "male": _HE}.get(gender, _ANYONE)
    return bool(pattern.search(text) or _BANGLA_PERSON.search(text))


def _opening(sentences: list, limit: int) -> str:
    """The scene's first sentences, as many as fit in ``limit`` characters (at least one)."""
    text = sentences[0].text
    for sentence in sentences[1:]:
        if len(text) + 1 + len(sentence.text) > limit:
            break
        text = f"{text} {sentence.text}"
    return _clip(text, limit)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"

"""Where the story begins: past the title page, copyright notes, and contents.

Books often open with pages a listener does not want read aloud: a title page,
publishing and download notes, a table of contents, a preface. ``story_start``
skips leading sections that look like that and returns the first one that
reads like the story itself, such as "Chapter 1". It only ever skips from the
front, never more than a quarter of the book, and falls back to the first
section when nothing looks like front matter.
"""

import re
import statistics
from collections.abc import Sequence

MIN_STORY_WORDS = 60  # a section shorter than this is a title page or a divider, not the story
MAX_FRONT_WORDS = 1500  # publishing notes are short; a longer section with a URL in it is the story
MAX_SKIPPED_SHARE = 0.25  # never skip more than this share of the book's words

_FRONT_TITLE = re.compile(
    r"\b(contents|preface|foreword|introduction|dedication|acknowledg\w*|epigraph|copyright|title page"
    r"|about the (author|book|text)|note on the text|list of illustrations|illustrations)\b",
    re.IGNORECASE,
)
_FRONT_NOTE = re.compile(
    r"copyright|©|\bisbn\b|all rights reserved|published (by|in)\b|printed in\b|first (published|edition)"
    r"|public domain|downloaded from|project gutenberg|wikisource|\be-?books?\b|www\.|https?://"
    r"|translated by|cover (design|art|image|illustration)",
    re.IGNORECASE,
)
_CONTENTS_ENTRY = re.compile(r"\b(chapter|chap\.|part|book)\s+([0-9]+|[ivxlc]+)\b", re.IGNORECASE)


def story_start(sections: Sequence[tuple[str, str]]) -> int:
    """Index of the section where the story begins.

    ``sections`` holds each section's title and the text of its paragraphs
    that are not headings.
    """
    if len(sections) < 2:
        return 0
    words = [len(body.split()) for _, body in sections]
    short_is_front = statistics.median(words) >= 4 * MIN_STORY_WORDS  # not a book of short poems
    total = sum(words) or 1
    skipped = 0
    for index, (title, body) in enumerate(sections):
        if not _front(title, body, words[index], short_is_front):
            return index if skipped / total <= MAX_SKIPPED_SHARE else 0
        skipped += words[index]
    return 0


def _front(title: str, body: str, words: int, short_is_front: bool) -> bool:
    if short_is_front and words < MIN_STORY_WORDS:
        return True
    if _FRONT_TITLE.search(title):
        return True
    if words > MAX_FRONT_WORDS:
        return False
    if len(_CONTENTS_ENTRY.findall(body)) >= 3:  # a table of contents set in body type
        return True
    return bool(_FRONT_NOTE.search(f"{title}\n{body}"))

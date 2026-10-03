"""Sentence segmentation for narration.

Uses spaCy's rule-based sentencizer on a blank English pipeline, so no
language model has to be downloaded. Two adjustments for fiction:

- An opening quotation mark that the sentencizer leaves at the end of the
  previous sentence moves to the sentence it opens.
- A "sentence" that starts with a lowercase word is joined to the one before
  it, which keeps dialogue attributions together:  “Why do you count?” she asked.
"""

from __future__ import annotations

from functools import cache

_OPENERS = "\"'“‘([{"


@cache
def _pipeline():
    import spacy

    nlp = spacy.blank("en")
    nlp.add_pipe("sentencizer")
    return nlp


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of the sentences in ``text``, without surrounding whitespace."""
    raw = [(s.start_char, s.end_char) for s in _pipeline()(text).sents if s.start_char < s.end_char]

    # Openers at the end of a sentence, after whitespace, belong to the next one: `said Hale. “On ...`
    for index in range(len(raw) - 1):
        start, end = raw[index]
        cut = end
        while cut > start and text[cut - 1] in _OPENERS:
            cut -= 1
        if start < cut < end and text[cut - 1].isspace():
            raw[index] = (start, len(text[:cut].rstrip()))
            raw[index + 1] = (cut, raw[index + 1][1])

    spans: list[tuple[int, int]] = []
    for start, end in raw:
        if spans and text[start:end].lstrip(_OPENERS)[:1].islower():
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))
    return spans

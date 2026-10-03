"""Text rules that the generated fixtures do not exercise."""

from collections import Counter

from imagine_reader.ingestion.layout import (
    RawLine,
    RawWord,
    build_lines,
    build_paragraphs,
    classify_margins,
    compose,
    is_heading,
    is_page_number,
)
from imagine_reader.ingestion.sentences import sentence_spans


def line(*texts: str) -> RawLine:
    return RawLine(1, [RawWord(1, text, 0, 0, 1, 1, 12, id=f"w-{text}") for text in texts])


def test_line_end_hyphen_is_joined_by_default():
    paragraph = compose([line("the", "light-"), line("house", "stood")], Counter())
    assert paragraph.text == "the lighthouse stood"
    assert [(edit.kind, edit.printed, edit.reading) for edit in paragraph.edits] == [
        ("hyphen_joined", "light- house", "lighthouse")
    ]


def test_line_end_hyphen_is_kept_for_a_compound_the_book_uses_elsewhere():
    paragraph = compose([line("the", "seventy-"), line("fifth", "step")], Counter({"seventy-fifth": 1}))
    assert paragraph.text == "the seventy-fifth step"
    assert paragraph.edits[0].kind == "hyphen_kept"


def test_line_end_hyphen_is_kept_between_two_words_the_book_never_joins():
    vocabulary = Counter({"nutmeg": 2, "grater": 1})
    assert compose([line("a", "nutmeg-"), line("grater")], vocabulary).text == "a nutmeg-grater"
    assert compose([line("an", "author-"), line("ity")], vocabulary).text == "an authority"


def test_line_end_hyphen_before_a_capital_is_kept():
    assert compose([line("met", "Jean-"), line("Paul", "there")], Counter()).text == "met Jean-Paul there"


def test_word_spans_point_into_the_reading_text():
    paragraph = compose([line("the", "light-"), line("house.")], Counter())
    assert [(word.text, paragraph.text[start:end]) for word, start, end in paragraph.spans] == [
        ("the", "the"),
        ("light-", "light"),
        ("house.", "house."),
    ]


def test_sentences_keep_dialogue_and_abbreviations_intact():
    text = "“So that I know,” said Mr. Hale. “On a dark night, go.” Nora smiled. “I counted,” she said."
    assert [text[start:end] for start, end in sentence_spans(text)] == [
        "“So that I know,” said Mr. Hale.",
        "“On a dark night, go.”",
        "Nora smiled.",
        "“I counted,” she said.",
    ]


def test_page_number_patterns():
    for text in ["12", "- 12 -", "Page 3", "3 of 10", "xiv", "IV"]:
        assert is_page_number(text), text
    for text in ["civil", "Chapter 1", "1984 was a year", "I said"]:
        assert not is_page_number(text), text


def word(text, x0, top, x1, bottom, size=10.0):
    return RawWord(1, text, x0, top, x1, bottom, size)


def test_drop_cap_joins_the_line_beside_it_without_breaking_the_paragraph():
    words = [
        word("M", 50, 100, 72, 128, size=28),  # spans two text lines
        word("y", 75, 100, 80, 110),
        word("father", 82, 100, 110, 110),
        word("name", 75, 112, 100, 122),
        word("was", 50, 124, 66, 134),
    ]
    lines = build_lines(words)
    assert [line.text for line in lines] == ["M y father", "name", "was"]
    assert [line.beside_drop_cap for line in lines] == [False, True, False]
    assert lines[0].top == 100 and lines[0].bottom == 110  # the cap does not stretch the line
    paragraph = compose(lines, Counter({"my": 3}))
    assert paragraph.text == "My father name was"


def test_drop_cap_that_is_a_word_of_its_own_stays_separate():
    words = [
        word("I", 50, 100, 60, 128, size=28),
        word("fully", 63, 100, 85, 110),
        word("expected", 88, 100, 120, 110),
        word("to", 63, 112, 70, 122),
        word("find", 73, 112, 90, 122),
    ]
    assert compose(build_lines(words), Counter()).text == "I fully expected to find"


def test_margin_lines_need_a_gap_and_body_size_to_be_flagged():
    page_lines = [
        [word("Chapter", 50, 10, 90, 30, size=20), word("1", 92, 10, 100, 30, size=20)],
        [word("Text", 50, 60, 70, 70)],
        [word("“Yes.”", 50, 72, 70, 82)],  # bottom zone, but no gap above it
        [word("Footer", 50, 92, 80, 98, size=6)],
    ]
    lines = []
    for page in (1, 2):
        for words in page_lines:
            lines.append(RawLine(page, [RawWord(page, w.text, w.x0, w.top, w.x1, w.bottom, w.size) for w in words]))
    classify_margins(lines, {1: 100, 2: 100})
    assert {(line.text, line.role) for line in lines} == {
        ("Chapter 1", "body"),
        ("Text", "body"),
        ("“Yes.”", "body"),
        ("Footer", "footer"),
    }


def test_a_large_opening_quote_is_a_drop_cap_too():
    words = [
        word("‘", 28, 113, 34, 141, size=28),
        word("MY", 35, 113, 50, 123),
        word("DEAR", 52, 113, 78, 123),
        word("‘I", 45, 125, 50, 135),
        word("write", 52, 125, 73, 135),
        word("that", 31, 137, 50, 147),
    ]
    lines = build_lines(words)
    assert [line.text for line in lines] == ["‘ MY DEAR", "‘I write", "that"]
    assert compose(lines, Counter({"my": 1})).text == "‘MY DEAR ‘I write that"


def test_small_capitals_split_by_font_size_are_one_word():
    # "CHAPTER 1 DOWN" set in small caps: large initials touching smaller letters.
    words = [
        word("C", 100, 10, 118, 35, size=25),
        word("HAPTER", 118, 16, 200, 34, size=17),
        word("1", 207, 10, 220, 35, size=25),
        word("D", 227, 10, 247, 35, size=25),
        word("OWN", 247, 16, 300, 34, size=17),
    ]
    lines = build_lines(words)
    assert [line.text for line in lines] == ["CHAPTER 1 DOWN"]
    assert (lines[0].x0, lines[0].x1, lines[0].size) == (100, 300, 25)


def test_large_small_caps_initials_are_not_drop_caps():
    # "ALICE'S": large "A" and apostrophe touching smaller letters on the same baseline.
    words = [
        word("A", 78, 167, 100, 197, size=30),
        word("LICE", 100, 174, 151, 195, size=21),
        word("’", 151, 167, 159, 197, size=30),
        word("S", 159, 174, 171, 195, size=21),
        word("TALE", 60, 230, 120, 240),
        word("of", 125, 230, 140, 240),
        word("a", 145, 230, 150, 240),
        word("story", 155, 230, 190, 240),
    ]
    assert build_lines(words)[0].text == "ALICE’S"


def placed(text: str, x0: float, x1: float, top: float, size: float = 16.5) -> RawLine:
    """A line in a column from x 50 to 550 on page 1."""
    item = RawLine(1, [RawWord(1, text, x0, top, x1, top + size, size)])
    item.column = (1, 50.0, 550.0)
    return item


def test_a_centered_title_a_little_larger_than_the_text_is_a_heading():
    title = placed("১. ট্রেন কিছুক্ষণের মধ্যেই ছাড়বে", 180, 420, 50, size=19)  # 1.15 times the text
    assert is_heading([title], title.text, 16.5)
    aside = placed("১. ট্রেন কিছুক্ষণের মধ্যেই ছাড়বে", 50, 290, 50, size=19)
    assert not is_heading([aside], aside.text, 16.5)  # not centered: a larger line in the text
    preface = placed("ভূমিকা", 270, 330, 50)
    assert not is_heading([preface], preface.text, 16.5)  # centered, but the size of the text


def test_justified_text_ends_a_paragraph_at_a_line_that_stops_short():
    # Paragraphs neither indented nor spaced apart: full lines reach the right edge, last lines stop short.
    ends = [550, 550, 300, 550, 200, 120, 550, 549]
    lines = [placed(f"line{n}", 50, x1, 100 + 26 * n) for n, x1 in enumerate(ends)]
    assert [[line.text for line in paragraph] for paragraph in build_paragraphs(lines, {1})] == [
        ["line0", "line1", "line2"],
        ["line3", "line4"],
        ["line5"],
        ["line6", "line7"],
    ]


def test_ragged_text_keeps_its_short_lines_in_one_paragraph():
    ends = [520, 480, 505, 450, 530, 470]  # no line reaches the edge: not justified
    lines = [placed(f"line{n}", 50, x1, 100 + 26 * n) for n, x1 in enumerate(ends)]
    assert len(build_paragraphs(lines, {1})) == 1

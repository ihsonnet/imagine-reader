"""Bangla printed by Google Docs or Chrome: its words are read back from the font, not from the PDF's labels."""

from pathlib import Path

import pdfplumber
import pytest

from imagine_reader.ingestion import ingest_pdf, shaped
from imagine_reader.ingestion.shaped import match_glyphs, typing_order

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
PDF = FIXTURES / "pdfs" / "bangla-shaped.pdf"  # made by fixtures/generate_shaped_pdf.py
STORY = [p.strip() for p in (FIXTURES / "stories" / "rater-train.txt").read_text(encoding="utf-8").split("\n\n")]


def test_the_pdfs_own_letters_are_out_of_order_and_missing():
    with pdfplumber.open(PDF) as pdf:
        labelled = pdf.pages[0].extract_text()
    assert "রােতর" in labelled and "\x00" in labelled  # রাতের, with ট্রে drawn but not labelled


def test_a_story_printed_by_chrome_reads_back_exactly_as_typed():
    document = ingest_pdf(PDF)
    assert [paragraph.text for paragraph in document.paragraphs] == [p for p in STORY if p]
    words = [word.text for word in document.words]
    assert "ট্রেন" in words and "ইন্টারেস্টিং!" in words and "কৌতূহলী" in words  # conjuncts drawn in pieces
    assert all(word.box.x1 > word.box.x0 for word in document.words)  # each word keeps its place on the page


def test_without_a_full_font_chromes_subset_keeps_its_own_letters(monkeypatch):
    monkeypatch.setattr(shaped, "REFERENCES", ())  # Chrome's subset has no rules of its own to shape with
    document = ingest_pdf(PDF)
    assert "ট্রেন" not in " ".join(paragraph.text for paragraph in document.paragraphs)


@pytest.mark.parametrize(
    ("drawn", "typed"),
    [
        (["ে", "ক"], "কে"),
        (["ি", "ক্ষ"], "ক্ষি"),
        (["ে", "ক", "া"], "কো"),  # one sign drawn on both sides
        (["ে", "ক", "ৗ"], "কৌ"),
        (["ি", "শ্", "চ"], "শ্চি"),  # a half form and the letter it joins
        (["ি", "ট", "্র"], "ট্রি"),  # and a ra-phala below
        (["ব", "া", "ে", "থ", "র্"], "বার্থে"),  # reph is drawn last, over its letter
        (["দ", "ঁ", "া"], "দাঁ"),  # chandrabindu sits over the consonant, after the sign in typing
    ],
)
def test_letters_drawn_in_order_are_put_in_typing_order(drawn, typed):
    assert typing_order(drawn) == typed


def test_twin_glyphs_follow_the_numbering_of_their_neighbours():
    # From glyph 2 on, the full font numbers its glyphs one higher; glyphs 3 and 4 have the same shape ("t").
    numbers = {"a": [0], "b": [1], "c": [3], "t": [4, 5], "d": [6]}
    assert match_glyphs(["a", "b", "c", "t", "t", "d"], numbers) == {0: 0, 1: 1, 2: 3, 3: 4, 4: 5, 5: 6}

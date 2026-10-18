"""Bangla books: the Devdas sample reads in logical order, splits at the dari, and its names are found."""

import re
from pathlib import Path

import pytest

from imagine_reader.ingestion import ingest_pdf, story_start
from imagine_reader.narration import narration_style
from imagine_reader.story import bible_from_cast, sections_from_document
from imagine_reader.story.names import name_pattern

SAMPLE = Path(__file__).resolve().parents[2] / "server" / "src" / "imagine_reader_server" / "samples" / "devdas.pdf"


@pytest.fixture(scope="module")
def document():
    return ingest_pdf(SAMPLE)


def test_the_text_comes_out_as_written_and_splits_at_the_dari(document):
    texts = [sentence.narration_text for sentence in document.sentences]
    opening = next(text for text in texts if text.startswith("একদিন"))
    assert opening == "একদিন বৈশাখের দ্বিপ্রহরে রৌদ্রেরও অন্ত ছিল না উত্তাপেরও সীমা ছিল না।"
    assert any("পাঠশালা-ঘরের" in text for text in texts)  # conjuncts, vowel signs, and hyphens intact
    assert "\x00" not in " ".join(texts)


def test_it_opens_at_the_first_chapter_and_reads_as_a_story(document):
    sections = [
        (s.title, " ".join(p.text for p in document.paragraphs[s.first_paragraph : s.end_paragraph] if not p.heading))
        for s in document.sections
    ]
    assert [title for title, _ in sections] == ["দেবদাস · শরৎচন্দ্র চট্টোপাধ্যায়", "প্রথম পরিচ্ছেদ", "দ্বিতীয় পরিচ্ছেদ"]
    assert story_start(sections) == 1
    assert narration_style([s.narration_text for s in document.sentences]) == "story"  # কহিল, বলিল


def test_bangla_names_match_with_their_endings_and_end_in_vowel_signs():
    text = "পার্বতীর মা ডাকিল। দেবদাসকে মারিল। পারুল আসিল।"
    assert re.search(name_pattern("পার্বতী"), text)  # ends in a vowel sign, with "র" after it
    assert re.search(name_pattern("দেবদাস"), text)  # with "কে" after it
    assert not re.search(name_pattern("পারু"), "পারুল আসিল।")  # another name, not পারু with an ending


def test_bangla_characters_are_kept_without_capital_letters(document):
    cast = {
        "setting": "Rural Bengal, early 1900s.",
        "characters": [
            {"name": "দেবদাস", "aliases": ["দেবা"], "role": "protagonist", "gender": "male", "age": "about 12",
             "species": "human", "look": "A slim boy in a white dhoti.", "look_from_text": False},
            {"name": "পার্ব্বতী", "aliases": ["পারু"], "role": "main", "gender": "female", "age": "about 8",
             "species": "human", "look": "A small girl with a loose braid.", "look_from_text": False},
        ],
        "things": [{"name": "শ্লেট", "aliases": [], "look": "A small slate.", "look_from_text": False}],
        "places": [],
    }  # fmt: skip
    bible = bible_from_cast(cast, sections_from_document(document), "cast-test")
    names = {c.name: c for c in bible.characters}
    assert set(names) == {"দেবদাস", "পার্ব্বতী"} and names["পার্ব্বতী"].aliases == ["পারু"]
    assert names["পার্ব্বতী"].first_mention > names["দেবদাস"].first_mention
    assert [t.name for t in bible.things] == ["শ্লেট"]


def test_a_cover_picture_alone_is_not_reported_as_needing_ocr(document):
    assert document.pages[0].method == "needs_ocr"  # the title page is a scan
    assert not any("needs OCR" in warning for warning in document.warnings)


def test_the_kabuliwala_sample_opens_at_the_story_and_reads_to_its_last_line():
    document = ingest_pdf(SAMPLE.with_name("kabuliwala.pdf"))
    sections = [
        (s.title, " ".join(p.text for p in document.paragraphs[s.first_paragraph : s.end_paragraph] if not p.heading))
        for s in document.sections
    ]
    assert [title for title, _ in sections] == ["কাবুলিওয়ালা · রবীন্দ্রনাথ ঠাকুর", "কাবুলিওয়ালা"]
    assert story_start(sections) == 1
    texts = [sentence.narration_text for sentence in document.sentences]
    assert "রহমত একটা অনাবশ্যক চন্দ্রবিন্দু যোগ করিয়া হাসিতে হাসিতে উত্তর করিত, “হাঁতি।”" in texts  # one ঁ, as printed
    assert texts[-1] == (
        "যেমন মনে করিয়াছিলাম তেমন করিয়া ইলেকট্রিক আলো জ্বালাইতে পারিলাম না, গড়ের বাদ্যও আসিল না, "
        "অন্তঃপুরে মেয়েরা অত্যন্ত অসন্তোষ প্রকাশ করিতে লাগিলেন, কিন্তু মঙ্গল-আলোকে আমার শুভ উৎসব উজ্জ্বল হইয়া উঠিল।"
    )

"""Extraction checked against the ground truth written by fixtures/generate_pdfs.py."""

import importlib.util
import json
import sys
from functools import cache
from pathlib import Path

import pytest

from imagine_reader.ingestion import ScannedPdfError, ingest_pdf

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
PDFS = FIXTURES / "pdfs"
TEXT_VARIANTS = ["single-column", "two-column", "rotated-landscape", "cropped", "book-page"]


@cache
def extracted(name: str):
    return ingest_pdf(PDFS / f"{name}.pdf")


@cache
def truth(name: str) -> dict:
    return json.loads((PDFS / f"{name}.truth.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", TEXT_VARIANTS)
def test_paragraph_text_matches_the_source_story(name):
    assert [paragraph.text for paragraph in extracted(name).paragraphs] == truth(name)["paragraphs"]


@pytest.mark.parametrize("name", TEXT_VARIANTS)
def test_body_lines_follow_reading_order_at_printed_positions(name):
    expected = [line for line in truth(name)["lines"] if line["role"] in ("title", "body")]
    body = [line for line in extracted(name).lines if line.role == "body"]
    assert [line.text for line in body] == [line["text"] for line in expected]
    for line, want in zip(body, expected, strict=True):
        assert line.box.page == want["page"]
        assert line.box.x0 == pytest.approx(want["x0"], abs=0.003)
        assert line.box.x1 == pytest.approx(want["x1"], abs=0.003)
        assert line.box.top < want["baseline"] < line.box.bottom


@pytest.mark.parametrize("name", TEXT_VARIANTS)
def test_running_headers_footers_and_page_numbers_are_flagged_not_narrated(name):
    expected = sorted(
        (line["page"], line["role"], line["text"])
        for line in truth(name)["lines"]
        if line["role"] in ("header", "footer", "page_number")
    )
    flagged = sorted((line.page, line.role, line.text) for line in extracted(name).lines if line.role != "body")
    assert flagged == expected


@pytest.mark.parametrize("name", TEXT_VARIANTS)
def test_hyphenated_line_ends_are_rejoined_and_recorded(name):
    split_lines = sum(1 for line in truth(name)["lines"] if line["role"] == "body" and line["text"].endswith("-"))
    edits = [edit for paragraph in extracted(name).paragraphs for edit in paragraph.edits]
    hyphen_edits = [edit for edit in edits if edit.kind.startswith("hyphen")]
    assert len(hyphen_edits) == split_lines
    assert all(edit.kind == "hyphen_joined" for edit in hyphen_edits)


@pytest.mark.parametrize("name", TEXT_VARIANTS)
def test_every_body_word_is_narrated_exactly_once_in_order(name):
    document = extracted(name)
    body_lines = {line.id for line in document.lines if line.role == "body"}
    body_words = [word.id for word in document.words if word.line_id in body_lines]
    assert [word_id for sentence in document.sentences for word_id in sentence.word_ids] == body_words


@pytest.mark.parametrize("name", TEXT_VARIANTS)
def test_sentences_rebuild_their_paragraphs(name):
    document = extracted(name)
    for paragraph in document.paragraphs:
        sentences = [s.narration_text for s in document.sentences if s.paragraph_id == paragraph.id]
        assert " ".join(sentences) == paragraph.text


@pytest.mark.parametrize("name", TEXT_VARIANTS)
def test_sentence_highlights_cover_their_words(name):
    document = extracted(name)
    words = {word.id: word.box for word in document.words}
    for sentence in document.sentences:
        for word_id in sentence.word_ids:
            word = words[word_id]
            assert any(
                box.page == word.page
                and box.x0 <= word.x0
                and word.x1 <= box.x1
                and box.top <= word.top
                and word.bottom <= box.bottom
                for box in sentence.boxes
            ), (sentence.id, word_id)


def test_drop_cap_rejoins_its_word_and_keeps_the_paragraph_whole():
    document = extracted("book-page")
    assert document.paragraphs[1].text.startswith("Every evening, just before the sun")
    edits = [edit for edit in document.paragraphs[1].edits if edit.kind == "drop_cap_joined"]
    assert [(edit.printed, edit.reading) for edit in edits] == [("E very", "Every")]


@pytest.mark.parametrize("name", TEXT_VARIANTS)
def test_the_story_title_opens_the_only_section(name):
    document = extracted(name)
    assert [(s.title, s.first_paragraph, s.end_paragraph, s.page) for s in document.sections] == [
        ("The Lantern Keeper", 0, len(document.paragraphs), 1)
    ]
    assert [p.heading for p in document.paragraphs] == [True] + [False] * (len(document.paragraphs) - 1)


def test_dialogue_keeps_quotes_and_attributions_together():
    sentences = [sentence.narration_text for sentence in extracted("two-column").sentences]
    assert "“Why do you count?” she asked one evening." in sentences
    assert "“So that I always know where I am,” said Mr. Hale." in sentences
    assert "“On a dark night, a man can lose his way even on his own staircase.”" in sentences
    assert "“I counted,” she said." in sentences


def test_a_scanned_page_is_refused_as_a_scan():
    with pytest.raises(ScannedPdfError) as caught:
        ingest_pdf(PDFS / "scanned-page.pdf")
    assert caught.value.code == "scanned_pdf"


def test_extraction_is_deterministic():
    data = (PDFS / "two-column.pdf").read_bytes()
    assert ingest_pdf(data).model_dump_json() == ingest_pdf(data).model_dump_json()


def test_committed_truth_files_match_the_generator():
    spec = importlib.util.spec_from_file_location("generate_pdfs", FIXTURES / "generate_pdfs.py")
    generator = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = generator  # dataclasses look up their module while the script loads
    spec.loader.exec_module(generator)
    title, paragraphs = generator.read_story(generator.STORY)
    for variant in generator.VARIANTS:
        placed, pages = generator.layout(variant, title, paragraphs)
        regenerated = json.loads(json.dumps(generator.truth(variant, title, paragraphs, placed, pages)))
        assert regenerated == truth(variant.name), f"re-run fixtures/generate_pdfs.py ({variant.name})"

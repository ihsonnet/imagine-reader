"""Upload validation: wrong file types, encryption, damage, and size limits."""

import io
from pathlib import Path

import pytest
from PIL import Image
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from imagine_reader.ingestion import (
    DamagedPdfError,
    EncryptedPdfError,
    LimitExceededError,
    Limits,
    NotAPdfError,
    ScannedPdfError,
    ingest_pdf,
)


def make_pdf(pages: int = 1, text: str = "Hello there.", encrypt=None) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, encrypt=encrypt, invariant=1)
    for _ in range(pages):
        pdf.drawString(72, 720, text)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def test_rejects_files_that_are_not_pdfs():
    with pytest.raises(NotAPdfError):
        ingest_pdf(b"PK\x03\x04 this is a zip archive")


def test_rejects_password_protected_pdfs():
    with pytest.raises(EncryptedPdfError):
        ingest_pdf(make_pdf(encrypt=StandardEncryption("secret", ownerPassword="owner")))


def test_accepts_pdfs_restricted_only_by_an_owner_password():
    document = ingest_pdf(make_pdf(encrypt=StandardEncryption("", ownerPassword="owner")))
    assert [sentence.narration_text for sentence in document.sentences] == ["Hello there."]


def test_rejects_damaged_pdfs():
    data = make_pdf()
    with pytest.raises(DamagedPdfError):
        ingest_pdf(data[: len(data) // 2])


@pytest.mark.parametrize(
    ("limits", "exceeded"),
    [(Limits(max_bytes=100), "bytes"), (Limits(max_pages=2), "pages"), (Limits(max_words=5), "words")],
)
def test_enforces_upload_limits(limits, exceeded):
    with pytest.raises(LimitExceededError) as caught:
        ingest_pdf(make_pdf(pages=3), limits)
    assert caught.value.limit == exceeded


def test_paragraphs_never_join_across_an_unreadable_page():
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, invariant=1)
    pdf.drawString(72, 720, "The story continues on the next")
    pdf.showPage()
    pdf.drawImage(ImageReader(Image.new("L", (200, 200), 255)), 72, 400, 200, 200)
    pdf.showPage()
    pdf.drawString(72, 720, "page after the picture.")
    pdf.showPage()
    pdf.save()

    document = ingest_pdf(buffer.getvalue())
    assert [(section.title, section.first_paragraph) for section in document.sections] == [("Beginning", 0)]
    assert [page.method for page in document.pages] == ["native", "needs_ocr", "native"]
    assert [paragraph.text for paragraph in document.paragraphs] == [
        "The story continues on the next",
        "page after the picture.",
    ]


def pictures_only(pages: int) -> bytes:
    """A scanned book: each page a picture, with no text layer."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, invariant=1)
    for _ in range(pages):
        pdf.drawImage(ImageReader(Image.new("L", (200, 200), 255)), 72, 400, 200, 200)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def test_a_scan_is_refused_as_a_scan_even_when_it_is_too_long():
    with pytest.raises(ScannedPdfError):
        ingest_pdf(pictures_only(3), Limits(max_pages=2))  # its first pages are read before its length is refused
    with pytest.raises(ScannedPdfError):
        ingest_pdf(pictures_only(3), Limits(max_pages=2), trim=True)


def test_a_cover_picture_or_an_illustration_does_not_make_a_book_a_scan():
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, invariant=1)
    pdf.drawImage(ImageReader(Image.new("L", (200, 200), 255)), 72, 400, 200, 200)  # the cover
    pdf.showPage()
    pdf.drawString(72, 720, "Once upon a time.")
    pdf.showPage()
    pdf.save()
    document = ingest_pdf(buffer.getvalue())
    assert [page.method for page in document.pages] == ["needs_ocr", "native"]  # half the pages: still a book


def test_a_long_pdf_can_be_trimmed_to_the_limits():
    from imagine_reader.ingestion import Limits, ingest_pdf

    fixtures = Path(__file__).resolve().parents[2] / "fixtures" / "pdfs"
    data = (fixtures / "two-column.pdf").read_bytes()
    with pytest.raises(LimitExceededError) as caught:
        ingest_pdf(data, Limits(max_pages=1))
    assert caught.value.code == "too_many_pages"
    first_page = ingest_pdf(data, Limits(max_pages=1), trim=True)
    assert len(first_page.pages) == 1 and first_page.warnings[0] == (
        "Only the first 1 of 2 pages were read, to stay within the reader's limits."
    )
    whole = ingest_pdf(data)
    words_on_page_1 = sum(1 for word in whole.words if word.box.page == 1)
    by_words = ingest_pdf(data, Limits(max_words=words_on_page_1 + 5), trim=True)
    assert len(by_words.pages) == 1  # the second page would pass the word limit

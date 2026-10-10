"""Build the plain section texts that story brains read."""

from ..ingestion.models import ExtractedDocument
from .models import SectionText, SentenceText


def sections_from_document(document: ExtractedDocument) -> list[SectionText]:
    sections = []
    for section in document.sections:
        paragraphs = document.paragraphs[section.first_paragraph : section.end_paragraph]
        ids = {paragraph.id for paragraph in paragraphs}
        sections.append(
            SectionText(
                id=section.id,
                title=section.title,
                sentences=[
                    SentenceText(s.id, s.order, s.paragraph_id, s.narration_text)
                    for s in document.sentences
                    if s.paragraph_id in ids
                ],
                heading_paragraphs={paragraph.id for paragraph in paragraphs if paragraph.heading},
            )
        )
    return sections

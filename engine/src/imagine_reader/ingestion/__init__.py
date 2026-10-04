"""PDF ingestion: validated upload in, source-linked paragraphs and sentences out."""

from .errors import (
    DamagedPdfError,
    EncryptedPdfError,
    IngestionError,
    LimitExceededError,
    NotAPdfError,
    ScannedPdfError,
)
from .front_matter import story_start
from .models import ExtractedDocument
from .pdf import EXTRACTOR_VERSION, Limits, ingest_pdf

__all__ = [
    "EXTRACTOR_VERSION",
    "DamagedPdfError",
    "EncryptedPdfError",
    "ExtractedDocument",
    "IngestionError",
    "Limits",
    "LimitExceededError",
    "NotAPdfError",
    "ScannedPdfError",
    "ingest_pdf",
    "story_start",
]

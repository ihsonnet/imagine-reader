"""Errors raised when an uploaded file cannot be ingested.

Each error has a stable ``code`` so the API can show a clear message without
parsing exception text.
"""


class IngestionError(Exception):
    code = "ingestion_failed"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotAPdfError(IngestionError):
    code = "not_a_pdf"


class EncryptedPdfError(IngestionError):
    code = "encrypted_pdf"


class DamagedPdfError(IngestionError):
    code = "damaged_pdf"


class ScannedPdfError(IngestionError):
    """Most pages are pictures of text, with no text layer to read: a scanned book, until there is OCR."""

    code = "scanned_pdf"


class LimitExceededError(IngestionError):
    code = "limit_exceeded"

    def __init__(self, message: str, limit: str, value: int, maximum: int):
        super().__init__(message)
        self.code = f"too_many_{limit}"  # too_many_bytes, too_many_pages, too_many_words
        self.limit = limit
        self.value = value
        self.maximum = maximum

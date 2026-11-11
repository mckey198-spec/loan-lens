"""Document-type extractors (deterministic regex/heuristics; no LLM)."""

from __future__ import annotations

from modules.extraction.parsers.bank_statement import extract_bank_statement
from modules.extraction.parsers.pan import extract_pan_card
from modules.extraction.parsers.salary_slip import extract_salary_slip
from modules.extraction.schemas import DocumentExtractionResult, ExtractionError

__all__ = [
    "ExtractionError",
    "DocumentExtractionResult",
    "extract_bank_statement",
    "extract_document",
    "extract_pan_card",
    "extract_salary_slip",
]


def extract_document(
    document_type: str,
    text: str,
) -> DocumentExtractionResult:
    """Dispatch to the parser for a supported classified document type."""
    if document_type == "pan_card":
        return extract_pan_card(text)
    if document_type == "salary_slip":
        return extract_salary_slip(text)
    if document_type == "bank_statement":
        return extract_bank_statement(text)
    raise ExtractionError(
        code="extraction_unsupported_type",
        message=f"No extractor registered for document_type={document_type!r}",
        details={"document_type": document_type},
    )

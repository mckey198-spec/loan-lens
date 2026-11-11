"""Document extraction engine (M07-T03)."""

from modules.extraction.parsers import extract_document
from modules.extraction.schemas import (
    BankStatementMonthlySummary,
    BankStatementSummary,
    DocumentExtractionResult,
    ExtractedFieldResult,
    ExtractionError,
    SalarySlipExtraction,
)
from modules.extraction.service import (
    record_document_extraction,
    record_extraction_failure,
)

__all__ = [
    "BankStatementMonthlySummary",
    "BankStatementSummary",
    "DocumentExtractionResult",
    "ExtractedFieldResult",
    "ExtractionError",
    "SalarySlipExtraction",
    "extract_document",
    "record_document_extraction",
    "record_extraction_failure",
]

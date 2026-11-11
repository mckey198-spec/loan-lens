"""PAN card field extractor (regex-validated; no external verification)."""

from __future__ import annotations

import re

from decimal import Decimal

from modules.extraction.parsers._text import (
    bbox_for_span,
    date_normalized,
    parse_flexible_date,
    require_nonempty_text,
    text_normalized,
)
from modules.extraction.schemas import (
    DocumentExtractionResult,
    ExtractedFieldResult,
    ExtractionError,
)

EXTRACTOR_NAME = "pan_card_regex_v1"

_PAN_RE = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b")
_NAME_RE = re.compile(
    r"(?:Name|Applicant\s*Name)\s*[:\-]?\s*(?P<value>[A-Za-z][A-Za-z .']{1,80})",
    re.IGNORECASE,
)
_DOB_RE = re.compile(
    r"(?:Date\s*of\s*Birth|DOB|D\.O\.B\.?)\s*[:\-]?\s*"
    r"(?P<value>\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def extract_pan_card(text: str) -> DocumentExtractionResult:
    """
    Extract ``pan``, ``applicant_name``, and ``date_of_birth``.

    ``pan`` must match ``^[A-Z]{5}[0-9]{4}[A-Z]$`` (PRD format check only).
    """
    try:
        body = require_nonempty_text(text)
    except ValueError as exc:
        raise ExtractionError(
            code="extraction_unreadable",
            message=str(exc),
        ) from exc

    upper = body.upper()
    pan_match = _PAN_RE.search(upper)
    if pan_match is None:
        raise ExtractionError(
            code="extraction_failed",
            message="PAN number not found or failed regex validation",
            details={"field": "pan"},
        )

    pan_value = pan_match.group(1)
    # Map uppercase match offsets back onto original body where possible.
    raw_pan_match = re.search(re.escape(pan_value), body, re.IGNORECASE)
    pan_start = raw_pan_match.start() if raw_pan_match else pan_match.start(1)
    pan_end = raw_pan_match.end() if raw_pan_match else pan_match.end(1)
    raw_pan = body[pan_start:pan_end]

    fields: list[ExtractedFieldResult] = [
        ExtractedFieldResult(
            field_name="pan",
            raw_value=raw_pan,
            normalized_value=text_normalized(pan_value),
            confidence=Decimal("0.9800"),
            page_number=1,
            bbox=bbox_for_span(body, pan_start, pan_end),
        )
    ]

    name_match = _NAME_RE.search(body)
    if name_match is None:
        raise ExtractionError(
            code="extraction_failed",
            message="Applicant name not found on PAN card text",
            details={"field": "applicant_name"},
        )
    raw_name = name_match.group("value").strip()
    fields.append(
        ExtractedFieldResult(
            field_name="applicant_name",
            raw_value=raw_name,
            normalized_value=text_normalized(raw_name),
            confidence=Decimal("0.9200"),
            page_number=1,
            bbox=bbox_for_span(
                body,
                name_match.start("value"),
                name_match.end("value"),
            ),
        )
    )

    dob_match = _DOB_RE.search(body)
    if dob_match is None:
        raise ExtractionError(
            code="extraction_failed",
            message="Date of birth not found on PAN card text",
            details={"field": "date_of_birth"},
        )
    raw_dob = dob_match.group("value").strip()
    try:
        dob = parse_flexible_date(raw_dob)
    except ValueError as exc:
        raise ExtractionError(
            code="extraction_failed",
            message=f"Date of birth could not be normalized: {raw_dob}",
            details={"field": "date_of_birth"},
        ) from exc
    fields.append(
        ExtractedFieldResult(
            field_name="date_of_birth",
            raw_value=raw_dob,
            normalized_value=date_normalized(dob),
            confidence=Decimal("0.9000"),
            page_number=1,
            bbox=bbox_for_span(
                body,
                dob_match.start("value"),
                dob_match.end("value"),
            ),
        )
    )

    return DocumentExtractionResult(
        extractor_name=EXTRACTOR_NAME,
        fields=fields,
        raw_payload={"document_type": "pan_card", "field_count": len(fields)},
    )

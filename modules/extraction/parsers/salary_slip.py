"""Salary slip structured extractor."""

from __future__ import annotations

import re
from decimal import Decimal

from modules.extraction.parsers._text import (
    bbox_for_span,
    find_amount_after_label,
    first_labeled_match,
    money_normalized,
    parse_flexible_date,
    require_nonempty_text,
    text_normalized,
)
from modules.extraction.schemas import (
    DocumentExtractionResult,
    ExtractedFieldResult,
    ExtractionError,
    SalarySlipExtraction,
)

EXTRACTOR_NAME = "salary_slip_regex_v1"

_EMPLOYER_LABELS = ("Employer Name", "Employer", "Company Name", "Organisation", "Organization")
_PERIOD_LABELS = ("Pay Period", "Pay Period Month", "Salary Month", "For the Month", "Month")
_BASIC_LABELS = ("Basic Pay", "Basic Salary", "Basic")
_ALLOWANCE_LABELS = ("Allowances", "Total Allowances", "Other Allowances")
_DEDUCTION_LABELS = ("Deductions", "Total Deductions")
_GROSS_LABELS = ("Gross Salary", "Gross Pay", "Gross Earnings", "Gross")
_NET_LABELS = ("Net Salary", "Net Pay", "Net Earnings", "Take Home")
_PAN_RE = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b")


def extract_salary_slip(text: str) -> DocumentExtractionResult:
    """
    Extract employer, pay period, gross/net, and breakdown amounts.

    Breakdown (``basic_pay``, ``allowances``, ``deductions``) is stored on the
    structured ``salary_slip_extractions`` row; key amounts also land as
    ``document_extracted_fields`` for lineage.
    """
    try:
        body = require_nonempty_text(text)
    except ValueError as exc:
        raise ExtractionError(
            code="extraction_unreadable",
            message=str(exc),
        ) from exc

    employer_match = first_labeled_match(
        body,
        _EMPLOYER_LABELS,
        r"[^\n\r]{2,120}",
    )
    if employer_match is None:
        raise ExtractionError(
            code="extraction_failed",
            message="Employer name not found on salary slip",
            details={"field": "employer_name"},
        )
    raw_employer = employer_match.group("value").strip()

    period_match = first_labeled_match(
        body,
        _PERIOD_LABELS,
        r"[A-Za-z]+\s+\d{4}|\d{4}-\d{2}(?:-\d{2})?|\d{1,2}[/\-.]\d{4}",
    )
    if period_match is None:
        raise ExtractionError(
            code="extraction_failed",
            message="Pay period month not found on salary slip",
            details={"field": "pay_period_month"},
        )
    raw_period = period_match.group("value").strip()
    try:
        pay_period = parse_flexible_date(raw_period)
        pay_period_month = pay_period.replace(day=1)
    except ValueError as exc:
        raise ExtractionError(
            code="extraction_failed",
            message=f"Pay period could not be normalized: {raw_period}",
            details={"field": "pay_period_month"},
        ) from exc

    basic = find_amount_after_label(body, _BASIC_LABELS)
    allowances = find_amount_after_label(body, _ALLOWANCE_LABELS)
    deductions = find_amount_after_label(body, _DEDUCTION_LABELS)
    gross = find_amount_after_label(body, _GROSS_LABELS)
    net = find_amount_after_label(body, _NET_LABELS)

    if net is None:
        raise ExtractionError(
            code="extraction_failed",
            message="Net salary not found on salary slip",
            details={"field": "net_salary"},
        )

    basic_pay = basic[1] if basic is not None else None
    allowance_amount = allowances[1] if allowances is not None else None
    deduction_amount = deductions[1] if deductions is not None else None
    net_pay = net[1]

    if gross is None and basic_pay is not None:
        computed = basic_pay + (allowance_amount or Decimal("0.00"))
        gross_raw = f"{computed:.2f}"
        gross_amount = computed
        gross_span: tuple[int, int] | None = None
    elif gross is not None:
        gross_raw, gross_amount, g_start, g_end = gross
        gross_span = (g_start, g_end)
    else:
        raise ExtractionError(
            code="extraction_failed",
            message="Gross salary not found and could not be derived",
            details={"field": "gross_salary"},
        )

    fields: list[ExtractedFieldResult] = [
        ExtractedFieldResult(
            field_name="employer_name",
            raw_value=raw_employer,
            normalized_value=text_normalized(raw_employer),
            confidence=Decimal("0.9400"),
            page_number=1,
            bbox=bbox_for_span(
                body,
                employer_match.start("value"),
                employer_match.end("value"),
            ),
        ),
        ExtractedFieldResult(
            field_name="gross_salary",
            raw_value=gross_raw,
            normalized_value=money_normalized(gross_amount),
            confidence=Decimal("0.9300") if gross is not None else Decimal("0.8500"),
            page_number=1,
            bbox=(
                bbox_for_span(body, gross_span[0], gross_span[1])
                if gross_span is not None
                else None
            ),
        ),
        ExtractedFieldResult(
            field_name="net_salary",
            raw_value=net[0],
            normalized_value=money_normalized(net_pay),
            confidence=Decimal("0.9500"),
            page_number=1,
            bbox=bbox_for_span(body, net[2], net[3]),
        ),
    ]

    employee_pan: str | None = None
    pan_match = _PAN_RE.search(body.upper())
    if pan_match is not None:
        employee_pan = pan_match.group(1)

    salary_data = SalarySlipExtraction(
        employer_name=raw_employer,
        pay_period_month=pay_period_month,
        basic_pay=basic_pay,
        allowances=allowance_amount,
        deductions=deduction_amount,
        net_pay=net_pay,
        employee_pan=employee_pan,
    )

    return DocumentExtractionResult(
        extractor_name=EXTRACTOR_NAME,
        fields=fields,
        salary_slip_data=salary_data,
        raw_payload={
            "document_type": "salary_slip",
            "pay_period_raw": raw_period,
            "pay_period_month": pay_period_month.isoformat(),
            "breakdown": {
                "basic_pay": None if basic_pay is None else f"{basic_pay:.2f}",
                "allowances": (
                    None if allowance_amount is None else f"{allowance_amount:.2f}"
                ),
                "deductions": (
                    None if deduction_amount is None else f"{deduction_amount:.2f}"
                ),
            },
        },
    )

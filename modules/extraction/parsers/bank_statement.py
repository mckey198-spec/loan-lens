"""Bank statement metadata and monthly summary extractor."""

from __future__ import annotations

import re
from decimal import Decimal

from modules.extraction.parsers._text import (
    bbox_for_span,
    date_normalized,
    find_amount_after_label,
    first_labeled_match,
    money_normalized,
    parse_amount,
    parse_flexible_date,
    require_nonempty_text,
    text_normalized,
)
from modules.extraction.schemas import (
    BankStatementMonthlySummary,
    DocumentExtractionResult,
    ExtractedFieldResult,
    ExtractionError,
)

EXTRACTOR_NAME = "bank_statement_regex_v1"

_ACCOUNT_LABELS = ("Account Number", "Account No", "A/C No", "A/c Number")
_IFSC_LABELS = ("IFSC", "IFSC Code")
_START_LABELS = ("Statement Start Date", "From Date", "Period From", "Statement From")
_END_LABELS = ("Statement End Date", "To Date", "Period To", "Statement To")
_AMB_LABELS = ("Average Monthly Balance", "AMB", "Avg Monthly Balance")
_CREDITS_LABELS = ("Total Credits", "Total Credit", "Credits")
_DEBITS_LABELS = ("Total Debits", "Total Debit", "Debits")
_CLOSING_LABELS = ("Closing Balance", "Closing Bal", "Balance Carried Forward")
_SALARY_LABELS = ("Identified Salary Credits", "Salary Credits", "Salary Credit")
_MONTH_LABELS = ("Statement Month", "Month")
_PERIOD_RE = re.compile(
    r"(?:Statement\s+Period|Period)\s*[:\-]?\s*"
    r"(?P<start>\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}-\d{2}-\d{2})"
    r"\s*(?:to|-|–)\s*"
    r"(?P<end>\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
_IFSC_VALUE_RE = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b")
_AMOUNT_TOKEN = r"[₹Rs.\s]*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
_MONTH_BLOCK_RE = re.compile(
    r"(?:Month|Statement\s+Month)\s*[:\-]?\s*(?P<month>\d{4}-\d{2}|\w+\s+\d{4})"
    r".*?"
    rf"(?:Total\s+Credits?)\s*[:\-]?\s*(?P<credits>{_AMOUNT_TOKEN})"
    r".*?"
    rf"(?:Total\s+Debits?)\s*[:\-]?\s*(?P<debits>{_AMOUNT_TOKEN})"
    r".*?"
    rf"(?:Closing\s+Bal(?:ance)?)\s*[:\-]?\s*(?P<closing>-?{_AMOUNT_TOKEN})",
    re.IGNORECASE | re.DOTALL,
)


def extract_bank_statement(text: str) -> DocumentExtractionResult:
    """
    Extract account metadata fields and monthly summary lines.

    Monthly rows include ``total_credits``, ``total_debits``, ``closing_balance``,
    and ``identified_salary_credits``.
    """
    try:
        body = require_nonempty_text(text)
    except ValueError as exc:
        raise ExtractionError(
            code="extraction_unreadable",
            message=str(exc),
        ) from exc

    fields: list[ExtractedFieldResult] = []

    account_match = first_labeled_match(
        body,
        _ACCOUNT_LABELS,
        r"[0-9X*]{6,20}",
    )
    if account_match is None:
        raise ExtractionError(
            code="extraction_failed",
            message="Account number not found on bank statement",
            details={"field": "account_number"},
        )
    raw_account = account_match.group("value").strip()
    fields.append(
        ExtractedFieldResult(
            field_name="account_number",
            raw_value=raw_account,
            normalized_value=text_normalized(raw_account),
            confidence=Decimal("0.9400"),
            page_number=1,
            bbox=bbox_for_span(
                body,
                account_match.start("value"),
                account_match.end("value"),
            ),
        )
    )

    ifsc_match = first_labeled_match(body, _IFSC_LABELS, r"[A-Za-z0-9]{11}")
    ifsc_value: str | None = None
    ifsc_raw: str | None = None
    ifsc_span: tuple[int, int] | None = None
    if ifsc_match is not None:
        ifsc_raw = ifsc_match.group("value").strip().upper()
        if _IFSC_VALUE_RE.fullmatch(ifsc_raw):
            ifsc_value = ifsc_raw
            ifsc_span = (ifsc_match.start("value"), ifsc_match.end("value"))
    if ifsc_value is None:
        loose = _IFSC_VALUE_RE.search(body.upper())
        if loose is not None:
            ifsc_value = loose.group(1)
            raw_hit = re.search(re.escape(ifsc_value), body, re.IGNORECASE)
            if raw_hit is not None:
                ifsc_raw = body[raw_hit.start() : raw_hit.end()]
                ifsc_span = (raw_hit.start(), raw_hit.end())
            else:
                ifsc_raw = ifsc_value
                ifsc_span = (loose.start(1), loose.end(1))
    if ifsc_value is None or ifsc_raw is None or ifsc_span is None:
        raise ExtractionError(
            code="extraction_failed",
            message="IFSC not found on bank statement",
            details={"field": "ifsc"},
        )
    fields.append(
        ExtractedFieldResult(
            field_name="ifsc",
            raw_value=ifsc_raw,
            normalized_value=text_normalized(ifsc_value),
            confidence=Decimal("0.9600"),
            page_number=1,
            bbox=bbox_for_span(body, ifsc_span[0], ifsc_span[1]),
        )
    )

    start_raw: str | None = None
    end_raw: str | None = None
    start_span: tuple[int, int] | None = None
    end_span: tuple[int, int] | None = None
    period = _PERIOD_RE.search(body)
    if period is not None:
        start_raw = period.group("start")
        end_raw = period.group("end")
        start_span = (period.start("start"), period.end("start"))
        end_span = (period.start("end"), period.end("end"))
    else:
        start_match = first_labeled_match(
            body,
            _START_LABELS,
            r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}-\d{2}-\d{2}",
        )
        end_match = first_labeled_match(
            body,
            _END_LABELS,
            r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}-\d{2}-\d{2}",
        )
        if start_match is not None:
            start_raw = start_match.group("value").strip()
            start_span = (start_match.start("value"), start_match.end("value"))
        if end_match is not None:
            end_raw = end_match.group("value").strip()
            end_span = (end_match.start("value"), end_match.end("value"))

    if start_raw is None or end_raw is None or start_span is None or end_span is None:
        raise ExtractionError(
            code="extraction_failed",
            message="Statement period dates not found on bank statement",
            details={"fields": ["statement_start_date", "statement_end_date"]},
        )
    try:
        start_date = parse_flexible_date(start_raw)
        end_date = parse_flexible_date(end_raw)
    except ValueError as exc:
        raise ExtractionError(
            code="extraction_failed",
            message="Statement period dates could not be normalized",
            details={"start": start_raw, "end": end_raw},
        ) from exc

    fields.append(
        ExtractedFieldResult(
            field_name="statement_start_date",
            raw_value=start_raw,
            normalized_value=date_normalized(start_date),
            confidence=Decimal("0.9100"),
            page_number=1,
            bbox=bbox_for_span(body, start_span[0], start_span[1]),
        )
    )
    fields.append(
        ExtractedFieldResult(
            field_name="statement_end_date",
            raw_value=end_raw,
            normalized_value=date_normalized(end_date),
            confidence=Decimal("0.9100"),
            page_number=1,
            bbox=bbox_for_span(body, end_span[0], end_span[1]),
        )
    )

    amb = find_amount_after_label(body, _AMB_LABELS)
    if amb is not None:
        fields.append(
            ExtractedFieldResult(
                field_name="average_monthly_balance",
                raw_value=amb[0],
                normalized_value=money_normalized(amb[1]),
                confidence=Decimal("0.8800"),
                page_number=1,
                bbox=bbox_for_span(body, amb[2], amb[3]),
            )
        )

    monthly: list[BankStatementMonthlySummary] = []
    for block in _MONTH_BLOCK_RE.finditer(body):
        month = parse_flexible_date(block.group("month")).replace(day=1)
        credits = parse_amount(block.group("credits"))
        debits = parse_amount(block.group("debits"))
        closing = parse_amount(block.group("closing"))
        # Salary lines often sit immediately after closing balance.
        window_end = min(len(body), block.end() + 240)
        window = body[block.start() : window_end]
        salary_amount = Decimal("0.00")
        salary_count = 0
        salary = find_amount_after_label(window, _SALARY_LABELS)
        if salary is not None:
            salary_amount = salary[1]
            salary_count = 1
        monthly.append(
            BankStatementMonthlySummary(
                statement_month=month,
                total_credits=credits,
                total_debits=debits,
                closing_balance=closing,
                identified_salary_credits=salary_amount,
                salary_credit_count=salary_count,
            )
        )

    if not monthly:
        credits = find_amount_after_label(body, _CREDITS_LABELS)
        debits = find_amount_after_label(body, _DEBITS_LABELS)
        closing = find_amount_after_label(body, _CLOSING_LABELS)
        if credits is None or debits is None or closing is None:
            raise ExtractionError(
                code="extraction_failed",
                message="Monthly credit/debit/closing summary not found",
            )
        month_match = first_labeled_match(
            body,
            _MONTH_LABELS,
            r"\d{4}-\d{2}|\w+\s+\d{4}",
        )
        if month_match is not None:
            statement_month = parse_flexible_date(
                month_match.group("value").strip()
            ).replace(day=1)
        else:
            statement_month = end_date.replace(day=1)
        salary = find_amount_after_label(body, _SALARY_LABELS)
        salary_amount = salary[1] if salary is not None else Decimal("0.00")
        salary_count = 1 if salary is not None else 0
        monthly.append(
            BankStatementMonthlySummary(
                statement_month=statement_month,
                total_credits=credits[1],
                total_debits=debits[1],
                closing_balance=closing[1],
                identified_salary_credits=salary_amount,
                salary_credit_count=salary_count,
            )
        )
        fields.append(
            ExtractedFieldResult(
                field_name="total_credits",
                raw_value=credits[0],
                normalized_value=money_normalized(credits[1]),
                confidence=Decimal("0.9000"),
                page_number=1,
                bbox=bbox_for_span(body, credits[2], credits[3]),
            )
        )
        fields.append(
            ExtractedFieldResult(
                field_name="total_debits",
                raw_value=debits[0],
                normalized_value=money_normalized(debits[1]),
                confidence=Decimal("0.9000"),
                page_number=1,
                bbox=bbox_for_span(body, debits[2], debits[3]),
            )
        )
    else:
        # Persist first-month credits/debits as field-layer samples.
        first = monthly[0]
        fields.append(
            ExtractedFieldResult(
                field_name="total_credits",
                raw_value=f"{first.total_credits:.2f}",
                normalized_value=money_normalized(first.total_credits),
                confidence=Decimal("0.9000"),
                page_number=1,
                bbox=None,
            )
        )
        fields.append(
            ExtractedFieldResult(
                field_name="total_debits",
                raw_value=f"{first.total_debits:.2f}",
                normalized_value=money_normalized(first.total_debits),
                confidence=Decimal("0.9000"),
                page_number=1,
                bbox=None,
            )
        )

    return DocumentExtractionResult(
        extractor_name=EXTRACTOR_NAME,
        fields=fields,
        bank_summaries=monthly,
        raw_payload={
            "document_type": "bank_statement",
            "month_count": len(monthly),
        },
    )

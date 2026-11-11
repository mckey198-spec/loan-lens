"""Shared text helpers for deterministic extractors (no LLM)."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from datetime import date, datetime

_AMOUNT_RE = re.compile(
    r"(?<![\w.])(-?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|-?\d+(?:\.\d{1,2})?)(?![\w.])"
)


def require_nonempty_text(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("document text is empty or unreadable")
    return stripped


def line_index_at(text: str, offset: int) -> int:
    if offset < 0:
        return 0
    return text.count("\n", 0, offset)


def bbox_for_span(
    text: str,
    start: int,
    end: int,
    *,
    page_number: int = 1,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """
    Deterministic synthetic bbox from character span.

    Coordinates are in points-like units derived from line/column indices so
    tests can assert persistence without a real OCR layout engine.
    """
    del page_number  # reserved for multi-page OCR layout
    line = line_index_at(text, start)
    line_start = text.rfind("\n", 0, start) + 1
    col_start = start - line_start
    col_end = max(col_start + 1, end - line_start)
    x_min = Decimal(col_start) * Decimal("6.0000")
    x_max = Decimal(col_end) * Decimal("6.0000")
    y_max = Decimal("792.0000") - (Decimal(line) * Decimal("14.0000"))
    y_min = y_max - Decimal("12.0000")
    return (x_min, y_min, x_max, y_max)


def parse_amount(raw: str) -> Decimal:
    cleaned = raw.strip().replace(",", "").replace("₹", "").replace("Rs.", "")
    cleaned = cleaned.replace("INR", "").strip()
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount: {raw!r}") from exc
    return amount.quantize(Decimal("0.01"))


def money_normalized(amount: Decimal, *, currency: str = "INR") -> dict[str, object]:
    return {"amount": f"{amount:.2f}", "currency": currency}


def text_normalized(value: str) -> dict[str, object]:
    return {"value": value.strip()}


def date_normalized(value: date) -> dict[str, object]:
    return {"value": value.isoformat()}


def parse_flexible_date(raw: str) -> date:
    candidate = raw.strip()
    for fmt in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %Y",
        "%b %Y",
        "%Y-%m",
    ):
        try:
            parsed = datetime.strptime(candidate, fmt)
            if fmt in ("%B %Y", "%b %Y", "%Y-%m"):
                return date(parsed.year, parsed.month, 1)
            return parsed.date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date: {raw!r}")


def first_labeled_match(
    text: str,
    labels: tuple[str, ...],
    value_pattern: str,
    *,
    flags: int = re.IGNORECASE,
) -> re.Match[str] | None:
    for label in labels:
        pattern = re.compile(
            rf"(?P<label>{re.escape(label)})\s*[:\-]?\s*(?P<value>{value_pattern})",
            flags,
        )
        match = pattern.search(text)
        if match is not None:
            return match
    return None


def find_amount_after_label(
    text: str,
    labels: tuple[str, ...],
) -> tuple[str, Decimal, int, int] | None:
    # Prefer comma-grouped amounts, else a plain digit run (avoid matching
    # only the first 3 digits of values like 60000.00).
    match = first_labeled_match(
        text,
        labels,
        r"[₹Rs.\s]*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?",
    )
    if match is None:
        return None
    raw = match.group("value").strip()
    amount = parse_amount(raw)
    return raw, amount, match.start("value"), match.end("value")

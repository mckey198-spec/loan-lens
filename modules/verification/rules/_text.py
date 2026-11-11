"""Pure text/numeric helpers for deterministic verification rules (no LLM)."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Iterable

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_WHITESPACE_RE = re.compile(r"\s+")


def casefold_normalize(value: str) -> str:
    """NFKC + casefold + collapse whitespace for exact string compares."""
    normalized = unicodedata.normalize("NFKC", value or "")
    collapsed = _WHITESPACE_RE.sub(" ", normalized).strip()
    return collapsed.casefold()


def tokenize_name(value: str) -> list[str]:
    """Case-folded alphanumeric tokens for name / employer similarity."""
    return _TOKEN_RE.findall(casefold_normalize(value))


def token_jaccard(left: str, right: str) -> Decimal:
    """
    Jaccard similarity over normalized tokens.

    Empty inputs yield 0. Identical empty-token pairs yield 0 (no match signal).
    """
    left_tokens = set(tokenize_name(left))
    right_tokens = set(tokenize_name(right))
    if not left_tokens or not right_tokens:
        return Decimal("0")
    intersection = left_tokens & right_tokens
    union = left_tokens | right_tokens
    ratio = Decimal(len(intersection)) / Decimal(len(union))
    return ratio.quantize(Decimal("0.0001"))


def normalize_pan(value: str) -> str:
    return (value or "").strip().upper().replace(" ", "")


def parse_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().replace(",", "").replace("₹", "")
    text = text.replace("Rs.", "").replace("INR", "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def within_tolerance(
    left: Decimal,
    right: Decimal,
    *,
    tolerance_pct: Decimal,
) -> bool:
    """True when |left - right| <= tolerance_pct% of the reference (max of abs)."""
    reference = max(abs(left), abs(right))
    if reference == 0:
        return left == right
    delta = abs(left - right)
    allowed = (reference * tolerance_pct) / Decimal("100")
    return delta <= allowed


def fields_by_name(
    fields: Iterable[object],
    field_name: str,
) -> list[object]:
    return [field for field in fields if getattr(field, "field_name", None) == field_name]

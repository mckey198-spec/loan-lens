"""Deterministic document classification with optional model fallback."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from modules.classification.provider import (
    ClassificationProvider,
    UnavailableClassificationProvider,
)
from modules.classification.schemas import (
    ClassificationMethod,
    ClassificationResult,
    DocumentType,
)

HIGH_CONFIDENCE_THRESHOLD = Decimal("0.90")
MIN_ACCEPT_CONFIDENCE = Decimal("0.60")

_PAN_PATTERN = re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]")

_DEFAULT_PROVIDER = UnavailableClassificationProvider()


@dataclass(frozen=True, slots=True)
class _TypeSignals:
    document_type: DocumentType
    keywords: tuple[str, ...]
    patterns: tuple[re.Pattern[str], ...] = ()


_SIGNAL_TABLE: tuple[_TypeSignals, ...] = (
    _TypeSignals(
        document_type="pan_card",
        keywords=(
            "income tax department",
            "permanent account number",
        ),
        patterns=(_PAN_PATTERN,),
    ),
    _TypeSignals(
        document_type="salary_slip",
        keywords=(
            "payslip",
            "salary slip",
            "earnings",
            "deductions",
            "basic pay",
            "net pay",
        ),
    ),
    _TypeSignals(
        document_type="bank_statement",
        keywords=(
            "account statement",
            "transaction date",
            "withdrawal",
            "deposit",
            "opening balance",
            "closing balance",
        ),
    ),
    _TypeSignals(
        document_type="identity_proof",
        keywords=(
            "aadhaar",
            "passport",
            "voter id",
            "driving licence",
            "driving license",
        ),
    ),
    _TypeSignals(
        document_type="address_proof",
        keywords=(
            "aadhaar",
            "passport",
            "voter id",
            "driving licence",
            "driving license",
            "address proof",
            "residential address",
        ),
    ),
)


def _unsupported(
    *,
    method: ClassificationMethod = "heuristic_rules",
) -> ClassificationResult:
    return ClassificationResult(
        document_type="unsupported",
        confidence=Decimal("0.0"),
        method=method,
        evidence_snippets=[],
    )


def _confidence_from_hits(
    *,
    pattern_hits: int,
    keyword_hits: int,
    document_type: DocumentType,
    folded_haystack: str,
) -> Decimal:
    if pattern_hits == 0 and keyword_hits == 0:
        return Decimal("0.0")
    if pattern_hits >= 1 and keyword_hits >= 1:
        return Decimal("0.95")
    if keyword_hits >= 3:
        return Decimal("0.92")
    if (
        document_type == "address_proof"
        and keyword_hits >= 2
        and (
            "address proof" in folded_haystack
            or "residential address" in folded_haystack
        )
    ):
        return Decimal("0.91")
    if pattern_hits >= 1:
        return Decimal("0.70")
    if keyword_hits == 2:
        return Decimal("0.75")
    return Decimal("0.55")


def _score_type(
    *,
    original: str,
    folded: str,
    signals: _TypeSignals,
) -> tuple[Decimal, list[str]]:
    evidence: list[str] = []
    pattern_hits = 0
    keyword_hits = 0

    for pattern in signals.patterns:
        match = pattern.search(original)
        if match is not None:
            pattern_hits += 1
            evidence.append(match.group(0))

    for keyword in signals.keywords:
        if keyword.casefold() in folded:
            keyword_hits += 1
            evidence.append(keyword)

    confidence = _confidence_from_hits(
        pattern_hits=pattern_hits,
        keyword_hits=keyword_hits,
        document_type=signals.document_type,
        folded_haystack=folded,
    )
    if confidence == Decimal("0.0"):
        return confidence, []
    return confidence, evidence


def _heuristic_classify(text: str, filename: str) -> ClassificationResult | None:
    """
    Layer 1: deterministic keyword/regex scoring.

    Returns a result only when confidence meets the high-confidence threshold.
    Otherwise returns None so the caller can invoke Layer 2.
    """
    combined = f"{filename}\n{text}"
    folded = combined.casefold()
    # PAN pattern expects uppercase A–Z / digits.
    original_for_patterns = combined.upper()

    best_type: DocumentType | None = None
    best_confidence = Decimal("0.0")
    best_evidence: list[str] = []

    for signals in _SIGNAL_TABLE:
        confidence, evidence = _score_type(
            original=original_for_patterns,
            folded=folded,
            signals=signals,
        )
        if confidence > best_confidence:
            best_confidence = confidence
            best_type = signals.document_type
            best_evidence = evidence

    if best_type is None or best_confidence < HIGH_CONFIDENCE_THRESHOLD:
        return None

    return ClassificationResult(
        document_type=best_type,
        confidence=best_confidence,
        method="heuristic_rules",
        evidence_snippets=best_evidence,
    )


def classify_document_text(
    text: str,
    filename: str = "",
    *,
    provider: ClassificationProvider | None = None,
) -> ClassificationResult:
    """
    Classify document text.

    Layer 1 uses deterministic heuristics. When confidence is below 0.90,
    Layer 2 routes to the classification provider. Confidence below 0.60
    (or empty/unrecognizable text) yields ``unsupported`` at 0.0.
    """
    stripped = (text or "").strip()
    if not stripped:
        return _unsupported(method="heuristic_rules")

    heuristic = _heuristic_classify(stripped, filename or "")
    if heuristic is not None:
        return heuristic

    active_provider = provider if provider is not None else _DEFAULT_PROVIDER
    fallback = active_provider.classify_text(stripped, filename or "")

    if (
        fallback.document_type == "unsupported"
        or fallback.confidence < MIN_ACCEPT_CONFIDENCE
    ):
        return ClassificationResult(
            document_type="unsupported",
            confidence=Decimal("0.0"),
            method=fallback.method,
            evidence_snippets=list(fallback.evidence_snippets),
        )

    return fallback

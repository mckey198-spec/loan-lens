"""Classification provider abstraction (ADR-003; no silent invent)."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from modules.classification.schemas import ClassificationResult


class ClassificationProvider(Protocol):
    """Layer-2 classifier used when heuristics do not reach high confidence."""

    def classify_text(
        self,
        text: str,
        filename: str = "",
    ) -> ClassificationResult:
        """Classify document text; must return a validated ClassificationResult."""
        ...


class UnavailableClassificationProvider:
    """
    Default Layer-2 adapter when no model provider is configured.

    Returns unsupported rather than inventing a type (ADR-003).
    """

    def classify_text(
        self,
        text: str,
        filename: str = "",
    ) -> ClassificationResult:
        del text, filename
        return ClassificationResult(
            document_type="unsupported",
            confidence=Decimal("0.0"),
            method="model_fallback",
            evidence_snippets=[],
        )

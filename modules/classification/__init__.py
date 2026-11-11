"""Deterministic document classification (heuristics + provider fallback)."""

from modules.classification.classifier import classify_document_text
from modules.classification.schemas import ClassificationResult

__all__ = [
    "ClassificationResult",
    "classify_document_text",
]

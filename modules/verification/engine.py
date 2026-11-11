"""Deterministic verification rule engine (ADR-025; no LLM calls)."""

from __future__ import annotations

from modules.verification.rules import RULE_EVALUATORS
from modules.verification.schemas import RuleEvaluationContext, RuleEvaluationResult


def evaluate_all_rules(
    context: RuleEvaluationContext,
) -> list[RuleEvaluationResult]:
    """
    Evaluate BR-01 through BR-11 in stable order.

    Each evaluator is a pure procedural function. This module never imports
    or calls LLM / model provider clients.
    """
    return [evaluator(context) for evaluator in RULE_EVALUATORS]

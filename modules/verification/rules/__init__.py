"""Deterministic BR-01…BR-11 evaluators (pure Python; no LLM)."""

from __future__ import annotations

from collections.abc import Callable

from modules.verification.rules import (
    br01_name,
    br02_pan,
    br03_dob,
    br04_employer,
    br05_salary_credit,
    br06_income_discrepancy,
    br07_tenure,
    br08_min_income,
    br09_statement_continuity,
    br10_emi_corroboration,
    br11_doc_completeness,
)
from modules.verification.schemas import RuleEvaluationContext, RuleEvaluationResult

RuleEvaluator = Callable[[RuleEvaluationContext], RuleEvaluationResult]

RULE_EVALUATORS: tuple[RuleEvaluator, ...] = (
    br01_name.evaluate,
    br02_pan.evaluate,
    br03_dob.evaluate,
    br04_employer.evaluate,
    br05_salary_credit.evaluate,
    br06_income_discrepancy.evaluate,
    br07_tenure.evaluate,
    br08_min_income.evaluate,
    br09_statement_continuity.evaluate,
    br10_emi_corroboration.evaluate,
    br11_doc_completeness.evaluate,
)

__all__ = ["RULE_EVALUATORS", "RuleEvaluator"]

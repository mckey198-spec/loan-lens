"""Observation generation orchestration (Architecture §8; BR-12)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from modules.observations.provider import (
    DeterministicObservationProvider,
    ObservationProvider,
)
from modules.observations.schemas import (
    CalculationMetricsSummary,
    ObservationContext,
    ObservationOutput,
    PolicyCitationSummary,
    RuleFindingSummary,
    SnapshotFactSummary,
)

_DEFAULT_PROVIDER = DeterministicObservationProvider()


def build_observation_context(
    *,
    application_id: UUID,
    processing_run_id: UUID,
    snapshot_facts: dict[str, Any] | None = None,
    verification_findings: list[dict[str, Any]] | None = None,
    financial_calculation: dict[str, Any] | None = None,
    policy_citations: dict[str, list[dict[str, Any]]] | list[dict[str, Any]] | None = None,
) -> ObservationContext:
    """
    Assemble a typed ObservationContext from workflow / DB payloads.

    Pure transformation: no I/O and no LLM calls.
    """
    snap_raw = snapshot_facts or {}
    snapshot = SnapshotFactSummary(
        applicant_type=snap_raw.get("applicant_type"),
        requested_amount=snap_raw.get("requested_amount"),
        tenure_months=snap_raw.get("tenure_months"),
        declared_net_monthly_income=snap_raw.get(
            "declared_net_monthly_income"
        ),
        declared_existing_emis=snap_raw.get("declared_existing_emis"),
        as_of_date=(
            str(snap_raw["as_of_date"])
            if snap_raw.get("as_of_date") is not None
            else None
        ),
    )

    findings: list[RuleFindingSummary] = []
    for item in verification_findings or []:
        if not isinstance(item, dict):
            continue
        rule_code = item.get("rule_code")
        status = item.get("status")
        severity = item.get("severity")
        message = item.get("message")
        if not rule_code or not status or not severity or message is None:
            continue
        findings.append(
            RuleFindingSummary(
                rule_code=str(rule_code),
                rule_name=(
                    str(item["rule_name"])
                    if item.get("rule_name") is not None
                    else None
                ),
                status=str(status),
                severity=str(severity),
                message=str(message),
            )
        )

    calculation: CalculationMetricsSummary | None = None
    if isinstance(financial_calculation, dict) and financial_calculation:
        calculation = CalculationMetricsSummary(
            proposed_emi=financial_calculation.get("proposed_emi"),
            total_obligations=financial_calculation.get("total_obligations"),
            foir_pct=financial_calculation.get("foir_pct"),
            foir_threshold_pct=financial_calculation.get("foir_threshold_pct"),
            foir_passed=financial_calculation.get("foir_passed"),
            net_disposable_income=financial_calculation.get(
                "net_disposable_income"
            ),
            max_qualifying_loan_amount=financial_calculation.get(
                "max_qualifying_loan_amount"
            ),
        )

    citations: list[PolicyCitationSummary] = []
    if isinstance(policy_citations, dict):
        for topic, matches in policy_citations.items():
            if not isinstance(matches, list):
                continue
            for match in matches:
                if not isinstance(match, dict) or match.get("chunk_id") is None:
                    continue
                citations.append(
                    PolicyCitationSummary(
                        chunk_id=UUID(str(match["chunk_id"])),
                        topic=str(topic),
                        section_label=(
                            str(match["section_label"])
                            if match.get("section_label") is not None
                            else None
                        ),
                        rank_score=(
                            float(match["rank_score"])
                            if match.get("rank_score") is not None
                            else None
                        ),
                    )
                )
    elif isinstance(policy_citations, list):
        for match in policy_citations:
            if not isinstance(match, dict) or match.get("chunk_id") is None:
                continue
            citations.append(
                PolicyCitationSummary(
                    chunk_id=UUID(str(match["chunk_id"])),
                    topic=(
                        str(match["topic"])
                        if match.get("topic") is not None
                        else None
                    ),
                    section_label=(
                        str(match["section_label"])
                        if match.get("section_label") is not None
                        else None
                    ),
                    rank_score=(
                        float(match["rank_score"])
                        if match.get("rank_score") is not None
                        else None
                    ),
                )
            )

    return ObservationContext(
        application_id=application_id,
        processing_run_id=processing_run_id,
        snapshot=snapshot,
        findings=findings,
        calculation=calculation,
        policy_citations=citations,
    )


def generate_observations(
    context: ObservationContext,
    *,
    provider: ObservationProvider | None = None,
) -> ObservationOutput:
    """
    Execute observation generation through the provider port.

    Defaults to ``DeterministicObservationProvider`` so tests and local
    runs never require live LLM credentials.
    """
    active = provider if provider is not None else _DEFAULT_PROVIDER
    return active.generate_observations(context)

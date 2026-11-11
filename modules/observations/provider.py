"""Observation provider abstraction (ADR-003; Architecture §8; BR-12)."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from modules.observations.schemas import ObservationContext, ObservationOutput

DETERMINISTIC_MODEL_ID = "deterministic-observation-v1"

_ADVISORY_PREAMBLE = (
    "Advisory AI observation only. This note does not assign an approval "
    "or rejection outcome and does not assign a risk grade. A human "
    "reviewer must decide the application."
)


class ObservationProvider(Protocol):
    """Provider port for labelled advisory observation generation."""

    def generate_observations(
        self,
        context: ObservationContext,
    ) -> ObservationOutput:
        """Produce structured advisory observations from assembled context."""
        ...


class DeterministicObservationProvider:
    """
    Stub / fallback provider that synthesizes observations deterministically.

    Uses verification findings and financial ratios only. Does not call a
    live LLM and does not require API keys (test isolation).
    """

    model_id: str = DETERMINISTIC_MODEL_ID

    def generate_observations(
        self,
        context: ObservationContext,
    ) -> ObservationOutput:
        risk_factors: list[str] = []
        strengths: list[str] = []

        for finding in context.findings:
            status = finding.status.lower()
            severity = finding.severity.lower()
            label = finding.rule_code
            detail = finding.message.strip() or finding.rule_name or label
            if status in ("failed", "flagged"):
                risk_factors.append(
                    f"{label} ({severity}): {detail}"
                )
            elif status == "passed":
                strengths.append(f"{label} passed: {detail}")

        if context.calculation is not None:
            calc = context.calculation
            if calc.foir_pct is not None and calc.foir_threshold_pct is not None:
                if calc.foir_passed is False:
                    risk_factors.append(
                        f"FOIR {calc.foir_pct}% exceeds threshold "
                        f"{calc.foir_threshold_pct}%."
                    )
                elif calc.foir_passed is True:
                    strengths.append(
                        f"FOIR {calc.foir_pct}% is within threshold "
                        f"{calc.foir_threshold_pct}%."
                    )
            if (
                calc.net_disposable_income is not None
                and calc.net_disposable_income < 0
            ):
                risk_factors.append(
                    f"Net disposable income is negative "
                    f"({calc.net_disposable_income})."
                )
            elif (
                calc.net_disposable_income is not None
                and calc.net_disposable_income > 0
            ):
                strengths.append(
                    f"Net disposable income is positive "
                    f"({calc.net_disposable_income})."
                )
            if calc.proposed_emi is not None:
                strengths.append(
                    f"Proposed EMI computed as {calc.proposed_emi} "
                    "(deterministic calculator)."
                )

        if not risk_factors and not strengths:
            strengths.append(
                "No material verification flags or ratio concerns were "
                "available in the observation context."
            )

        cited: list[UUID] = []
        seen: set[UUID] = set()
        for citation in context.policy_citations:
            if citation.chunk_id not in seen:
                seen.add(citation.chunk_id)
                cited.append(citation.chunk_id)

        body_parts = [
            _ADVISORY_PREAMBLE,
            "",
            "Synthesized from deterministic verification findings "
            "(BR-01..BR-11) and financial calculation metrics.",
        ]
        if risk_factors:
            body_parts.append("")
            body_parts.append("Potential areas for reviewer attention:")
            for item in risk_factors:
                body_parts.append(f"- {item}")
        if strengths:
            body_parts.append("")
            body_parts.append("Supporting observations:")
            for item in strengths:
                body_parts.append(f"- {item}")
        if cited:
            body_parts.append("")
            body_parts.append(
                f"Grounded against {len(cited)} policy citation chunk(s)."
            )

        return ObservationOutput(
            observation_text="\n".join(body_parts),
            risk_factors=risk_factors,
            strengths=strengths,
            cited_chunk_ids=cited,
            model_id=self.model_id,
        )

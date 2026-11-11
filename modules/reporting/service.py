"""Read-only assessment package assembly and PDF export (M13; Architecture §10)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from modules.governance.audit import append_audit_event
from modules.policy.search import DEFAULT_CITATION_TOPICS, retrieve_relevant_citations
from modules.reporting.generator import build_assessment_pdf
from modules.reporting.schemas import (
    EXPORTABLE_APPLICATION_STATUSES,
    AiObservationRow,
    AssessmentDecisionBlock,
    AssessmentReportPackage,
    DispositionRow,
    FieldCorrectionRow,
    FinancialMetricsBlock,
    PolicyCitationRow,
    RuleFindingRow,
    SnapshotMetadata,
)
from shared.errors import AppError


def _as_decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


async def _fetch_application(
    connection: Any,
    application_id: UUID,
) -> dict[str, Any] | None:
    row = await connection.execute(
        """
        SELECT
            id,
            reference_code,
            status::text AS status
          FROM applications
         WHERE id = %s
        """,
        (application_id,),
    )
    return await row.fetchone()


async def _fetch_latest_assessment(
    connection: Any,
    application_id: UUID,
) -> dict[str, Any] | None:
    """
    Prefer the latest decided assessment; otherwise the latest open assessment.

    Export is allowed while ``in_review`` (Architecture §11 assessments group).
    """
    decided = await connection.execute(
        """
        SELECT
            a.id,
            a.application_id,
            a.processing_run_id,
            a.input_snapshot_id,
            a.applicable_policy_version_id,
            a.assigned_reviewer_id,
            a.recommendation::text AS recommendation,
            a.reviewer_notes,
            a.decided_at,
            a.created_at,
            u.full_name AS reviewer_full_name
          FROM assessments AS a
          LEFT JOIN users AS u ON u.id = a.assigned_reviewer_id
         WHERE a.application_id = %s
           AND a.decided_at IS NOT NULL
         ORDER BY a.decided_at DESC, a.created_at DESC, a.id DESC
         LIMIT 1
        """,
        (application_id,),
    )
    decided_row = await decided.fetchone()
    if decided_row is not None:
        return dict(decided_row)

    open_row = await connection.execute(
        """
        SELECT
            a.id,
            a.application_id,
            a.processing_run_id,
            a.input_snapshot_id,
            a.applicable_policy_version_id,
            a.assigned_reviewer_id,
            a.recommendation::text AS recommendation,
            a.reviewer_notes,
            a.decided_at,
            a.created_at,
            u.full_name AS reviewer_full_name
          FROM assessments AS a
          LEFT JOIN users AS u ON u.id = a.assigned_reviewer_id
         WHERE a.application_id = %s
         ORDER BY a.created_at DESC, a.id DESC
         LIMIT 1
        """,
        (application_id,),
    )
    result = await open_row.fetchone()
    return dict(result) if result is not None else None


async def _fetch_snapshot_metadata(
    connection: Any,
    *,
    application_id: UUID,
    snapshot_id: UUID,
    application_reference_code: str,
    application_status: str,
) -> SnapshotMetadata:
    row = await connection.execute(
        """
        SELECT
            s.id AS snapshot_id,
            s.snapshot_hash,
            s.as_of_date,
            s.applicable_policy_version_id AS policy_version_id,
            pv.version_label AS policy_version_label
          FROM application_input_snapshots AS s
          JOIN policy_versions AS pv
            ON pv.id = s.applicable_policy_version_id
         WHERE s.id = %s
           AND s.application_id = %s
        """,
        (snapshot_id, application_id),
    )
    record = await row.fetchone()
    if record is None:
        raise AppError(
            code="SNAPSHOT_NOT_FOUND",
            message="Input snapshot for assessment export was not found.",
            status_code=404,
            details={
                "application_id": str(application_id),
                "snapshot_id": str(snapshot_id),
            },
        )
    return SnapshotMetadata(
        snapshot_id=record["snapshot_id"],
        snapshot_hash=record["snapshot_hash"],
        as_of_date=record["as_of_date"],
        policy_version_id=record["policy_version_id"],
        policy_version_label=record["policy_version_label"],
        application_reference_code=application_reference_code,
        application_status=application_status,
    )


async def _fetch_financial_metrics(
    connection: Any,
    *,
    application_id: UUID,
    processing_run_id: UUID,
    input_snapshot_id: UUID,
) -> FinancialMetricsBlock:
    inputs_row = await connection.execute(
        """
        SELECT
            monthly_gross_income,
            monthly_net_income,
            monthly_obligations
          FROM snapshot_calculation_inputs
         WHERE input_snapshot_id = %s
           AND application_id = %s
        """,
        (input_snapshot_id, application_id),
    )
    inputs = await inputs_row.fetchone()

    calc_row = await connection.execute(
        """
        SELECT
            proposed_emi,
            total_obligations,
            foir_pct,
            foir_threshold_pct,
            foir_passed,
            net_disposable_income,
            max_qualifying_loan_amount
          FROM application_financial_calculations
         WHERE application_id = %s
           AND processing_run_id = %s
        """,
        (application_id, processing_run_id),
    )
    calc = await calc_row.fetchone()

    return FinancialMetricsBlock(
        monthly_gross_income=_as_decimal(
            inputs["monthly_gross_income"] if inputs else None
        ),
        monthly_net_income=_as_decimal(
            inputs["monthly_net_income"] if inputs else None
        ),
        monthly_obligations=_as_decimal(
            inputs["monthly_obligations"] if inputs else None
        ),
        proposed_emi=_as_decimal(calc["proposed_emi"] if calc else None),
        total_obligations=_as_decimal(calc["total_obligations"] if calc else None),
        foir_pct=_as_decimal(calc["foir_pct"] if calc else None),
        foir_threshold_pct=_as_decimal(
            calc["foir_threshold_pct"] if calc else None
        ),
        foir_passed=bool(calc["foir_passed"]) if calc is not None else None,
        max_qualifying_loan_amount=_as_decimal(
            calc["max_qualifying_loan_amount"] if calc else None
        ),
        net_disposable_income=_as_decimal(
            calc["net_disposable_income"] if calc else None
        ),
    )


async def _fetch_findings(
    connection: Any,
    *,
    application_id: UUID,
    processing_run_id: UUID,
) -> list[RuleFindingRow]:
    rows = await connection.execute(
        """
        SELECT
            f.rule_code,
            f.rule_name,
            f.status::text AS status,
            f.severity::text AS severity,
            f.message,
            f.diff_payload_json
          FROM verification_findings AS f
          JOIN verification_runs AS vr
            ON vr.id = f.verification_run_id
         WHERE f.application_id = %s
           AND vr.processing_run_id = %s
         ORDER BY f.rule_code ASC, f.created_at ASC, f.id ASC
        """,
        (application_id, processing_run_id),
    )
    findings: list[RuleFindingRow] = []
    for record in await rows.fetchall():
        payload = record["diff_payload_json"] or {}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        findings.append(
            RuleFindingRow(
                rule_code=record["rule_code"],
                rule_name=record["rule_name"],
                status=record["status"],
                severity=record["severity"],
                message=record["message"],
                diff_payload_json=payload,
            )
        )
    return findings


async def _fetch_dispositions(
    connection: Any,
    *,
    application_id: UUID,
    assessment_id: UUID,
) -> list[DispositionRow]:
    rows = await connection.execute(
        """
        SELECT
            d.action::text AS action,
            d.justification,
            d.created_by_user_id,
            d.created_at,
            f.rule_code
          FROM finding_dispositions AS d
          LEFT JOIN verification_findings AS f
            ON f.id = d.verification_finding_id
         WHERE d.application_id = %s
           AND d.assessment_id = %s
         ORDER BY d.created_at ASC, d.id ASC
        """,
        (application_id, assessment_id),
    )
    return [
        DispositionRow(
            rule_code=record["rule_code"],
            action=record["action"],
            justification=record["justification"],
            created_by_user_id=record["created_by_user_id"],
            created_at=record["created_at"],
        )
        for record in await rows.fetchall()
    ]


async def _fetch_corrections(
    connection: Any,
    *,
    application_id: UUID,
    assessment_id: UUID,
) -> list[FieldCorrectionRow]:
    rows = await connection.execute(
        """
        SELECT
            c.corrected_value_text,
            c.corrected_value_numeric,
            c.corrected_value_date,
            c.reason,
            c.corrected_by_user_id,
            c.created_at,
            ef.field_name::text AS field_name
          FROM field_corrections AS c
          LEFT JOIN document_extracted_fields AS ef
            ON ef.id = c.extracted_field_id
         WHERE c.application_id = %s
           AND c.assessment_id = %s
         ORDER BY c.created_at ASC, c.id ASC
        """,
        (application_id, assessment_id),
    )
    return [
        FieldCorrectionRow(
            field_name=record["field_name"],
            corrected_value_text=record["corrected_value_text"],
            corrected_value_numeric=_as_decimal(record["corrected_value_numeric"]),
            corrected_value_date=record["corrected_value_date"],
            reason=record["reason"],
            corrected_by_user_id=record["corrected_by_user_id"],
            created_at=record["created_at"],
        )
        for record in await rows.fetchall()
    ]


async def _fetch_policy_citations(
    connection: Any,
    *,
    policy_version_id: UUID,
) -> list[PolicyCitationRow]:
    citations = await retrieve_relevant_citations(
        connection,
        policy_version_id,
        list(DEFAULT_CITATION_TOPICS),
    )
    rows: list[PolicyCitationRow] = []
    for topic, matches in citations.items():
        for match in matches:
            rows.append(
                PolicyCitationRow(
                    topic=topic,
                    section_label=match.section_label,
                    content_text=match.content_text,
                )
            )
    return rows


async def _fetch_ai_observations(
    connection: Any,
    *,
    application_id: UUID,
    processing_run_id: UUID,
) -> list[AiObservationRow]:
    rows = await connection.execute(
        """
        SELECT
            model_id,
            observation_text,
            risk_factors_json,
            strengths_json,
            is_ai_generated,
            created_at
          FROM assessment_ai_observations
         WHERE application_id = %s
           AND processing_run_id = %s
         ORDER BY created_at ASC, id ASC
        """,
        (application_id, processing_run_id),
    )
    observations: list[AiObservationRow] = []
    for record in await rows.fetchall():
        risk = record["risk_factors_json"] or []
        strengths = record["strengths_json"] or []
        observations.append(
            AiObservationRow(
                model_id=record["model_id"],
                observation_text=record["observation_text"],
                risk_factors=[str(item) for item in risk],
                strengths=[str(item) for item in strengths],
                is_ai_generated=bool(record["is_ai_generated"]),
                created_at=record["created_at"],
            )
        )
    return observations


async def assemble_assessment_report_package(
    connection: Any,
    application_id: UUID,
) -> AssessmentReportPackage:
    """Load a read-only assessment package. Does not mutate domain rows."""
    application = await _fetch_application(connection, application_id)
    if application is None:
        raise AppError(
            code="APPLICATION_NOT_FOUND",
            message="Application not found.",
            status_code=404,
            details={"application_id": str(application_id)},
        )

    status = application["status"]
    if status not in EXPORTABLE_APPLICATION_STATUSES:
        raise AppError(
            code="APPLICATION_INVALID_STATE",
            message=(
                "Assessment export requires application status "
                "in_review, review_completed, or escalated."
            ),
            status_code=409,
            details={
                "application_id": str(application_id),
                "status": status,
                "allowed_statuses": sorted(EXPORTABLE_APPLICATION_STATUSES),
            },
        )

    assessment = await _fetch_latest_assessment(connection, application_id)
    if assessment is None:
        raise AppError(
            code="ASSESSMENT_NOT_FOUND",
            message="No assessment is available to export for this application.",
            status_code=404,
            details={"application_id": str(application_id)},
        )

    snapshot = await _fetch_snapshot_metadata(
        connection,
        application_id=application_id,
        snapshot_id=assessment["input_snapshot_id"],
        application_reference_code=application["reference_code"],
        application_status=status,
    )
    financials = await _fetch_financial_metrics(
        connection,
        application_id=application_id,
        processing_run_id=assessment["processing_run_id"],
        input_snapshot_id=assessment["input_snapshot_id"],
    )
    findings = await _fetch_findings(
        connection,
        application_id=application_id,
        processing_run_id=assessment["processing_run_id"],
    )
    dispositions = await _fetch_dispositions(
        connection,
        application_id=application_id,
        assessment_id=assessment["id"],
    )
    corrections = await _fetch_corrections(
        connection,
        application_id=application_id,
        assessment_id=assessment["id"],
    )
    policy_citations = await _fetch_policy_citations(
        connection,
        policy_version_id=assessment["applicable_policy_version_id"],
    )
    ai_observations = await _fetch_ai_observations(
        connection,
        application_id=application_id,
        processing_run_id=assessment["processing_run_id"],
    )

    return AssessmentReportPackage(
        application_id=application_id,
        snapshot=snapshot,
        decision=AssessmentDecisionBlock(
            assessment_id=assessment["id"],
            recommendation=assessment["recommendation"],
            reviewer_user_id=assessment["assigned_reviewer_id"],
            reviewer_full_name=assessment["reviewer_full_name"],
            decided_at=assessment["decided_at"],
            reviewer_notes=assessment["reviewer_notes"],
        ),
        financials=financials,
        findings=findings,
        dispositions=dispositions,
        corrections=corrections,
        policy_citations=policy_citations,
        ai_observations=ai_observations,
    )


async def generate_assessment_report(
    connection: Any,
    application_id: UUID,
    actor_user_id: UUID,
    *,
    request_id: str | None = None,
) -> bytes:
    """
    Assemble the assessment package, render PDF bytes, and append export audit.

    Domain tables (assessments, calculations, findings) are read-only here.
    The only write is ``assessment.exported`` via ``append_audit_event``.
    """
    package = await assemble_assessment_report_package(connection, application_id)
    pdf_bytes = build_assessment_pdf(package)

    await append_audit_event(
        connection,
        action="assessment.exported",
        actor_type="user",
        actor_user_id=actor_user_id,
        application_id=application_id,
        entity_type="assessment",
        entity_id=package.decision.assessment_id,
        assessment_id=package.decision.assessment_id,
        after_json={
            "application_id": str(application_id),
            "assessment_id": str(package.decision.assessment_id),
            "snapshot_hash": package.snapshot.snapshot_hash,
            "byte_size": len(pdf_bytes),
            "content_type": "application/pdf",
        },
        request_id=request_id,
    )
    return pdf_bytes

"""Verification context assembly and persistence (M08-T02)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence
from uuid import UUID

from apps.api.metrics import record_verification_finding
from modules.verification import repository
from modules.verification.engine import evaluate_all_rules
from modules.verification.schemas import (
    LayeredField,
    MandatoryDocumentRequirement,
    PolicyThresholds,
    RecurringDebitObservation,
    RuleEvaluationContext,
    RuleEvaluationResult,
    RuleTolerances,
    SalaryCreditObservation,
    SnapshotFacts,
    StatementPeriod,
    VerificationFindingRecord,
    VerificationFindingResponse,
    VerificationRunRecord,
    VerificationRunSummaryResponse,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def resolve_correction_overlay(
    *,
    field_id: UUID | None,
    field_name: str,
    document_type: str | None,
    overlays: Mapping[str, dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """
    Resolve Layer-4 correction payload from an optional overlay map.

    Keys may be extracted-field UUID, ``field_name``, or ``field_name:document_type``.
    """
    if not overlays:
        return None
    candidates: list[str] = []
    if field_id is not None:
        candidates.append(str(field_id))
    if document_type:
        candidates.append(f"{field_name}:{document_type}")
    candidates.append(field_name)
    for key in candidates:
        if key in overlays:
            return overlays[key]
    return None


def build_layered_fields(
    rows: Sequence[Mapping[str, Any]],
    *,
    correction_overlays: Mapping[str, dict[str, Any]] | None = None,
) -> list[LayeredField]:
    fields: list[LayeredField] = []
    for row in rows:
        field_id = row.get("id")
        field_name = str(row["field_name"])
        document_type = row.get("document_type")
        corrected = resolve_correction_overlay(
            field_id=field_id if isinstance(field_id, UUID) else None,
            field_name=field_name,
            document_type=str(document_type) if document_type else None,
            overlays=correction_overlays,
        )
        fields.append(
            LayeredField(
                field_name=field_name,
                document_type=str(document_type) if document_type else None,
                source_document_id=row.get("source_document_id"),
                extraction_id=row.get("extraction_id"),
                raw_value=row.get("raw_value"),
                normalized_value=row.get("normalized_value_json"),
                corrected_value=corrected,
            )
        )
    return fields


def build_context_from_parts(
    *,
    snapshot: SnapshotFacts,
    fields: Sequence[LayeredField],
    policy_thresholds: PolicyThresholds,
    salary_slip_net_pays: Sequence[Decimal] | None = None,
    salary_credits: Sequence[SalaryCreditObservation] | None = None,
    statement_periods: Sequence[StatementPeriod] | None = None,
    recurring_debits: Sequence[RecurringDebitObservation] | None = None,
    mandatory_documents: Sequence[MandatoryDocumentRequirement] | None = None,
    tolerances: RuleTolerances | None = None,
) -> RuleEvaluationContext:
    return RuleEvaluationContext(
        snapshot=snapshot,
        fields=list(fields),
        salary_slip_net_pays=list(salary_slip_net_pays or []),
        salary_credits=list(salary_credits or []),
        statement_periods=list(statement_periods or []),
        recurring_debits=list(recurring_debits or []),
        mandatory_documents=list(mandatory_documents or []),
        policy_thresholds=policy_thresholds,
        tolerances=tolerances or RuleTolerances(),
    )


async def build_rule_context(
    connection: Any,
    *,
    application_id: UUID,
    processing_run_id: UUID,
    input_snapshot_id: UUID,
    correction_overlays: Mapping[str, dict[str, Any]] | None = None,
    recurring_debits: Sequence[RecurringDebitObservation] | None = None,
) -> RuleEvaluationContext:
    """
    Assemble rule inputs from snapshot, applicant, extractions, and policy.

    Layer resolution preference is encoded on each ``LayeredField``:
    correction overlay → normalized_value_json → raw_value.
    """
    snapshot_row = await repository.load_snapshot_row(
        connection,
        application_id=application_id,
        input_snapshot_id=input_snapshot_id,
    )
    if snapshot_row is None:
        raise ValueError("input snapshot not found for verification")

    applicant = await repository.load_applicant_row(
        connection,
        application_id=application_id,
    )
    if applicant is None:
        raise ValueError("applicant not found for verification")

    policy_version_id = snapshot_row["applicable_policy_version_id"]
    applicant_type = str(snapshot_row["applicant_type"])
    thresholds_row = await repository.load_policy_thresholds(
        connection,
        policy_version_id=policy_version_id,
        applicant_type=applicant_type,
    )
    if thresholds_row is None:
        raise ValueError("policy thresholds not found for applicant type")

    req_rows = await repository.load_mandatory_document_requirements(
        connection,
        policy_version_id=policy_version_id,
        applicant_type=applicant_type,
    )
    field_rows = await repository.load_extracted_fields_for_run(
        connection,
        application_id=application_id,
        processing_run_id=processing_run_id,
    )
    net_pays = await repository.load_salary_slip_net_pays(
        connection,
        application_id=application_id,
        processing_run_id=processing_run_id,
    )
    credit_rows = await repository.load_bank_salary_credits(
        connection,
        application_id=application_id,
        processing_run_id=processing_run_id,
    )
    period_rows = await repository.load_statement_periods(
        connection,
        application_id=application_id,
        processing_run_id=processing_run_id,
    )

    inventory = snapshot_row.get("document_inventory_json") or []
    if not isinstance(inventory, list):
        inventory = []

    snapshot = SnapshotFacts(
        applicant_full_name=str(applicant["full_name"]),
        date_of_birth=applicant["date_of_birth"],
        pan=str(applicant["pan"]).strip().upper(),
        employer_or_business_name=str(applicant["employer_or_business_name"]),
        time_in_job_or_business_months=int(
            applicant["time_in_job_or_business_months"]
        ),
        declared_gross_monthly_income=Decimal(
            str(snapshot_row["declared_gross_monthly_income"])
        ),
        declared_net_monthly_income=Decimal(
            str(snapshot_row["declared_net_monthly_income"])
        ),
        declared_existing_emis=Decimal(str(snapshot_row["declared_existing_emis"])),
        applicant_type=applicant_type,  # type: ignore[arg-type]
        as_of_date=snapshot_row["as_of_date"],
        document_inventory=list(inventory),
    )
    thresholds = PolicyThresholds(
        min_employment_months=int(thresholds_row["min_employment_months"]),
        min_monthly_income=Decimal(str(thresholds_row["min_monthly_income"])),
        max_foir_pct=Decimal(str(thresholds_row["max_foir_pct"])),
        max_loan_amount=Decimal(str(thresholds_row["max_loan_amount"])),
    )
    fields = build_layered_fields(
        field_rows,
        correction_overlays=correction_overlays,
    )
    salary_credits = [
        SalaryCreditObservation(
            statement_month=row["statement_month"],
            amount=Decimal(str(row["identified_salary_credits"])),
            source_document_id=row.get("source_document_id"),
        )
        for row in credit_rows
    ]
    statement_periods: list[StatementPeriod] = []
    for row in period_rows:
        start = _parse_date(row.get("start_raw"))
        end = _parse_date(row.get("end_raw"))
        if start is None or end is None:
            continue
        statement_periods.append(
            StatementPeriod(
                start_date=start,
                end_date=end,
                source_document_id=row.get("source_document_id"),
            )
        )
    mandatory = [
        MandatoryDocumentRequirement(
            document_type=str(row["document_type"]),
            is_mandatory=bool(row["is_mandatory"]),
            min_count=1,
        )
        for row in req_rows
    ]

    return build_context_from_parts(
        snapshot=snapshot,
        fields=fields,
        policy_thresholds=thresholds,
        salary_slip_net_pays=[Decimal(str(v)) for v in net_pays],
        salary_credits=salary_credits,
        statement_periods=statement_periods,
        recurring_debits=list(recurring_debits or []),
        mandatory_documents=mandatory,
    )


def count_statuses(
    results: Sequence[RuleEvaluationResult],
) -> tuple[int, int, int, int]:
    total = len(results)
    passed = sum(1 for item in results if item.status == "passed")
    flagged = sum(1 for item in results if item.status == "flagged")
    failed = sum(1 for item in results if item.status == "failed")
    return total, passed, flagged, failed


async def persist_verification_results(
    connection: Any,
    *,
    application_id: UUID,
    processing_run_id: UUID,
    input_snapshot_id: UUID,
    results: Sequence[RuleEvaluationResult],
    claim_fence: int,
) -> tuple[VerificationRunRecord, list[VerificationFindingRecord]]:
    """Insert verification_runs + findings in the caller's open transaction."""
    now = _utcnow()
    total, passed, flagged, failed = count_statuses(results)
    run_row = await repository.insert_verification_run(
        connection,
        application_id=application_id,
        processing_run_id=processing_run_id,
        input_snapshot_id=input_snapshot_id,
        total_rules_evaluated=total,
        passed_count=passed,
        flagged_count=flagged,
        failed_count=failed,
        created_at=now,
    )
    finding_rows = await repository.insert_verification_findings(
        connection,
        application_id=application_id,
        verification_run_id=run_row["id"],
        results=results,
        claim_fence=claim_fence,
        created_at=now,
    )
    for result in results:
        record_verification_finding(
            rule_code=result.rule_code,
            status=str(result.status),
        )
    return (
        VerificationRunRecord.model_validate(run_row),
        [VerificationFindingRecord.model_validate(row) for row in finding_rows],
    )


async def run_verification(
    connection: Any,
    *,
    application_id: UUID,
    processing_run_id: UUID,
    input_snapshot_id: UUID,
    claim_fence: int,
    correction_overlays: Mapping[str, dict[str, Any]] | None = None,
    recurring_debits: Sequence[RecurringDebitObservation] | None = None,
) -> tuple[
    RuleEvaluationContext,
    list[RuleEvaluationResult],
    VerificationRunRecord,
    list[VerificationFindingRecord],
]:
    """Build context, evaluate BR-01…BR-11, and persist immutable rows."""
    context = await build_rule_context(
        connection,
        application_id=application_id,
        processing_run_id=processing_run_id,
        input_snapshot_id=input_snapshot_id,
        correction_overlays=correction_overlays,
        recurring_debits=recurring_debits,
    )
    results = evaluate_all_rules(context)
    run, findings = await persist_verification_results(
        connection,
        application_id=application_id,
        processing_run_id=processing_run_id,
        input_snapshot_id=input_snapshot_id,
        results=results,
        claim_fence=claim_fence,
    )
    return context, results, run, findings


def _finding_response(row: Mapping[str, Any]) -> VerificationFindingResponse:
    return VerificationFindingResponse(
        id=row["id"],
        rule_code=str(row["rule_code"]),
        rule_name=str(row["rule_name"]),
        status=row["status"],
        severity=row["severity"],
        message=str(row["message"]),
        diff_payload=dict(row.get("diff_payload_json") or {}),
        provenance_sources=list(row.get("provenance_sources_json") or []),
        created_at=row["created_at"],
    )


def _run_summary_response(
    run_row: Mapping[str, Any],
    finding_rows: Sequence[Mapping[str, Any]],
) -> VerificationRunSummaryResponse:
    return VerificationRunSummaryResponse(
        id=run_row["id"],
        application_id=run_row["application_id"],
        processing_run_id=run_row["processing_run_id"],
        total_rules_evaluated=int(run_row["total_rules_evaluated"]),
        passed_count=int(run_row["passed_count"]),
        flagged_count=int(run_row["flagged_count"]),
        failed_count=int(run_row["failed_count"]),
        created_at=run_row["created_at"],
        findings=[_finding_response(row) for row in finding_rows],
    )


async def get_latest_verification_run(
    connection: Any,
    application_id: UUID,
) -> VerificationRunSummaryResponse | None:
    """
    Read-only: latest verification run for the application with findings.

    Returns None when no runs exist for ``application_id``. Does not mutate
    runs or findings (dispositions belong to M12).
    """
    run_row = await repository.fetch_latest_verification_run(
        connection,
        application_id=application_id,
    )
    if run_row is None:
        return None
    finding_rows = await repository.fetch_findings_for_run(
        connection,
        application_id=application_id,
        verification_run_id=run_row["id"],
    )
    return _run_summary_response(run_row, finding_rows)


async def get_verification_run_by_id(
    connection: Any,
    application_id: UUID,
    run_id: UUID,
) -> VerificationRunSummaryResponse | None:
    """
    Read-only: specific verification run scoped to ``application_id``.

    Returns None when the run is missing or belongs to another application.
    """
    run_row = await repository.fetch_verification_run_by_id(
        connection,
        application_id=application_id,
        run_id=run_id,
    )
    if run_row is None:
        return None
    finding_rows = await repository.fetch_findings_for_run(
        connection,
        application_id=application_id,
        verification_run_id=run_row["id"],
    )
    return _run_summary_response(run_row, finding_rows)

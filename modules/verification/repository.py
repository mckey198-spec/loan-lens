"""Raw SQL persistence for verification runs and findings (psycopg only)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence
from uuid import UUID

from psycopg.types.json import Jsonb

from modules.verification.schemas import RuleEvaluationResult


async def insert_verification_run(
    connection: Any,
    *,
    application_id: UUID,
    processing_run_id: UUID,
    input_snapshot_id: UUID,
    total_rules_evaluated: int,
    passed_count: int,
    flagged_count: int,
    failed_count: int,
    created_at: datetime,
) -> dict[str, Any]:
    row = await connection.execute(
        """
        INSERT INTO verification_runs (
            application_id,
            processing_run_id,
            input_snapshot_id,
            total_rules_evaluated,
            passed_count,
            flagged_count,
            failed_count,
            created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING
            id,
            application_id,
            processing_run_id,
            input_snapshot_id,
            total_rules_evaluated,
            passed_count,
            flagged_count,
            failed_count,
            created_at
        """,
        (
            application_id,
            processing_run_id,
            input_snapshot_id,
            total_rules_evaluated,
            passed_count,
            flagged_count,
            failed_count,
            created_at,
        ),
    )
    result = await row.fetchone()
    assert result is not None
    return dict(result)


async def insert_verification_finding(
    connection: Any,
    *,
    application_id: UUID,
    verification_run_id: UUID,
    result: RuleEvaluationResult,
    claim_fence: int,
    created_at: datetime,
) -> dict[str, Any]:
    row = await connection.execute(
        """
        INSERT INTO verification_findings (
            application_id,
            verification_run_id,
            rule_code,
            rule_name,
            status,
            severity,
            message,
            diff_payload_json,
            provenance_sources_json,
            claim_fence,
            created_at
        ) VALUES (
            %s,
            %s,
            %s,
            %s,
            %s::rule_status,
            %s::rule_severity,
            %s,
            %s,
            %s,
            %s,
            %s
        )
        RETURNING
            id,
            application_id,
            verification_run_id,
            rule_code,
            rule_name,
            status::text AS status,
            severity::text AS severity,
            message,
            diff_payload_json,
            provenance_sources_json,
            claim_fence,
            created_at
        """,
        (
            application_id,
            verification_run_id,
            result.rule_code,
            result.rule_name,
            result.status,
            result.severity,
            result.message,
            Jsonb(result.diff_payload),
            Jsonb(result.provenance_sources),
            claim_fence,
            created_at,
        ),
    )
    finding = await row.fetchone()
    assert finding is not None
    return dict(finding)


async def insert_verification_findings(
    connection: Any,
    *,
    application_id: UUID,
    verification_run_id: UUID,
    results: Sequence[RuleEvaluationResult],
    claim_fence: int,
    created_at: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        rows.append(
            await insert_verification_finding(
                connection,
                application_id=application_id,
                verification_run_id=verification_run_id,
                result=result,
                claim_fence=claim_fence,
                created_at=created_at,
            )
        )
    return rows


async def load_snapshot_row(
    connection: Any,
    *,
    application_id: UUID,
    input_snapshot_id: UUID,
) -> dict[str, Any] | None:
    row = await connection.execute(
        """
        SELECT
            id,
            application_id,
            applicant_type::text AS applicant_type,
            as_of_date,
            declared_gross_monthly_income,
            declared_net_monthly_income,
            declared_existing_emis,
            document_inventory_json,
            applicable_policy_version_id
          FROM application_input_snapshots
         WHERE id = %s
           AND application_id = %s
        """,
        (input_snapshot_id, application_id),
    )
    result = await row.fetchone()
    return dict(result) if result is not None else None


async def load_applicant_row(
    connection: Any,
    *,
    application_id: UUID,
) -> dict[str, Any] | None:
    row = await connection.execute(
        """
        SELECT
            full_name,
            date_of_birth,
            pan,
            employer_or_business_name,
            time_in_job_or_business_months,
            gross_monthly_income,
            net_monthly_income,
            total_existing_monthly_emis
          FROM applicants
         WHERE application_id = %s
        """,
        (application_id,),
    )
    result = await row.fetchone()
    return dict(result) if result is not None else None


async def load_policy_thresholds(
    connection: Any,
    *,
    policy_version_id: UUID,
    applicant_type: str,
) -> dict[str, Any] | None:
    row = await connection.execute(
        """
        SELECT
            min_monthly_income,
            max_foir_pct,
            min_employment_months,
            max_loan_amount
          FROM policy_threshold_configs
         WHERE policy_version_id = %s
           AND applicant_type = %s::applicant_type
        """,
        (policy_version_id, applicant_type),
    )
    result = await row.fetchone()
    return dict(result) if result is not None else None


async def load_mandatory_document_requirements(
    connection: Any,
    *,
    policy_version_id: UUID,
    applicant_type: str,
) -> list[dict[str, Any]]:
    rows = await connection.execute(
        """
        SELECT
            document_type::text AS document_type,
            is_mandatory
          FROM policy_document_requirements
         WHERE policy_version_id = %s
           AND applicant_type = %s::applicant_type
           AND is_mandatory = true
         ORDER BY document_type ASC
        """,
        (policy_version_id, applicant_type),
    )
    return [dict(item) for item in await rows.fetchall()]


async def load_extracted_fields_for_run(
    connection: Any,
    *,
    application_id: UUID,
    processing_run_id: UUID,
) -> list[dict[str, Any]]:
    rows = await connection.execute(
        """
        SELECT
            f.id,
            f.extraction_id,
            f.field_name::text AS field_name,
            f.raw_value,
            f.normalized_value_json,
            d.document_type::text AS document_type,
            d.id AS source_document_id
          FROM document_extracted_fields AS f
          INNER JOIN document_extractions AS e
            ON e.id = f.extraction_id
           AND e.application_id = f.application_id
          INNER JOIN application_documents AS d
            ON d.id = e.application_document_id
           AND d.application_id = e.application_id
         WHERE f.application_id = %s
           AND e.processing_run_id = %s
           AND e.status = 'completed'::extraction_status
         ORDER BY f.created_at ASC, f.id ASC
        """,
        (application_id, processing_run_id),
    )
    return [dict(item) for item in await rows.fetchall()]


async def load_salary_slip_net_pays(
    connection: Any,
    *,
    application_id: UUID,
    processing_run_id: UUID,
) -> list[Any]:
    rows = await connection.execute(
        """
        SELECT s.net_pay
          FROM salary_slip_extractions AS s
          INNER JOIN document_extractions AS e
            ON e.id = s.extraction_id
           AND e.application_id = s.application_id
         WHERE s.application_id = %s
           AND e.processing_run_id = %s
           AND e.status = 'completed'::extraction_status
         ORDER BY s.pay_period_month ASC, s.id ASC
        """,
        (application_id, processing_run_id),
    )
    return [item["net_pay"] for item in await rows.fetchall()]


async def load_bank_salary_credits(
    connection: Any,
    *,
    application_id: UUID,
    processing_run_id: UUID,
) -> list[dict[str, Any]]:
    rows = await connection.execute(
        """
        SELECT
            s.statement_month,
            s.identified_salary_credits,
            e.application_document_id AS source_document_id
          FROM bank_statement_monthly_summary AS s
          INNER JOIN document_extractions AS e
            ON e.id = s.extraction_id
           AND e.application_id = s.application_id
         WHERE s.application_id = %s
           AND e.processing_run_id = %s
           AND e.status = 'completed'::extraction_status
           AND s.identified_salary_credits > 0
         ORDER BY s.statement_month ASC, s.id ASC
        """,
        (application_id, processing_run_id),
    )
    return [dict(item) for item in await rows.fetchall()]


async def load_statement_periods(
    connection: Any,
    *,
    application_id: UUID,
    processing_run_id: UUID,
) -> list[dict[str, Any]]:
    rows = await connection.execute(
        """
        SELECT
            e.application_document_id AS source_document_id,
            MAX(
                CASE
                    WHEN f.field_name = 'statement_start_date'::extraction_field_name
                    THEN COALESCE(
                        f.normalized_value_json ->> 'value',
                        f.raw_value
                    )
                END
            ) AS start_raw,
            MAX(
                CASE
                    WHEN f.field_name = 'statement_end_date'::extraction_field_name
                    THEN COALESCE(
                        f.normalized_value_json ->> 'value',
                        f.raw_value
                    )
                END
            ) AS end_raw
          FROM document_extracted_fields AS f
          INNER JOIN document_extractions AS e
            ON e.id = f.extraction_id
           AND e.application_id = f.application_id
         WHERE f.application_id = %s
           AND e.processing_run_id = %s
           AND e.status = 'completed'::extraction_status
           AND f.field_name IN (
                'statement_start_date'::extraction_field_name,
                'statement_end_date'::extraction_field_name
           )
         GROUP BY e.application_document_id, e.id
         ORDER BY e.id ASC
        """,
        (application_id, processing_run_id),
    )
    return [dict(item) for item in await rows.fetchall()]


async def fetch_latest_verification_run(
    connection: Any,
    *,
    application_id: UUID,
) -> dict[str, Any] | None:
    """Latest verification_runs row for an application (tenant-scoped)."""
    row = await connection.execute(
        """
        SELECT
            id,
            application_id,
            processing_run_id,
            input_snapshot_id,
            total_rules_evaluated,
            passed_count,
            flagged_count,
            failed_count,
            created_at
          FROM verification_runs
         WHERE application_id = %s
         ORDER BY created_at DESC, id DESC
         LIMIT 1
        """,
        (application_id,),
    )
    result = await row.fetchone()
    return dict(result) if result is not None else None


async def fetch_verification_run_by_id(
    connection: Any,
    *,
    application_id: UUID,
    run_id: UUID,
) -> dict[str, Any] | None:
    """Fetch a verification run only when it belongs to application_id."""
    row = await connection.execute(
        """
        SELECT
            id,
            application_id,
            processing_run_id,
            input_snapshot_id,
            total_rules_evaluated,
            passed_count,
            flagged_count,
            failed_count,
            created_at
          FROM verification_runs
         WHERE id = %s
           AND application_id = %s
        """,
        (run_id, application_id),
    )
    result = await row.fetchone()
    return dict(result) if result is not None else None


async def fetch_findings_for_run(
    connection: Any,
    *,
    application_id: UUID,
    verification_run_id: UUID,
) -> list[dict[str, Any]]:
    """Findings for a run, tenant-scoped and ordered by rule_code."""
    rows = await connection.execute(
        """
        SELECT
            id,
            application_id,
            verification_run_id,
            rule_code,
            rule_name,
            status::text AS status,
            severity::text AS severity,
            message,
            diff_payload_json,
            provenance_sources_json,
            claim_fence,
            created_at
          FROM verification_findings
         WHERE verification_run_id = %s
           AND application_id = %s
         ORDER BY rule_code ASC
        """,
        (verification_run_id, application_id),
    )
    return [dict(item) for item in await rows.fetchall()]

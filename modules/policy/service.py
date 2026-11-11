"""Policy document ingestion, lexical chunking, and active selection (M06-T02)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import ExclusionViolation, UniqueViolation
from psycopg_pool import AsyncConnectionPool

from modules.governance.audit import append_audit_event
from modules.policy import repository
from modules.policy.chunker import chunk_policy_markdown
from modules.policy.schemas import (
    CreatePolicyDocumentRequest,
    CreatePolicyVersionRequest,
    PolicyChunk,
    PolicyCitationsResponse,
    PolicyDocumentRequirementResponse,
    PolicyDocumentResponse,
    PolicyThresholdConfigResponse,
    PolicyVersionResponse,
    PolicyVersionWithRules,
)
from modules.policy.search import (
    DEFAULT_CITATION_TOPICS,
    retrieve_relevant_citations,
)
from shared.errors import AppError


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _document_response(row: dict[str, Any]) -> PolicyDocumentResponse:
    return PolicyDocumentResponse(
        id=row["id"],
        title=row["title"],
        lender_label=row["lender_label"],
        created_by_user_id=row["created_by_user_id"],
        created_at=row["created_at"],
    )


def _version_response(row: dict[str, Any]) -> PolicyVersionResponse:
    return PolicyVersionResponse(
        id=row["id"],
        policy_document_id=row["policy_document_id"],
        version_label=row["version_label"],
        status=row["status"],
        annual_interest_rate_pct=row["annual_interest_rate_pct"],
        effective_from=row["effective_from"],
        effective_to=row["effective_to"],
        created_by_user_id=row["created_by_user_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _threshold_response(row: dict[str, Any]) -> PolicyThresholdConfigResponse:
    return PolicyThresholdConfigResponse(
        id=row["id"],
        policy_version_id=row["policy_version_id"],
        applicant_type=row["applicant_type"],
        min_monthly_income=row["min_monthly_income"],
        max_foir_pct=row["max_foir_pct"],
        min_employment_months=row["min_employment_months"],
        max_loan_amount=row["max_loan_amount"],
        created_at=row["created_at"],
    )


def _requirement_response(row: dict[str, Any]) -> PolicyDocumentRequirementResponse:
    return PolicyDocumentRequirementResponse(
        id=row["id"],
        policy_version_id=row["policy_version_id"],
        applicant_type=row["applicant_type"],
        document_type=row["document_type"],
        is_mandatory=row["is_mandatory"],
        created_at=row["created_at"],
    )


def _chunk_response(row: dict[str, Any]) -> PolicyChunk:
    return PolicyChunk(
        id=row["id"],
        policy_version_id=row["policy_version_id"],
        chunk_index=row["chunk_index"],
        section_label=row["section_label"],
        content_text=row["content_text"],
        created_at=row["created_at"],
    )


async def create_policy_document(
    pool: AsyncConnectionPool,
    *,
    title: str,
    lender_label: str,
    created_by_user_id: UUID,
) -> PolicyDocumentResponse:
    """Create a policy document shell. Versions are added separately."""
    payload = CreatePolicyDocumentRequest(title=title, lender_label=lender_label)
    now = _utcnow()
    async with pool.connection() as conn:
        async with conn.transaction():
            row = await repository.insert_policy_document(
                conn,
                title=payload.title,
                lender_label=payload.lender_label,
                created_by_user_id=created_by_user_id,
                created_at=now,
            )
    return _document_response(row)


async def create_policy_version(
    pool: AsyncConnectionPool,
    *,
    policy_document_id: UUID,
    version_label: str,
    annual_interest_rate_pct: Any,
    effective_from: date,
    effective_to: date | None,
    thresholds: list[Any],
    doc_requirements: list[Any],
    created_by_user_id: UUID,
) -> PolicyVersionResponse:
    """
    Create a draft policy version with threshold configs and document requirements.

    Date ranges must satisfy effective_to IS NULL OR effective_to >= effective_from.
    """
    payload = CreatePolicyVersionRequest(
        version_label=version_label,
        annual_interest_rate_pct=annual_interest_rate_pct,
        effective_from=effective_from,
        effective_to=effective_to,
        thresholds=thresholds,
        doc_requirements=doc_requirements,
    )
    now = _utcnow()

    async with pool.connection() as conn:
        async with conn.transaction():
            document = await repository.fetch_policy_document(
                conn,
                policy_document_id=policy_document_id,
            )
            if document is None:
                raise AppError(
                    code="POLICY_DOCUMENT_NOT_FOUND",
                    message="Policy document not found.",
                    status_code=404,
                    details={"policy_document_id": str(policy_document_id)},
                )

            try:
                version = await repository.insert_policy_version(
                    conn,
                    policy_document_id=policy_document_id,
                    version_label=payload.version_label,
                    annual_interest_rate_pct=payload.annual_interest_rate_pct,
                    effective_from=payload.effective_from,
                    effective_to=payload.effective_to,
                    created_by_user_id=created_by_user_id,
                    created_at=now,
                    updated_at=now,
                )
            except UniqueViolation as exc:
                raise AppError(
                    code="POLICY_VERSION_LABEL_CONFLICT",
                    message="A version with this label already exists for the document.",
                    status_code=409,
                    details={
                        "policy_document_id": str(policy_document_id),
                        "version_label": payload.version_label,
                    },
                ) from exc

            for threshold in payload.thresholds:
                await repository.insert_threshold_config(
                    conn,
                    policy_version_id=version["id"],
                    applicant_type=threshold.applicant_type,
                    min_monthly_income=threshold.min_monthly_income,
                    max_foir_pct=threshold.max_foir_pct,
                    min_employment_months=threshold.min_employment_months,
                    max_loan_amount=threshold.max_loan_amount,
                    created_at=now,
                )

            for requirement in payload.doc_requirements:
                await repository.insert_document_requirement(
                    conn,
                    policy_version_id=version["id"],
                    applicant_type=requirement.applicant_type,
                    document_type=requirement.document_type,
                    is_mandatory=requirement.is_mandatory,
                    created_at=now,
                )

    return _version_response(version)


async def ingest_policy_chunks(
    pool: AsyncConnectionPool,
    *,
    policy_version_id: UUID,
    markdown_text: str,
) -> list[PolicyChunk]:
    """
    Lexically chunk markdown by heading boundaries and insert policy_chunks rows.

    PostgreSQL generates content_tsv. No embeddings are created.
    """
    drafts = chunk_policy_markdown(markdown_text)
    if not drafts:
        raise AppError(
            code="POLICY_CHUNKS_EMPTY",
            message="Markdown produced no heading sections to ingest.",
            status_code=400,
            details={"policy_version_id": str(policy_version_id)},
        )

    now = _utcnow()
    async with pool.connection() as conn:
        async with conn.transaction():
            version = await repository.fetch_policy_version(
                conn,
                policy_version_id=policy_version_id,
            )
            if version is None:
                raise AppError(
                    code="POLICY_VERSION_NOT_FOUND",
                    message="Policy version not found.",
                    status_code=404,
                    details={"policy_version_id": str(policy_version_id)},
                )
            if version["status"] != "draft":
                raise AppError(
                    code="POLICY_VERSION_NOT_DRAFT",
                    message="Chunks may only be ingested for draft policy versions.",
                    status_code=409,
                    details={
                        "policy_version_id": str(policy_version_id),
                        "status": version["status"],
                    },
                )

            await repository.delete_chunks_for_version(
                conn,
                policy_version_id=policy_version_id,
            )

            inserted: list[dict[str, Any]] = []
            for index, draft in enumerate(drafts):
                row = await repository.insert_policy_chunk(
                    conn,
                    policy_version_id=policy_version_id,
                    chunk_index=index,
                    section_label=draft.section_label,
                    content_text=draft.content_text,
                    created_at=now,
                )
                inserted.append(row)

    return [_chunk_response(row) for row in inserted]


async def activate_policy_version(
    pool: AsyncConnectionPool,
    *,
    policy_version_id: UUID,
    actor_user_id: UUID,
    request_id: str | None = None,
) -> PolicyVersionResponse:
    """
    Transition a draft version to active.

    Overlapping active date ranges fail via the btree_gist exclusion constraint.
    Emits audit action policy.activated.
    """
    now = _utcnow()
    async with pool.connection() as conn:
        async with conn.transaction():
            existing = await repository.fetch_policy_version(
                conn,
                policy_version_id=policy_version_id,
            )
            if existing is None:
                raise AppError(
                    code="POLICY_VERSION_NOT_FOUND",
                    message="Policy version not found.",
                    status_code=404,
                    details={"policy_version_id": str(policy_version_id)},
                )
            if existing["status"] != "draft":
                raise AppError(
                    code="POLICY_VERSION_NOT_DRAFT",
                    message="Only draft policy versions can be activated.",
                    status_code=409,
                    details={
                        "policy_version_id": str(policy_version_id),
                        "status": existing["status"],
                    },
                )

            try:
                activated = await repository.activate_policy_version(
                    conn,
                    policy_version_id=policy_version_id,
                    updated_at=now,
                )
            except ExclusionViolation as exc:
                raise AppError(
                    code="POLICY_ACTIVE_RANGE_CONFLICT",
                    message=(
                        "Active policy date range overlaps an existing active version."
                    ),
                    status_code=409,
                    details={
                        "policy_version_id": str(policy_version_id),
                        "effective_from": existing["effective_from"].isoformat(),
                        "effective_to": (
                            existing["effective_to"].isoformat()
                            if existing["effective_to"] is not None
                            else None
                        ),
                    },
                ) from exc

            if activated is None:
                raise AppError(
                    code="POLICY_VERSION_NOT_DRAFT",
                    message="Only draft policy versions can be activated.",
                    status_code=409,
                    details={"policy_version_id": str(policy_version_id)},
                )

            await append_audit_event(
                conn,
                action="policy.activated",
                actor_user_id=actor_user_id,
                entity_type="policy_version",
                entity_id=policy_version_id,
                before_json={
                    "status": "draft",
                    "version_label": existing["version_label"],
                },
                after_json={
                    "status": "active",
                    "version_label": activated["version_label"],
                    "effective_from": activated["effective_from"].isoformat(),
                    "effective_to": (
                        activated["effective_to"].isoformat()
                        if activated["effective_to"] is not None
                        else None
                    ),
                },
                request_id=request_id,
            )

    return _version_response(activated)


async def select_active_policy_version_on_connection(
    conn: AsyncConnection[Any],
    *,
    as_of_date: date,
) -> PolicyVersionWithRules | None:
    """
    Select the single active policy covering as_of_date on an open connection.

    Returns None when zero versions match. Raises when more than one matches.
    Used by submit so policy resolution shares the application lock transaction.
    """
    matches = await repository.list_active_policy_versions_for_date(
        conn,
        as_of_date=as_of_date,
    )
    if not matches:
        return None
    if len(matches) > 1:
        raise AppError(
            code="POLICY_AMBIGUOUS_ACTIVE",
            message="More than one active policy version covers the as-of date.",
            status_code=409,
            details={
                "as_of_date": as_of_date.isoformat(),
                "match_count": len(matches),
                "policy_version_ids": [str(row["id"]) for row in matches],
            },
        )

    version = matches[0]
    thresholds = await repository.list_threshold_configs(
        conn,
        policy_version_id=version["id"],
    )
    requirements = await repository.list_document_requirements(
        conn,
        policy_version_id=version["id"],
    )

    base = _version_response(version)
    return PolicyVersionWithRules(
        **base.model_dump(),
        thresholds=[_threshold_response(row) for row in thresholds],
        doc_requirements=[_requirement_response(row) for row in requirements],
    )


async def select_active_policy_version(
    pool: AsyncConnectionPool,
    *,
    as_of_date: date,
) -> PolicyVersionWithRules | None:
    """
    Select the single active policy covering as_of_date (Architecture §9 / ADR-016).

    Returns None when zero versions match. Raises when more than one matches.
    Joins threshold configs and document requirements for the selected version.
    """
    async with pool.connection() as conn:
        return await select_active_policy_version_on_connection(
            conn,
            as_of_date=as_of_date,
        )


async def get_application_policy_citations(
    pool: AsyncConnectionPool,
    *,
    application_id: UUID,
    topics: list[str] | None = None,
) -> PolicyCitationsResponse:
    """
    Lexically retrieve citations for an application's frozen policy version.

    Uses the latest input snapshot's ``applicable_policy_version_id`` (ADR-016).
    Dense retrieval remains off (ADR-020).
    """
    query_topics = list(topics) if topics is not None else list(DEFAULT_CITATION_TOPICS)

    async with pool.connection() as conn:
        application = await conn.execute(
            """
            SELECT id
              FROM applications
             WHERE id = %s
            """,
            (application_id,),
        )
        if await application.fetchone() is None:
            raise AppError(
                code="APPLICATION_NOT_FOUND",
                message="Application not found.",
                status_code=404,
                details={"application_id": str(application_id)},
            )

        snapshot_row = await conn.execute(
            """
            SELECT applicable_policy_version_id
              FROM application_input_snapshots
             WHERE application_id = %s
             ORDER BY snapshot_number DESC
             LIMIT 1
            """,
            (application_id,),
        )
        snapshot = await snapshot_row.fetchone()
        if snapshot is None:
            raise AppError(
                code="POLICY_CITATIONS_SNAPSHOT_MISSING",
                message="No input snapshot found for this application.",
                status_code=404,
                details={"application_id": str(application_id)},
            )

        policy_version_id = snapshot["applicable_policy_version_id"]
        if policy_version_id is None:
            raise AppError(
                code="POLICY_CITATIONS_VERSION_MISSING",
                message="Application snapshot has no applicable policy version.",
                status_code=409,
                details={"application_id": str(application_id)},
            )

        citations = await retrieve_relevant_citations(
            conn,
            policy_version_id,
            query_topics,
        )

    return PolicyCitationsResponse(
        application_id=application_id,
        policy_version_id=policy_version_id,
        citations=citations,
    )

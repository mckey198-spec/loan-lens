"""Lexical policy chunk retrieval via PostgreSQL full-text search (ADR-016, ADR-020).

Dense retrieval / embeddings are deferred (U-04 / M16). All queries must be
scoped to a single ``policy_version_id`` — never global across versions.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from modules.policy.schemas import PolicyChunkMatch

# Standard underwriting clause queries for citation retrieval.
DEFAULT_CITATION_TOPICS: tuple[str, ...] = (
    "foir limit",
    "minimum income",
    "employment tenure",
    "mandatory documents",
)

# Map failed/flagged BR-* codes to lexical query topics.
RULE_CITATION_TOPICS: dict[str, str] = {
    "BR-07": "employment tenure",
    "BR-08": "minimum income",
    "BR-11": "mandatory documents",
}


async def search_policy_chunks(
    connection: AsyncConnection[Any],
    policy_version_id: UUID,
    query_text: str,
    limit: int = 5,
) -> list[PolicyChunkMatch]:
    """
    Full-text search ``policy_chunks`` strictly within ``policy_version_id``.

    Uses ``content_tsv @@ plainto_tsquery('english', ...)`` and ranks with
    ``ts_rank_cd``. No vector embeddings or embedding API calls.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")

    cleaned = query_text.strip()
    if not cleaned:
        return []

    rows = await connection.execute(
        """
        SELECT id AS chunk_id,
               section_label,
               content_text,
               ts_rank_cd(
                   content_tsv,
                   plainto_tsquery('english', %s)
               ) AS rank_score
          FROM policy_chunks
         WHERE policy_version_id = %s
           AND content_tsv @@ plainto_tsquery('english', %s)
         ORDER BY ts_rank_cd(
                   content_tsv,
                   plainto_tsquery('english', %s)
               ) DESC,
               chunk_index ASC
         LIMIT %s
        """,
        (cleaned, policy_version_id, cleaned, cleaned, limit),
    )
    matches: list[PolicyChunkMatch] = []
    for row in await rows.fetchall():
        matches.append(
            PolicyChunkMatch(
                chunk_id=row["chunk_id"],
                section_label=row["section_label"],
                content_text=row["content_text"],
                rank_score=float(row["rank_score"]),
            )
        )
    return matches


async def retrieve_relevant_citations(
    connection: AsyncConnection[Any],
    policy_version_id: UUID,
    topics: list[str],
) -> dict[str, list[PolicyChunkMatch]]:
    """
    Retrieve lexical citation chunks for each topic against one policy version.

    Empty topic results are returned as ``[]`` (insufficient evidence for that
    query). Search never crosses policy versions.
    """
    citations: dict[str, list[PolicyChunkMatch]] = {}
    for topic in topics:
        key = topic.strip()
        if not key:
            continue
        if key in citations:
            continue
        citations[key] = await search_policy_chunks(
            connection,
            policy_version_id,
            key,
            limit=5,
        )
    return citations


def topics_for_findings(
    *,
    verification_findings: list[dict[str, Any]] | None,
    financial_calculation: dict[str, Any] | None,
    include_defaults: bool = True,
) -> list[str]:
    """
    Build citation query topics from failed/flagged rules and FOIR outcome.

    When no finding-derived topics exist and ``include_defaults`` is true,
    returns the standard underwriting topic list.
    """
    topics: list[str] = []
    seen: set[str] = set()

    def _add(topic: str) -> None:
        if topic not in seen:
            seen.add(topic)
            topics.append(topic)

    for finding in verification_findings or []:
        status = finding.get("status")
        if status not in ("failed", "flagged"):
            continue
        rule_code = finding.get("rule_code")
        if not isinstance(rule_code, str):
            continue
        mapped = RULE_CITATION_TOPICS.get(rule_code)
        if mapped is not None:
            _add(mapped)

    if financial_calculation is not None and financial_calculation.get(
        "foir_passed"
    ) is False:
        _add("foir limit")

    if not topics and include_defaults:
        for topic in DEFAULT_CITATION_TOPICS:
            _add(topic)

    return topics

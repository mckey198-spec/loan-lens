# LoanLens v1.2 consistency report

> **Superseded by [CONSISTENCY_REPORT.md](./CONSISTENCY_REPORT.md) (v1.3, 04 November 2026).** The open items below are the v1.2 record. They are not the current design status.

| Attribute | Value |
|---|---|
| Date | 04 November 2026 |
| Compared | `TECHNICAL_ARCHITECTURE.md` v1.2, `DATABASE_DESIGN.md` v1.2, `LoanLens_Final_PRD_v1.0.docx` |
| Scope | The eight integrity items in the v1.2 review, plus carry-forward product gaps |

This report states where the three documents agree and where they do not. An open item is not described as fixed.

---

## Agreed (architecture and database match each other and the PRD)

| Topic | PRD | v1.2 architecture and schema |
|---|---|---|
| Decision support | No automatic approve/reject. Reviewer records the outcome. | Unchanged. Outcome insert is Reviewer-only. |
| Declared net income | Required, ≥ 0, ≤ gross. Not overwritten by extraction. | `applicants.net_monthly_income` NOT NULL with those CHECKs. Snapshot copies the declared amount. |
| Not computable | Zero, missing, or insufficiently verified denominator. No divide-by-zero. Insufficient-evidence finding. Conflicting values shown, not auto-picked. Provisional only when labelled. | `snapshot_calculation_inputs.status` is `missing`, `unverified`, `disputed`, or `verified`. FOIR/LTI trigger: `computed` only if net income is `verified` and `> 0`. `disputed` / `missing` / zero cannot be provisional. `result_numeric` NULL when `not_computable`. |
| Dismiss vs Critical | FR-13 and §11: Reviewer may dismiss with a reason; dismissed findings stay visible. §7.2 / §10.1: Critical and insufficient evidence need resolved, overridden, or decision-without-evidence. | Enum keeps `dismissed`. Outcome trigger ignores it for the Critical / insufficient-evidence gate. ADR-014. |
| Previous outcomes | Reopen keeps the previous decision and creates a new assessment version. | `review_outcomes` has no update-in-place supersession columns. `review_outcome_supersessions` is insert-only. ADR-010. |
| Document failure visibility | Failure is never hidden or treated as success. | `document_failure_events` are not updated. `document_failure_resolutions` reference the event. ADR-013. |
| Page provenance | Source document and page on extracted fields. | `document_page_id` composite FK to run-scoped `document_pages`. |
| Same-application evidence | Findings and fields belong to the application under review. | Composite `(id, application_id)` foreign keys. ADR-011. |
| Policy on an assessment | Record the policy version used. Select by effective date, not upload date. Do not invent clauses. | `submitted_at` → UTC as-of date. Assessment `policy_version_id` NOT NULL and must equal the snapshot. Chunk-level misses stay insufficient-evidence **after** a policy is selected. ADR-016. |
| No policy version | “Record the policy version used” cannot be satisfied by inventing one. | Zero matches: run `failed`, `no_applicable_policy`, no assessment row. Overlapping active ranges: exclusion constraint, and `ambiguous_applicable_policy` if more than one row still matches. |
| Idempotent processing | Reliability NFR: retryable, idempotent, recorded failure. Redis only if measured. | PostgreSQL `SKIP LOCKED`, `fence_token`, `claim_fence`, logical unique keys. ADR-006 and ADR-015. |
| Audit | Append-only significant actions. | Global `chain_seq`, singleton `FOR UPDATE`, function-only insert, hash verifier. ADR-012. |
| Deterministic vs model | Arithmetic in code. Anomaly signals are informational. AI observations labelled and separate. | BR-01…BR-11 and checklist → `findings`. BR-12 → `ai_observations` only. |
| Duplicate uploads | Same content hash on one application: warn, do not store a second active copy. | Partial unique `(application_id, content_sha256)` for non-replaced documents. |

---

## Open contradictions and undecided items

These are **not** fixed.

| ID | Conflict | Status |
|---|---|---|
| U-01 | PRD §7 says the lifecycle has **ten** states and does not name Needs Documents. Architecture and the `application_status` enum define **eleven**, including `needs_documents` (ADR-005). | Open. Needs a PRD amendment or an explicit product decision before external docs claim eleven states. |
| U-02 | After outcome `referred_for_more_information`, the architecture allows either Needs Documents or Clarification required. The PRD says that outcome follows the clarification/reassessment workflow and does not name Needs Documents. | Open. Automatic transition is not specified. |
| U-03 | Session vs JWT. | Open implementation choice. PRD only requires authenticated calls and RBAC. |
| U-04 | Embedding dimension `N`. | Open until the model is chosen. |
| U-06 | `assessment_calculations.insufficient_evidence_finding_id` is specified as required for `not_computable`, but insert order may prevent a hard CHECK. | Open. The net-income **status** trigger is specified; this FK timing is not closed. |
| U-07 | PRD forbids calling PostgreSQL FTS “BM25” unless a BM25 engine is chosen. | No contradiction. Upgrade remains future work. |
| U-08 | `document_requests` is optional in the schema. The Needs Documents **state** does not depend on that table. | Open whether the table is in the MVP cut. |

---

## Product changes that would need PRD approval

v1.2 does **not** adopt these. They are listed so they are not mistaken for current behaviour.

| Proposal | Why it needs the PRD |
|---|---|
| Count Needs Documents as already specified by PRD §7 | §7 still says ten states. |
| Let `dismissed` satisfy the Critical / insufficient-evidence gate | §7.2 lists three dispositions and does not include dismiss. |
| Insert an assessment with a null policy when none applies | §10 and §5.3 require the assessment to record the policy version used, and forbid invented policy. |
| Auto-pick one of several overlapping active policies | The PRD does not authorise a silent choice. v1.2 fails the run instead. |

---

## Checks performed

- Both design documents are version 1.2 and cite ADRs 009–016 for the same decisions.
- `review_outcomes` column list has no `superseded` fields; supersession is a separate table in both documents.
- Failure facts and handling are different tables and different enums in both documents.
- `dismissed` is in the enum and is excluded from the outcome gate in both documents.
- Policy failure codes `no_applicable_policy` and `ambiguous_applicable_policy` appear as run failures with no assessment row in both documents.
- Worker claim increments `fence_token`; side effects carry `claim_fence`; logical unique keys match across §6 of the architecture and the schema.
- Calculation-input statuses and the FOIR/LTI trigger predicate match across §8 of the architecture and `snapshot_calculation_inputs`.

---

*End of consistency report v1.2*

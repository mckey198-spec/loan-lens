# Architecture Revision Change Log

| Attribute | Value |
|---|---|
| Product | LoanLens |
| From | TECHNICAL_ARCHITECTURE.md / DATABASE_DESIGN.md **v1.0** (03 Nov 2026) |
| To | **v1.3** (04 Nov 2026). v1.1 and v1.2 sections are retained below as history |
| PRD baseline | `LoanLens_Final_PRD_v1.0.docx` (unchanged) |
| Current consistency | [CONSISTENCY_REPORT.md](./CONSISTENCY_REPORT.md) |

This change log explains each modification and why it was made. Product behaviour remains governed by the PRD. Sections through v1.2 are history, including decisions that v1.3 withdraws. The current status is the v1.3 section at the end of this file.

**Current status (v1.3).** ADR-005 (`needs_documents`) is withdrawn. U-01, U-02, U-03, U-06, U-07, and U-08 are closed. U-04 (embedding model and dimension) is an explicit deferred implementation decision and is the only remaining open item. U-05 was already closed in v1.2. No PRD amendment is made or required by v1.3.

---

## Modifications

### 1. Needs Documents application state

| Change | Reason |
|---|---|
| Added `needs_documents` to `application_status` and lifecycle diagrams in both documents | Correction #1: document-wait must be first-class and consistent across API, DB, and UI |
| Defined ownership (Loan Officer), entry/exit transitions, and distinction from `clarification_required` | Clarification is Q&A-centric; Needs Documents is upload/replace-centric |
| Added ADR-005 | Preserve decision rationale; flag PRD §7 “ten states” alignment |
| Added optional `document_requests` table | Structured checklist for requested document types without overloading `clarifications` |

### 2. Nullable financial inputs and Not Computable handling

| Change | Reason |
|---|---|
| Removed live `annual_interest_rate_pct` from `applications`; rate lives on `policy_versions` and is frozen on input snapshots / calc inputs | PRD §9: rate supplied by applicable policy, not free-typed as a derived field |
| Kept required declared incomes NOT NULL; optional amounts (`other_monthly_income`, expenses, outstanding) remain NULL-able with `>= 0` when present | Matches PRD required/optional dictionary; avoids false zeros |
| Introduced `calculation_status` enum and CHECKs: `not_computable ⇒ result_numeric IS NULL`; `computed ⇒ result_numeric NOT NULL` | Correction #2: prevent storing fake ratios; encode Not computable in schema |
| Documented that “missing/unverified for calc” ≠ “null declared column” | PRD §9.1: zero/missing/**not verified** denominators → Not computable + insufficient-evidence finding |
| Linked `insufficient_evidence_finding_id` on calculations | Traceability from calc status to finding |

### 3. Explicit, auditable document failure dispositions

| Change | Reason |
|---|---|
| Added `document_failure_kind` enum and append-only `document_failure_dispositions` | Correction #3; PRD §7.2 / §15.2 require visible, non-silent failure handling |
| Removed reliance on a single mutable `failure_reason` column as the audit record | History must survive retries/replacements |

### 4. Immutable findings vs append-only review dispositions

| Change | Reason |
|---|---|
| Removed mutable `status` / disposition columns from `findings` | Correction #4; ADR-007 |
| Added `finding_dispositions` with required reason and unresolved-visibility check for `decision_without_evidence` | PRD §7.2 dispositions must remain auditable |
| Effective status = latest disposition or `open` | Review UI/PDF derive current state without rewriting facts |

### 5. Traceable review outcomes and historical assessments

| Change | Reason |
|---|---|
| Strengthened `review_outcomes`: `superseded_at`, `superseded_by_outcome_id`, unique per `assessment_id`, one current outcome per application | Correction #5; reopen must not erase history (PRD §11) |
| Assessments uniquely tied to `processing_run_id` and `input_snapshot_id` | Every version points at the exact run and frozen inputs |
| Added reproducibility checklist (§11 in DATABASE_DESIGN) | Operational definition of “historically reproducible” |

### 6. Versioned applicant/application input snapshots

| Change | Reason |
|---|---|
| Added `application_input_snapshots` and ADR-008 | Correction #6; live applicant edits must not rewrite past assessments |
| `processing_runs.input_snapshot_id` and `assessments.input_snapshot_id` required | End-to-end freeze from enqueue through report |

### 7. Application-scoped document deduplication and concurrency

| Change | Reason |
|---|---|
| Denormalized `content_sha256` onto `application_documents` | Unique constraint cannot join through `stored_files` reliably under concurrency |
| Partial unique index on `(application_id, content_sha256)` for non-replaced docs | Fixes incorrect v1.0 uniqueness on `(application_id, stored_file_id)` |
| Documented unique-violation → warn / no second active copy | Aligns with PRD duplicate rule under race conditions |
| Retained partial unique active processing run; added idempotency key uniqueness | Concurrency safety for enqueue |

### 8. Background worker, queue, checkpointing, retry, idempotency

| Change | Reason |
|---|---|
| Specified PostgreSQL `SKIP LOCKED` workers, Compose `worker` service, lease fields on `processing_runs` | Correction #8; PRD reliability NFR without introducing Redis (ADR-006) |
| Added `workflow_checkpoints` | Resume after crash without redoing succeeded nodes unsafely |
| Defined provider / node / run / lease retry layers and idempotency keys | Makes “retryable, idempotent processing” implementable |
| Updated system context and deployment topology | API and worker are separate processes, same codebase |

### 9. Document-page provenance via relationships

| Change | Reason |
|---|---|
| Added `extracted_fields.document_page_id` FK → `document_pages` | Correction #9; integer `source_page` alone is weak provenance |
| Dropped standalone `source_page` column in favour of relational page identity (page_number via join) | Single source of truth |
| Recommended consistency trigger between page’s document and field’s document | Prevent cross-document page pointers |

### 10. Deterministic findings vs AI observations

| Change | Reason |
|---|---|
| Narrowed `detection_source` to `deterministic` \| `checklist` | Correction #10 |
| BR-12 and model notes → `ai_observations` only (`rule_code`, labels) | PRD: AI observations explicitly labelled and separated; anomaly ≠ decision |
| Module boundary table updated (Verification must not persist model prose as findings) | Keeps deterministic core honest |

### 11. Foreign-key, uniqueness, and check constraints

| Change | Reason |
|---|---|
| Expanded §6 integrity matrix and per-table CHECKs/partial uniques | Correction #11 |
| Added checks for loan purpose/other, archived_at, calc status, disposition unresolved flag, policy effective dates, money non-negativity | Push invariants into the database where practical |

### 12. Security and audit integrity

| Change | Reason |
|---|---|
| Hash-chained `audit_events` (`prev_event_hash`, `event_hash`) | Correction #12; tamper evidence beyond “append-only by convention” |
| Explicit append-only table list + revoke UPDATE/DELETE + triggers | Defence in depth for findings, snapshots, dispositions, outcomes, comments |
| Checkpoint freeze after run completes | Prevent rewriting history under the guise of workflow state |

### Cross-document consistency fixes

| Change | Reason |
|---|---|
| Both docs bumped to v1.1 with shared companion/changelog links | Avoid drift |
| Annual rate, Needs Documents, worker model, findings/AI split, snapshot reproducibility aligned across both documents | Resolve v1.0 contradictions (e.g. weak dedup uniqueness vs architecture prose; mutable findings vs immutability principles) |
| Implementation order updated for snapshots, dispositions, workers, AI split | Match dependency order |
| New ADRs 005–008 preserved alongside ADR-001–004 | Material decisions recorded |

---

## Unresolved decisions

| ID | Topic | Notes |
|---|---|---|
| U-01 | **PRD §7 state count** | Architecture v1.1 defines **eleven** states including Needs Documents (ADR-005). PRD v1.0 still says ten. Needs a formal PRD amendment or an approved product decision before external stakeholder docs claim eleven. |
| U-02 | **Referred → Needs Documents automation** | Whether `referred_for_more_information` *automatically* transitions to Needs Documents, or only when the Reviewer also creates `document_requests`, is left to workflow UX. Both paths are permitted by the state diagram. |
| U-03 | **Auth session vs JWT** | Still an open implementation choice (unchanged from v1.0); does not affect schema beyond session storage if cookies are chosen. |
| U-04 | **Embedding dimension N** | Depends on selected embedding model; first migration that creates `policy_chunks.embedding` freezes N. |
| U-06 | **`not_computable` ↔ finding FK timing** | Preferred rule requires `insufficient_evidence_finding_id`. Same-transaction insert order may force service enforcement plus an integration test instead of a hard CHECK. **Not fixed in v1.2.** The net-income status trigger is separate and is specified. |
| U-07 | **Lexical engine upgrade** | Remain on PostgreSQL FTS unless benchmarks justify ParadeDB/pg_search; still must not call FTS “BM25”. |
| U-08 | **`document_requests` necessity** | Table included to separate Needs Documents from clarifications; if MVP UI only needs free-text + ad hoc uploads, it can be deferred without removing the Needs Documents *state*. |

No other intentional product-requirement changes were made in v1.1. Threshold numeric values remain policy-configured and unspecified here, per PRD.

---

## v1.1 → v1.2 (database integrity)

Documents: `TECHNICAL_ARCHITECTURE.md` and `DATABASE_DESIGN.md` are both **v1.2**. ADRs 001–008 are unchanged in force. ADRs 009–016 were added.

### 1. Net monthly income and calculation-input status

**Problem.** Declared `net_monthly_income` was NOT NULL, while Not computable depended on “missing / unverified” without a frozen status. A later edit or a replay could not prove which status the ratio used. A trigger could not reject a computed FOIR.

**Consequence.** Historical assessments were not reproducible for the ratio that gates affordability, and a bug could store a number for a disputed or zero denominator.

**Change (ADR-009).**

- Declared amounts stay on `applicants` and are copied onto the snapshot.
- New append-only `snapshot_calculation_inputs` with `calc_input_status`: `missing | unverified | disputed | verified`.
- Checks: `verified` ⇒ `selected_numeric` only; `unverified` ⇒ `candidate_numeric` only; `missing` and `disputed` ⇒ neither selected nor candidate.
- Trigger on `assessment_calculations` for `foir` and `lti`: `computed` only when frozen net income is `verified` and `> 0`; `provisional` only when `unverified` and candidate `> 0`; otherwise `not_computable` and `result_numeric` NULL.
- `inputs_json` must record those frozen statuses.

**PRD.** Implements §9 / §9.1. No amendment. Required declared net income stays required.

### 2. Outcome supersession

**Problem.** v1.1 said `review_outcomes` were append-only and also updated `superseded`, `superseded_at`, and `superseded_by_outcome_id`.

**Consequence.** A reopen rewrote history. The partial unique index depended on a mutable flag.

**Change (ADR-010).**

- Those three columns are removed.
- New append-only `review_outcome_supersessions` (`outcome_id` unique, reason required, actor, timestamp).
- Trigger: at most one outcome per application that has no supersession row.
- Unique `(assessment_id)` remains.

**PRD.** Matches §11 (previous outcome preserved). No amendment.

### 3. Same-application referential integrity

**Problem.** Independent FKs allowed a run, extraction, correction, finding, or disposition to point at another application’s parent.

**Consequence.** Cross-application evidence could be attached to an assessment and would still look like a valid FK.

**Change (ADR-011).** `UNIQUE (id, application_id)` on application-scoped parents. Children use composite FKs. Catalogue in DATABASE_DESIGN §6.1. `stored_files` stays shareable; scope starts at `application_documents`.

**PRD.** No behaviour change. No amendment.

### 4. Audit hash chain

**Problem.** Two transactions could read the same tail and both insert, forking the chain. v1.1 left per-application vs global scope open (U-05).

**Consequence.** Verification could not tell which branch was authoritative.

**Change (ADR-012).** One global chain. Singleton `audit_chain_state` locked `FOR UPDATE` inside `append_audit_event`. `chain_seq` unique and gapless. Direct insert revoked. Verifier recomputes hashes and checks the singleton tail.

**PRD.** No amendment. **U-05 is decided**, not left open.

### 5. Document failure events vs resolutions

**Problem.** One enum mixed pipeline facts (`ocr_failed`) with later decisions (`accepted_insufficient_evidence`).

**Consequence.** A handling row could be read as if it replaced the fact that OCR failed.

**Change (ADR-013).** `document_failure_events` and `document_failure_resolutions`. Resolutions reference the event. Unique event key `(processing_run_id, application_document_id, kind)`. Neither table is updated.

**PRD.** §7.2 / §15.2 still require the failure to stay visible. No amendment. Accepting a gap does not by itself close a Critical finding.

### 6. `dismissed`

**Problem.** The enum included `dismissed`, while Critical text listed only three dispositions, so it was unclear whether dismiss was legal or sufficient to record an outcome.

**Change (ADR-014).** `dismissed` stays. It is the PRD §11 / FR-13 dismiss action. The outcome trigger accepts only a latest disposition in `{resolved, overridden, decision_without_evidence}` for Critical and insufficient-evidence findings.

**PRD.** This matches §7.2 and §11 together. No amendment. Letting `dismissed` close a Critical item would need PRD approval; v1.2 does not do that.

### 7. Worker fencing and logical keys

**Problem.** Idempotency was described as “keyed by processing_run_id” without unique keys, and a worker could commit after its lease was taken by another worker.

**Consequence.** Duplicate findings on replay, or a stale worker overwriting a reclaimed run.

**Change (ADR-015).** `fence_token` increments on claim and reclaim. `claim_fence` trigger. Lease expiry does not fail the run. Logical unique keys for pages, extractions, failure events, findings, calculations, AI observations, calc inputs, and checkpoints. Same key with a different payload fails the run; it does not update the row.

**PRD.** Reliability NFR only. No amendment. Still no Redis.

### 8. Policy selection when none applies

**Problem.** Assessments required `policy_version_id`, but nothing defined zero or many matching versions. `submitted_at` was not stored, so “application date” was ambiguous.

**Consequence.** Workflow might skip policy, invent a rate, or insert an assessment the schema would reject.

**Change (ADR-016).**

- `applications.submitted_at` set once.
- As-of date = UTC date of `submitted_at`.
- Exactly one active version whose range covers that date.
- Zero → `no_applicable_policy`. Many → `ambiguous_applicable_policy`.
- Both fail the run and insert no assessment.
- `btree_gist` exclusion rejects overlapping **active** ranges.
- Snapshot policy id and assessment policy id must be equal and non-null.
- Insufficient retrieved chunks remain an in-assessment insufficient-evidence result, and only after a policy was selected.

**PRD.** Agrees with “record the policy version used” and “never invent”. A partial assessment with a null policy would need PRD approval; v1.2 does not do that.

### Product decisions that still need PRD approval

| Item | Why it is not “fixed” |
|---|---|
| U-01 eleven states vs PRD “ten states” | ADR-005 remains an architecture default. The PRD text was not amended. |
| U-02 whether “Referred for more information” always enters Needs Documents | Still a workflow choice. |
| Treating `dismissed` as enough to close a Critical finding | Would contradict PRD §7.2. Not adopted. |
| Emitting an assessment when no policy version applies | Would contradict “record the policy version used” unless the PRD is changed. Not adopted. |

### Still unresolved (not claimed fixed)

U-01, U-02, U-03 (session vs JWT), U-04 (embedding dimension), U-06 (hard CHECK vs service for `insufficient_evidence_finding_id` timing), U-07 (FTS vs a future BM25 engine), U-08 (whether `document_requests` ships in MVP).

U-05 is closed by ADR-012.

---

The list above is the **v1.2** status. v1.3 closes it, except U-04, which stays deferred. Do not treat this list as the current design.

---

## v1.2 → v1.3 (PRD alignment)

Documents: `TECHNICAL_ARCHITECTURE.md` and `DATABASE_DESIGN.md` are both **v1.3**. ADRs 001–004 and 006–016 stay in force. ADR-005 is withdrawn. ADRs 017–025 were added.

The PRD text was not changed. Where v1.2 had added product behaviour the PRD does not contain, v1.3 removes it.

### 1. Ten lifecycle states (U-01)

**Problem.** ADR-005 added `needs_documents`, so the design had eleven states. PRD §7 names ten.

**Consequence.** An implementer following v1.2 would ship a state the PRD does not define, including withdrawal and referral edges that the PRD does not list.

**Change (ADR-017).** The enum and the state diagram use the ten PRD states. A trigger allows only the edges in TECHNICAL_ARCHITECTURE §5 and requires a matching `application_state_history` row. Document wait uses Draft, Clarification required, or Processing failed.

**PRD.** Matches §7. No amendment. An eleventh state would need one.

### 2. Clarification versus additional documents, and referral (U-02)

**Problem.** After `referred_for_more_information`, v1.2 allowed either Needs Documents or Clarification required and did not pick one. The PRD has one Reviewer action, Request clarification, and says a referred outcome follows the existing clarification and reassessment workflows.

**Change (ADR-018).**

- Request clarification, from `in_review` only, inserts `clarifications` and moves to `clarification_required`. The question may name documents. There is no second action.
- The Loan Officer or Admin submits the response from `clarification_required`. Text or a document uploaded after the question opened is required. That starts a new run.
- `referred_for_more_information` moves the application to `review_completed` and does not open a clarification.
- Reopen supersedes the outcome, starts a new run (`review_completed` → `processing`), and returns to `in_review` when the new assessment exists. The Reviewer may then request clarification.
- While a run is queued or running, status is `processing`, including correction reprocess and reopen. The first success goes to `awaiting_human_review`. Later success with an active assignment returns to `in_review`.

**PRD.** Uses §7 edges plus the reprocessing rule in §7.1 and §11. The intermediate `processing` status on reopen is the existing Processing state, not a new one. No amendment. An action that both records the referred outcome and enters Clarification required would add a §7 edge and would need an amendment.

### 3. Server-side sessions (U-03)

**Problem.** v1.2 left session versus JWT undecided.

**Change (ADR-019).** Browser authentication is a server-side session. The cookie is HttpOnly. The database stores a SHA-256 of the secret in `user_sessions`. Passwords use Argon2id. Mutating requests require a CSRF token. Logout and deactivation revoke the session. The MVP API does not accept a bearer JWT.

**Why this and not JWT.** The PRD requires authenticated calls and backend RBAC. Immediate revocation when an Admin deactivates a user fits a session row in PostgreSQL. It does not need Redis or a token denylist. The client is a first-party SPA.

**PRD.** No amendment.

### 4. Embedding dimension left deferred (U-04)

**Problem.** v1.2 said `vector(N)` and did not say whether N had been chosen.

**Change (ADR-020).** No model and no N are chosen. The migration that creates `policy_chunks.embedding` is blocked until the deployment names the embedding model. Lexical full-text search can be built before that. Dense retrieval stays off.

**PRD.** No amendment. The missing fact is which configured model the deployment will use.

### 5. Mandatory document-hash equality

**Problem.** v1.2 denormalized `content_sha256` onto `application_documents` and called the match to `stored_files` optional.

**Consequence.** The uniqueness key could name different bytes from the object that `stored_file_id` points at.

**Change (ADR-021).** A `BEFORE INSERT` trigger rejects a mismatch. Updates to either hash or to `stored_file_id` are rejected. `stored_files` is insert-only and `content_sha256` is unique.

**PRD.** File-handling NFR and the duplicate-upload rule. No amendment.

### 6. Concurrent review outcomes

**Problem.** v1.2 rejected a second unsurperseded outcome with a trigger that reads other rows. Under READ COMMITTED, two transactions can both see no active outcome and both insert. Unique `(assessment_id)` does not stop two active outcomes on two assessments.

**Change (ADR-022).** Direct insert is revoked. `record_review_outcome` and `supersede_review_outcome` lock the `applications` row `FOR UPDATE` before the check. A deferred constraint aborts a commit that leaves more than one unsurperseded outcome.

**PRD.** §11. No amendment.

### 7. Document-failure resolutions and §7.2 dispositions

**Problem.** v1.2 separated events from resolutions, which is right, and did not map those resolutions onto the PRD’s next actions and the three dispositions.

**Change.** Both documents now use the same map. Retry, replacement, and the clarification state keep the failure event visible and do not satisfy the outcome gate. `resolved`, `overridden`, and `decision_without_evidence` are finding dispositions. `accepted_insufficient_evidence` may accompany `decision_without_evidence` and does not replace it. `dismissed` still does not close a Critical or insufficient-evidence finding.

**PRD.** §7.2, §11, §15.2. No amendment.

### 8. Calculation status, result, and provisional flag

**Problem.** v1.2’s separate checks allowed `not_computable` with `is_provisional = true`, and `provisional` with a null result.

**Change (ADR-023).** One check allows only three shapes: computed (result set, not provisional, no limitation, no gap finding), not computable (null result, not provisional, gap finding required), or provisional (result set, provisional, limitation text, no gap finding). The net-income trigger still restricts which shape FOIR and loan-to-annual-income may use. Declared net income is never frozen as `missing`.

**PRD.** §9.1. No amendment.

### 9. Insufficient-evidence finding link (U-06)

**Problem.** The finding id was required in prose and optional as a constraint, because an immediate foreign key fixes the insert order.

**Change (ADR-023).** The check requires the id on every `not_computable` row. The foreign key is `DEFERRABLE INITIALLY DEFERRED`. A deferred trigger requires `findings.is_insufficient_evidence` and the same assessment. The assessment node writes the assessment, findings, and calculations in one transaction. UUIDs are allocated first. Either child order commits. The service-only exception is removed.

**PRD.** §9.1. No amendment.

### 10. Malware scan, encryption at rest, download authorization

**Problem.** v1.2 required controlled storage and TLS, and did not specify scanning, at-rest encryption, or who may download bytes.

**Change (ADR-025).** Bytes are scanned in quarantine. Only a clean scan inserts `stored_files` (`malware_scanner`, `malware_scanned_at`). Infected files and scanner errors do not become documents. Volumes are encrypted at rest; S3-compatible storage uses server-side encryption. Download is `GET .../documents/{id}/content` for an active Loan Officer, Reviewer, or Admin. The storage URI is not returned. The download is audited. MVP Compose runs ClamAV, replaceable behind those columns.

**PRD.** §12 file handling and §13.1. No new lending requirement. No amendment.

### 11. Full-text search is not BM25 (U-07)

**Change.** Both documents say PostgreSQL full-text search is the lexical engine, that it is not BM25, and that it is not equivalent to BM25. A BM25 engine remains a later, measured decision. No wording equates `ts_rank` with BM25.

**PRD.** §12.1. No amendment.

### 12. `document_requests` deferred (U-08)

**Problem.** v1.2 included an optional checklist table and left MVP inclusion open.

**Change (ADR-024).** The table is not in the MVP schema. Clarification text names any documents. The documented limitation is that the MVP has no per-type open/fulfilled checklist.

**PRD.** Request clarification is the Must action. No amendment.

### Decisions that stay rejected

| Proposal | Why it is still not adopted |
|---|---|
| Let `dismissed` close a Critical or insufficient-evidence finding | PRD §7.2 lists three dispositions. |
| Insert an assessment with a null policy | PRD requires the policy version used, and forbids invented policy. |
| Auto-pick one of several overlapping active policies | The PRD does not authorise a silent choice. |
| Add `needs_documents` without a PRD amendment | Withdrawn in v1.3. |

### Open after v1.3

| ID | Status |
|---|---|
| U-04 | **Open / deferred.** Embedding provider, model, and vector dimension N. Blocks only `policy_chunks.embedding`. |
| U-01, U-02, U-03, U-05, U-06, U-07, U-08 | Closed. See the sections above. |

---

*End of Architecture Revision Change Log (through v1.3)*

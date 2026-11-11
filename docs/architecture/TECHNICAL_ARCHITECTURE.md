# LoanLens Technical Architecture

| Attribute | Value |
|---|---|
| Product | LoanLens |
| Document | System Architecture & Module-Boundary Specification |
| Version | 1.3 |
| Date | 04 November 2026 |
| Status | Baseline for MVP implementation (PRD alignment revision) |
| Source of truth (product) | `LoanLens_Final_PRD_v1.0.docx` |
| Companion | [DATABASE_DESIGN.md](./DATABASE_DESIGN.md) |
| Revision notes | [ARCHITECTURE_REVISION_CHANGELOG.md](./ARCHITECTURE_REVISION_CHANGELOG.md) |
| Consistency | [CONSISTENCY_REPORT.md](./CONSISTENCY_REPORT.md) |

**Version history.** 1.0 (03 Nov 2026) initial baseline. 1.1–1.2 (04 Nov 2026) integrity revision. 1.3 (04 Nov 2026) aligns the lifecycle, review actions, and integrity controls with PRD v1.0. ADR-005 is withdrawn.

**Purpose.** Define *how* LoanLens is built: module boundaries, runtime topology, workflow, providers, security, and deployment. Product behaviour remains governed by the PRD. Material scope changes require a PRD revision.

---

## 1. Architecture principles

1. **Decision support only.** No code path may auto-approve or auto-reject an application.
2. **Traceability.** Every material finding links to a document/page, a deterministic calculation with inputs, or an explicitly labelled model observation.
3. **Deterministic core.** Verification rules and financial arithmetic run in tested application code—not LLM judgement.
4. **Provenance over confidence.** Extracted fields keep source page (relational FK where possible), evidence text, method, and confidence. Confidence is never treated as authenticity or correctness.
5. **Four value layers stay distinct.** `extracted` → `normalized` → `verified` → `human_corrected`. Corrections never overwrite original extraction.
6. **Version everything that affects an assessment.** Policy, business rules, prompts, models/providers, embeddings/rerankers, evaluation dataset versions, **and the applicant/loan input snapshot** are recorded with each assessment so any historical version is reproducible.
7. **Backend-enforced RBAC.** UI hiding is not authorization.
8. **Untrusted uploads.** Document content is hostile input (including prompt-injection text).
9. **Explicit failure.** Provider outages, OCR failures, and unreadable evidence are visible; no silent provider fallback and no silent success. Document failures carry auditable dispositions.
10. **Modular monolith first.** Clear module boundaries inside one deployable. Redis/microservices only when justified by measured need.
11. **Immutable facts, append-only dispositions.** System-generated findings and assessments are immutable; human review dispositions, comments, outcomes, and audit events are append-only.
12. **Deterministic findings ≠ AI observations.** Reconciliation findings (BR-01…BR-11 and checklist rules) are stored separately from labelled model observations (including BR-12 anomaly signals).

---

## 2. System context

```text
┌─────────────────┐     HTTPS/REST      ┌──────────────────────────────────────┐
│  Web UI         │ ─────────────────── │  LoanLens API (modular monolith)     │
│  (Loan Officer, │                     │  /api/v1                             │
│   Reviewer,     │                     │                                      │
│   Admin)        │                     │  ┌──────── Intake / Auth / RBAC ───┐ │
└─────────────────┘                     │  │  Document Intelligence          │ │
                                        │  │  Verification & Reconciliation  │ │
                                        │  │  Financial Analysis             │ │
                                        │  │  Policy Intelligence            │ │
                                        │  │  Assessment & Reporting         │ │
                                        │  │  Workflow Orchestration         │ │
                                        │  │  Observability & Governance     │ │
                                        │  └─────────────────────────────────┘ │
                                        └───────────────┬──────────────────────┘
                                                        │
              ┌─────────────────┬───────────────────────┼──────────────────┐
              ▼                 ▼                       ▼                  ▼
     ┌────────────────┐ ┌──────────────┐    ┌──────────────────┐  ┌──────────────┐
     │ PostgreSQL     │ │ Object store │    │ LLM / Embedding  │  │ OCR /        │
     │ + pgvector     │ │ (local/S3-   │    │ / Rerank         │  │ Extraction   │
     │ system of      │ │ compatible)  │    │ providers        │  │ providers    │
     │ record + job   │ │ originals +  │    │ (configurable)   │  │ (configurable)│
     │ queue/checkpoints│ │ hashes       │    └──────────────────┘  └──────────────┘
     └────────────────┘ └──────────────┘
```

**MVP integrations:** none beyond document upload and configured AI/OCR providers. No credit bureau, LOS, or core banking.

**Demo constraint:** synthetic applicants and documents only in public demonstrations.

---

## 3. Technology stack (MVP defaults)

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.12+ | Strong AI/OCR ecosystem; PRD assumes Pydantic/OpenAPI style API |
| API | FastAPI + Pydantic v2 | Versioned REST, OpenAPI, structured errors |
| App structure | Modular monolith packages | Clear boundaries without distributed complexity |
| Workflow | LangGraph (stateful graph) | Explicit states, retries, human hand-off |
| DB | PostgreSQL 16 + pgvector | System of record + embeddings + job queue |
| Lexical search | PostgreSQL full-text search (`tsvector` / `ts_rank`) | Lexical half of hybrid retrieval. This is not BM25 and is not equivalent to BM25 |
| Vector search | pgvector | Same DB; simpler ops for MVP |
| Reranker | Provider interface (commercial or local) | Required by FR-11 |
| Object storage | Local filesystem volume in Compose; S3-compatible interface | Preserve originals + content hashes |
| Auth | Server-side session cookie (HttpOnly); Argon2id password hashes; CSRF token on mutating requests | ADR-019. Authenticated users and backend RBAC on every protected endpoint |
| Background jobs | Dedicated worker process(es) + PostgreSQL-backed queue (`SKIP LOCKED`); no Redis in MVP | Long OCR/pipeline runs off request thread; matches “Redis only when measured need” |
| Containers | Docker Compose | Reproducible local + demo environment |
| Observability | Structured JSON logs; OpenTelemetry traces; LLM token/usage traces | FR-17 |
| Frontend | Separate SPA (React/Next or similar) calling `/api/v1` | Not prescribed by PRD; keep API-first |

### ADR-001 — Modular monolith

- **Decision:** Single deployable backend with package-level modules.
- **Why:** PRD MVP is single-tenant demo; avoids premature microservices.
- **Consequence:** Modules communicate via in-process interfaces / service classes; DB ownership by aggregate tables, not by separate databases.

### ADR-002 — Hybrid retrieval without claiming BM25

- **Decision:** Lexical = PostgreSQL full-text search (`tsvector`, ranked with `ts_rank`); dense = pgvector; then rerank.
- **Why:** Meets the PRD hybrid requirement. PostgreSQL FTS is a lexical engine. It is not BM25, and it is not treated as equivalent to BM25. ParadeDB/pg_search, or another BM25 engine, is a later change and only after measurement.
- **Consequence:** Documents, logs, and metrics say “lexical” or “full-text search”. They use “BM25” only after a BM25 engine is selected and benchmarked. This revision does not select that engine. The vector half stays disabled until the embedding dimension is fixed (ADR-020).

### ADR-003 — Provider abstraction, no silent fallback

- **Decision:** Chat, embeddings, extraction, and reranking sit behind interfaces with env-driven config.
- **Why:** Hybrid commercial + local strategy; provider outage must fail visibly.
- **Consequence:** Switching providers is configuration, not business-rule changes. Bounded retries → recorded failure → visible status.

### ADR-004 — Money and time

- **Decision:** `NUMERIC(18,2)` (or equivalent fixed decimal) for INR amounts; timestamps in UTC (`timestamptz`); currency code stored/explicit as INR.
- **Why:** PRD NFR; avoid float money.

### ADR-005 — Needs Documents application state (withdrawn)

- **Status:** Withdrawn by ADR-017. Do not implement `needs_documents`.
- **What v1.1 decided:** An eleventh application state for document wait, separate from Clarification required.
- **Why it was withdrawn:** PRD §7 defines ten states and does not name Needs Documents. Additional documents are requested through Request clarification, and replacements after a failed run happen in Processing failed. Keeping the eleventh state would contradict the PRD.

### ADR-006 — PostgreSQL-backed workers (no Redis in MVP)

- **Decision:** Background processing uses one or more Compose `worker` processes claiming jobs from PostgreSQL (`processing_runs` + `workflow_checkpoints`) via `FOR UPDATE SKIP LOCKED`. Idempotent node handlers. No Redis/Celery/ARQ required for MVP.
- **Why:** PRD forbids introducing Redis unless justified by measured need; reliability NFRs still require retryable, idempotent background work.
- **Consequence:** API enqueues runs; workers execute LangGraph nodes with checkpointing. Scale-out = more worker replicas against the same DB. Revisit Redis/Celery only if queue latency or locking contention is measured.

### ADR-007 — Immutable findings; append-only dispositions

- **Decision:** `findings` rows are immutable after insert. Reviewer actions write append-only `finding_dispositions`. Current effective status is the latest disposition, or `open` if none.
- **Why:** Mutating finding rows destroys auditability of what the assessment originally asserted.
- **Consequence:** Outcome gates read effective disposition; PDF/history list the full disposition chain.

### ADR-008 — Input snapshots for assessment reproducibility

- **Decision:** Every processing run freezes an `application_input_snapshots` row (declared applicant/loan fields + document inventory identity). Every `assessments` row references that snapshot (and policy/rules/provider versions).
- **Why:** Historical assessment versions must be reproducible even if live `applicants` / `applications` rows later change.
- **Consequence:** Corrections and edits after a run affect only subsequent snapshots/versions. Calculation-input **verification status** is part of that freeze (ADR-009).

### ADR-009 — Frozen calculation-input verification status

- **Decision:** Declared applicant amounts stay on `applicants` / snapshot columns. Whether each calculation input is `missing`, `unverified`, `disputed`, or `verified` is stored on append-only `snapshot_calculation_inputs` at freeze time. FOIR and loan-to-annual-income may be `computed` only when frozen `net_monthly_income` is `verified` and `selected_numeric > 0`. They may be `provisional` only when that input is `unverified`, `candidate_numeric > 0`, and limitation text is set. Every other case is `not_computable` with `result_numeric` NULL, including `missing`, `disputed`, and a zero denominator.
- **Why:** v1.1 stored declared net income as NOT NULL but did not freeze verification status, so Not computable could not be enforced from historical rows, and “missing” was confused with a null declared column.
- **Consequence:** Replay does not re-derive verification from live extractions. A DB trigger rejects ratio rows that disagree with the frozen net-income status.
- **PRD:** Implements §9 and §9.1. Does not change required/optional field rules. No PRD amendment.

### ADR-010 — Append-only outcome supersession

- **Decision:** `review_outcomes` rows are insert-only and have no supersession columns. Reopen inserts `review_outcome_supersessions` (one row per superseded outcome). The current outcome is the application’s outcome with no supersession row.
- **Why:** v1.1 called outcomes append-only while updating `superseded`, `superseded_at`, and `superseded_by_outcome_id`.
- **Consequence:** History is a pure insert log. A trigger rejects a second outcome while an unsurperseded one exists.
- **PRD:** Matches §11 (previous decisions preserved). No PRD amendment.

### ADR-011 — Same-application composite foreign keys

- **Decision:** Parent tables that are application-scoped expose `UNIQUE (id, application_id)`. Children reference that pair. Nullable links are all-null or all-set.
- **Why:** Separate FKs allowed an extraction, run, assessment, correction, or disposition to point at another application’s parent.
- **Consequence:** Cross-application references fail at insert. No new datastore.
- **PRD:** Strengthens traceability (FR-06, FR-14). No PRD amendment.

### ADR-012 — Single global audit chain

- **Decision:** One chain for all `audit_events`, ordered by `chain_seq`. A singleton `audit_chain_state` row is locked `FOR UPDATE` inside the only insert path. Direct inserts are revoked.
- **Why:** Concurrent inserts that each read the same previous hash create competing branches. v1.1 left chain scope open (former U-05).
- **Consequence:** Writers serialize on one row. A verifier walks `chain_seq` with no gaps and recomputes hashes. Per-application chains are not used.
- **PRD:** Meets append-only audit (FR-14, §13.1). No PRD amendment.

### ADR-013 — Document failure events vs resolutions

- **Decision:** Pipeline facts go to append-only `document_failure_events`. Later handling goes to append-only `document_failure_resolutions` that reference the event. Neither table is updated.
- **Why:** v1.1 mixed OCR failures and reviewer “accepted insufficient evidence” in one disposition enum, so a resolution could be mistaken for the failure itself.
- **Consequence:** Full history is the union of both tables. Accepting a gap does not delete the event.
- **PRD:** Matches §7.2 and §15.2. No PRD amendment.

### ADR-014 — `dismissed` is valid and does not close Critical items

- **Decision:** `finding_disposition_type` keeps `dismissed`. It is a legal Reviewer action (PRD §11, FR-13). It does **not** satisfy the pre-outcome gate for Critical or insufficient-evidence items. That gate accepts only the latest disposition in `{resolved, overridden, decision_without_evidence}` (PRD §7.2, §10.1).
- **Why:** v1.1 listed `dismissed` in the enum and also described only the three Critical dispositions, without saying whether dismiss was legal or sufficient.
- **Consequence:** A latest `dismissed` on a Critical finding still blocks outcome recording. Major findings may be closed by dismiss or by a comment, per §10.1.
- **PRD:** Alignment only. No PRD amendment. Changing the Critical gate to accept `dismissed` would require PRD approval; this design does not do that.

### ADR-015 — Worker fence token and logical side-effect keys

- **Decision:** Each claim or lease reclaim increments `processing_runs.fence_token`. Side-effect inserts carry `claim_fence` and are rejected when it is not the current token. Each artifact class has a unique logical key so replay uses `ON CONFLICT DO NOTHING`. Expired leases are reclaimable; they do not by themselves mark the run failed.
- **Why:** v1.1 said replays were idempotent but did not define keys, and a crashed worker could still commit after another worker reclaimed the lease.
- **Consequence:** Stale workers fail closed. Checkpoints still resume completed nodes. Extends ADR-006; still no Redis.
- **PRD:** Implements reliability NFR (retryable, idempotent processing). No PRD amendment.

### ADR-016 — No assessment without exactly one applicable policy

- **Decision:** Policy is chosen by application as-of date (UTC calendar date of first `submitted_at`), not upload date. Exactly one `active` `policy_versions` row must cover that date. Zero matches → run `failed` with `no_applicable_policy`. More than one → `ambiguous_applicable_policy`. Neither case inserts `assessments`. `assessments.policy_version_id` stays `NOT NULL` and must equal the snapshot’s policy id.
- **Why:** v1.1 required a policy id on every assessment but did not define the empty or overlapping case, so workflow and schema could disagree.
- **Consequence:** Admin must publish a non-overlapping active version, then retry. Retrieval “insufficient evidence” still applies when a policy exists but chunks do not support the question.
- **PRD:** Consistent with “record the policy version used” and “never invent” policy. A partial assessment with a null policy would need PRD approval; this design does not do that.

### ADR-017 — Ten application states (withdraws ADR-005)

- **Decision:** `application_status` has exactly the ten states named in PRD §7. There is no `needs_documents` status.
- **Why:** The PRD is the source of truth for the lifecycle. ADR-005 added a state the PRD does not contain.
- **Consequence:** Document wait uses states the PRD already defines. Draft is for preparation. Clarification required is for a Reviewer request for more information, including when that information is a document. Processing failed is where a failed document can be replaced before retry. See §5 and ADR-018.
- **PRD:** No amendment. An eleventh state would require one.

### ADR-018 — Clarification, additional documents, and referral (closes U-02)

- **Decision:**
  - **Request clarification** is the only Reviewer action that asks the Loan Officer for more information. It is allowed from `in_review` only. It inserts one `clarifications` row (question text required) and moves the application to `clarification_required`. The question may name documents. That does not create another state or another action.
  - **Request additional documents** is not a separate action, permission, or transition.
  - **`referred_for_more_information`** is an outcome. Recording it moves `in_review` (or `escalated`, for the assigned Reviewer) to `review_completed` and does nothing else. It does not open a clarification.
  - After that outcome, more information is collected only by the PRD reassessment path: **Reopen** (`review_completed` → `processing`, previous outcome superseded and preserved, new assessment version when the run succeeds, then `in_review`). The Reviewer may then request clarification.
- **Why:** The permission matrix lists Request clarification and does not list Request documents. §11.2 says a referred outcome follows the clarification/reassessment workflow, which §7 and §11.1 already define as those two actions. §7 does not list an edge from Review completed to Clarification required.
- **PRD:** No amendment. A single action that both records the referred outcome and enters Clarification required would add a §7 edge and needs a PRD amendment.

### ADR-019 — Server-side sessions (closes U-03)

- **Decision:** The browser signs in to a server-side session. The cookie is `HttpOnly`; `Secure` outside local Compose; `SameSite=Lax`. The database stores the SHA-256 of the session secret, not the secret. Passwords are hashed with Argon2id (parameters come from configuration). Login rotates the session identifier. Logout and `users.is_active = false` revoke sessions. Mutating requests require a CSRF token in addition to the cookie.
- **Why:** The PRD requires an authenticated caller and backend RBAC. It does not require JWT. Sessions can be revoked in the same database when an Admin deactivates a user, without Redis and without a token denylist. The MVP client is a first-party SPA.
- **Consequence:** Table `user_sessions`. The MVP HTTP API does not accept a bearer JWT. A future non-browser client would be a separate decision.
- **PRD:** No amendment.

### ADR-020 — Embedding model and dimension deferred (U-04)

- **Status:** Deferred — implementation decision pending.
- **Decision:** This revision does not choose an embedding model or a vector dimension. The embedding model has not been selected. The vector dimension must match the selected model. The `policy_chunks.embedding` vector column must not be created until the model and dimension are finalized. The migration that creates that column then fixes N. A different dimension later is a new column or a new table, not an in-place resize. `embedding_model` is stored on each chunk, and `embedding_version` is stored on each assessment.
- **Why:** The PRD requires configurable embedding providers and does not name a model. Choosing one here would invent a product setting.
- **Consequence:** Lexical retrieval using PostgreSQL full-text search can be implemented independently. Chunk text, citations, and rerank wiring can be built with it. Dense retrieval remains disabled until the embedding decision is finalized. The hybrid retrieval requirement remains partially incomplete until dense retrieval is implemented.
- **PRD:** No amendment. The missing fact is an implementation input, not a product rule.

### ADR-021 — Document hash equality is mandatory

- **Decision:** On every insert of `application_documents`, a trigger requires `content_sha256` to equal `stored_files.content_sha256` for `stored_file_id`. Updates to either hash column or to `stored_file_id` are rejected. `stored_files` rows are insert-only.
- **Why:** v1.2 denormalized the hash for the uniqueness index and left the match check optional. A mismatched hash would deduplicate against the wrong bytes.
- **Consequence:** The partial unique index and the stored object refer to the same digest. Specification is in DATABASE_DESIGN §4.3.
- **PRD:** File-handling NFR and the duplicate-upload rule. No amendment.

### ADR-022 — One active review outcome under concurrency

- **Decision:** The application role has no direct `INSERT` on `review_outcomes` or `review_outcome_supersessions`. `record_review_outcome` and `supersede_review_outcome` are the only writers. Each locks the parent `applications` row `FOR UPDATE` before it checks or inserts. A deferred constraint trigger aborts a commit that leaves more than one outcome for that application with no supersession row.
- **Why:** Under READ COMMITTED, two transactions can both observe “no active outcome” and both insert. A trigger that only reads committed rows does not close that race. Unique `(assessment_id)` does not stop two active outcomes on two assessments.
- **Consequence:** Conflicting active outcomes cannot commit. Supersession still does not update the outcome row (ADR-010).
- **PRD:** §11, previous decision preserved, one reviewer-recorded outcome at a time. No amendment.

### ADR-023 — Calculation status tuple and deferred finding link (closes U-06)

- **Decision:** `assessment_calculations` accepts only three row shapes: `computed` (numeric result, not provisional, no limitation text, no gap finding), `not_computable` (null result, not provisional, gap finding required), or `provisional` (numeric result, provisional flag, limitation text, no gap finding). The gap-finding foreign key is `DEFERRABLE INITIALLY DEFERRED`. A deferred constraint trigger requires that finding to have `is_insufficient_evidence = true` and the same `assessment_id`.
- **Why:** v1.2’s separate checks still allowed pairs such as `not_computable` with `is_provisional = true`, or `provisional` with a null result. An immediate foreign key also forced an insert order the service could not always meet, so the finding link was left optional.
- **Consequence:** The assessment node inserts the assessment, the findings, and the calculations in one transaction. It allocates finding identifiers first. Either child may be inserted first. At commit, every not-computable row points at its insufficient-evidence finding. The service-only escape hatch is removed.
- **PRD:** §9.1. No amendment.

### ADR-024 — `document_requests` is not in the MVP (closes U-08)

- **Decision:** The MVP schema has no `document_requests` table.
- **Why:** PRD Must scope asks the Reviewer to request clarification in text. It does not require a per-document-type checklist with fulfillment status.
- **Limitation:** The product cannot show a structured open/fulfilled row per document type. The clarification question states what is missing. Uploaded files are ordinary `application_documents` rows. A checklist can be added later without a new application state.
- **PRD:** No amendment.

### ADR-025 — Malware scanning, encryption at rest, and download authorization

- **Decision:**
  - Intake writes bytes to a quarantine location, scans them, and inserts `stored_files` only when the scan status is `clean`. Infected files and scanner errors do not become application documents. The API returns a structured error. Processing and download require `clean`.
  - The MVP scanner is ClamAV (`clamd`) in Compose, or another scanner that writes the same status values. The schema does not depend on the scanner vendor.
  - Database and upload volumes are encrypted at rest (disk or volume encryption). When the object store is S3-compatible, server-side encryption is required. Local Compose uses an encrypted disk for those volumes. TLS is required outside local Compose.
  - Clients never receive a storage URI. `GET /api/v1/applications/{application_id}/documents/{document_id}/content` requires a live session and an active Loan Officer, Reviewer, or Admin, then streams the bytes. The call appends an audit event.
- **Why:** PRD §12 and §13.1 require untrusted-upload handling, controlled storage, encryption in transit, and protection of stored files. v1.2 specified transport encryption and hash retention, and did not specify scanning, at-rest encryption, or the download check.
- **Consequence:** Column `stored_files.malware_scan_status`. No new lending workflow and no new role.
- **PRD:** No amendment. Retention periods remain in the security specification; this ADR does not invent them.

---

## 4. Logical modules and boundaries

Aligned to PRD §5.2. Each module owns its domain logic. Shared kernel holds identity, RBAC, audit, and common types.

```text
loanlens/
  apps/api/                 # FastAPI app, routers, DI wiring
  apps/worker/              # Background claim loop (same codebase)
  modules/
    identity/               # users, roles, authn/authz
    intake/                 # applications, uploads, validation, duplicates, snapshots
    document_intelligence/  # classify, OCR, field extraction, failure events and resolutions
    verification/           # BR-01..BR-11 (+ checklist) deterministic findings
    financial/              # EMI, FOIR, LTI; deterministic calculators; Not computable
    policy/                 # policy versions, chunking, hybrid retrieve + rerank
    assessment/             # assessment versions, report assembly, PDF export
    workflow/               # LangGraph orchestration, run tracking, checkpoints, enqueue
    governance/             # audit, version stamps, metrics hooks
  providers/                # LLM, embedding, rerank, OCR adapters
  shared/                   # errors, money, ids, pagination, middleware
```

| Module | Owns | Must not |
|---|---|---|
| **Identity** | Users, roles, password hashes, server-side sessions, permission checks | Record lending outcomes; accept a JWT on the MVP browser API |
| **Intake** | Applications, applicant/loan fields, documents metadata, submit/withdraw rules, input snapshots, hash dedup | Run OCR or invent document types after classification fails silently |
| **Document Intelligence** | Classification, OCR, structured extraction, page provenance, failure events; resolutions that reference those events | Decide verification severity or approve/reject |
| **Verification** | Deterministic business-rule findings (BR-01…BR-11 and required-document checklist) | Persist model prose as a finding; use LLM to invent reconciliation outcomes |
| **Financial** | Deterministic indicators and Not computable handling | Call LLMs for arithmetic; invent substitute ratios |
| **Policy** | Ingest/version policies, retrieve/rerank, citations, insufficient-evidence | Answer without retrieval or fabricate clauses |
| **Assessment** | Assemble assessment versions, AI observation section, PDF export content | Auto-set final outcome; merge AI observations into deterministic findings |
| **Workflow** | State machine transitions that are system-owned; processing runs; checkpoints; worker hand-off | Allow humans to edit processing states manually |
| **Governance** | Append-only audit (integrity-protected), version stamps, traces | Soften unresolved Critical items |

**Cross-cutting rule:** API routers are thin. Business rules live in module services. Infrastructure (DB repos, file store, providers) is injected.

---

## 5. Application lifecycle (runtime)

Ten states, matching PRD §7 exactly (ADR-017). The system owns `submitted`’s hand-off into processing, `processing`, and `processing_failed`. People own the other transitions listed below. A person cannot set a processing state by editing it.

```text
Draft
  → Submitted                         (Loan Officer or Admin; mandatory documents and required fields valid)
  → Archived                          (Loan Officer withdrawal, with reason)

Submitted
  → Processing                        (system, when the worker claims the queued run)

Processing
  → Awaiting human review             (first successful assessment; unassigned queue)
  → In review                         (successful reprocess while a reviewer assignment is still active)
  → Processing failed                 (retries exhausted or non-retryable failure)

Processing failed
  → Processing                        (Loan Officer or Admin retry; new run)
  → Archived                          (Loan Officer withdrawal, with reason)

Awaiting human review
  → In review                         (Reviewer self-claim or Admin assign)
  → Archived                          (Admin archive)

In review
  → Clarification required            (Reviewer requests clarification)
  → Escalated                         (Reviewer escalates, with reason)
  → Review completed                  (Reviewer records an outcome)
  → Processing                        (Reviewer field correction starts a new run)

Clarification required
  → Processing                        (Loan Officer or Admin submits the response and/or new documents)
  → Archived                          (Loan Officer withdrawal, with reason)

Escalated
  → In review                         (Admin reassignment or return)
  → Review completed                  (assigned Reviewer records an outcome)

Review completed
  → Processing                        (Reviewer reopen: supersession, new run, new assessment version)
  → Archived                          (Admin archive)

Archived
  → (none)
```

The first submit leaves status at `submitted` while that run is queued. The worker’s claim moves `submitted` → `processing`. Retry, clarification resume, field-correction reprocess, and reopen set status to `processing` in the same transaction that enqueues the run, including while the run is still `queued`. A later claim does not change application status if it is already `processing`. PRD §7 names `in_review` as the business destination of reopen. The run that creates the new assessment version is still `processing`, because that is the state in which the system is extracting and calculating. On success after reopen, correction, or clarification, status returns to `in_review` and the existing assignment stays. The first successful run goes to `awaiting_human_review` and the unassigned queue. This uses only the ten PRD states. It does not add `review_completed` → `clarification_required`.

### State ownership and the two information-gathering actions

| Action | Who | From | To | What is written |
|---|---|---|---|---|
| Request clarification | Reviewer | `in_review` | `clarification_required` | `clarifications` row. Question text is required and may name documents. No outcome row. |
| Submit clarification response | Loan Officer or Admin | `clarification_required` | `processing` | Response text and/or documents uploaded after the clarification was opened. At least one of those must be present. Uploads alone do not change status, so several files can be attached before submit. New run and, on success, a new assessment version. |
| Record outcome, including Referred for more information | Assigned Reviewer | `in_review`, or `escalated` | `review_completed` | `review_outcomes` row only. Referral does not open a clarification. |
| Reopen | Reviewer | `review_completed` | `processing`, then `in_review` on success | `review_outcome_supersessions` row. Previous outcome row is unchanged. New snapshot, new run, new assessment version. |
| Replace a failed document | Loan Officer or Admin | `processing_failed` | stays `processing_failed` until Retry | New `application_documents` row and a `replaced` resolution. Retry is a separate action. |
| Withdraw | Loan Officer | `draft`, `processing_failed`, `clarification_required` | `archived` | Reason required. |
| Archive | Admin | `awaiting_human_review`, `review_completed` | `archived` | Also allowed on the withdrawal states above. |
| Escalate | Reviewer | `in_review` | `escalated` | Reason required. Clarification, field correction, and dismissal are rejected while escalated. The assigned Reviewer may still record an outcome. |
| Claim or assign | Reviewer self-claim, or Admin | `awaiting_human_review` | `in_review` | One active assignment. Unassigned age is the time spent in `awaiting_human_review`. The system never auto-decides. |

Requesting additional documents is the Request clarification action. The Reviewer writes the documents into the question. There is no second command and no `document_requests` checklist (ADR-018, ADR-024).

While status is `review_completed` or `archived`, field corrections, dispositions, comments, and clarifications are rejected. While status is `processing` or `submitted`, those review writes are rejected as well.

### Workflow engine responsibilities

- One **active processing run** per application. A second concurrent run returns a structured `409`.
- Retry only from `processing_failed`, by a Loan Officer or Admin, within the configured limit. Each retry is a new `processing_runs` row and a new idempotency key. The previous run remains.
- A new document on the clarification or failure paths, a Reviewer field correction, or a reopen starts a new run and a **new assessment version**. Earlier assessments, dispositions, and outcomes stay.
- Before a run starts, Intake freezes an **application input snapshot** (ADR-008), including `snapshot_calculation_inputs` (ADR-009). Policy selection either stores one version or fails the run with no assessment (ADR-016).
- `applications.submitted_at` is set once on the first transition to `submitted` and is never updated. The policy as-of date is that timestamp’s UTC calendar date.
- Every transition records previous state, new state, actor (`user` or `system`), timestamp, and reason.
- Uploads are accepted only in `draft`, `clarification_required`, and `processing_failed`. The same state rule applies to Admin uploads. Reviewers cannot upload (PRD §11 matrix).
- Humans cannot manually set `processing` or `processing_failed`.
- A database trigger rejects an `applications.status` change unless that edge is one of the edges in the diagram above and the same transaction inserts the matching `application_state_history` row. The service also checks the actor’s role. The trigger does not replace the role check.

### LangGraph sketch (MVP)

```text
[select_policy]          # exactly one active version for as-of date, else fail run
    → [validate_inputs]
    → [classify_documents]
    → [extract_fields]
    → [normalize_fields]
    → [freeze_calc_inputs]   # missing | unverified | disputed | verified
    → [run_verification_rules]
    → [run_financial_calcs]
    → [retrieve_policy]
    → [assemble_assessment]  # refused unless snapshot.applicable_policy_version_id is set
    → [hand_off_to_review]
```

`select_policy` runs before other nodes and is checkpointed. `no_applicable_policy` and `ambiguous_applicable_policy` are non-retryable until an Admin changes policy versions; the Loan Officer or Admin may then retry, which creates a new run. Failure at any later node: mark the run failed after bounded retries for retryable provider errors, persist partial artifacts where safe, write `document_failure_events` where a document failed, and surface retry, replacement upload, or clarification. Do not insert an assessment. `assemble_assessment` writes the assessment, findings, calculations, and AI observations in one database transaction (ADR-023).

---

## 6. Background workers, queue, checkpointing, retry, idempotency

### Topology

```text
API process                    Worker process(es)
────────────                   ──────────────────
validate + authorize
create processing_runs
  (status=queued, idempotency_key, fence_token=0)
commit                         loop:
respond 202 / run id             BEGIN
                                 SELECT candidate
                                   status=queued
                                   OR (running AND lease_expires_at < now())
                                   FOR UPDATE SKIP LOCKED
                                 fence_token := fence_token + 1
                                 status=running, locked_by, lease_expires_at
                                 COMMIT
                                 execute from last succeeded checkpoint
                                 every write includes claim_fence
                                 on success → succeeded + assessment
                                 on failure → failed + error fields
```

Compose services: `api`, `worker` (same image, different entrypoint), `db`, optional `web`.

### Fencing and lease expiry (ADR-015)

- `fence_token` starts at 0. Every successful claim or reclaim increments it in the same transaction as the lock.
- The worker keeps that token in memory. Lease renewal is `UPDATE … SET lease_expires_at = now()+ttl WHERE id=? AND fence_token=? AND locked_by=?`. Zero rows means the worker is stale: stop, do not write.
- Side-effect inserts set `claim_fence`. A trigger rejects the insert when `claim_fence` is not `processing_runs.fence_token`.
- Lease expiry does **not** set `failed`. It makes the row reclaimable. The new worker gets a higher fence; the old worker’s writes fail.
- After reclaim, resume at the latest checkpoint with `status=succeeded`. Do not rerun that node. Rerun only the incomplete node; its inserts conflict-skip on logical keys.
- Heartbeats that lose the fence must not extend the lease.

### Checkpointing

- After each successful LangGraph node, persist a `workflow_checkpoints` row: `processing_run_id`, `node_name`, `sequence_no`, `state_json`, `status=succeeded`, `claim_fence`.
- Checkpoint upserts are allowed only while the run is `running` and `claim_fence` matches. After the run reaches `succeeded` or `failed`, checkpoints are frozen.

### Logical keys (replay-safe)

| Artifact | Unique logical key |
|---|---|
| `document_pages` | `(processing_run_id, application_document_id, page_number)` |
| `extracted_fields` | `(processing_run_id, logical_key)` |
| `document_failure_events` | `(processing_run_id, application_document_id, kind)` |
| `findings` | `(assessment_id, logical_key)` |
| `assessment_calculations` | `(assessment_id, indicator_name)` |
| `ai_observations` | `(assessment_id, logical_key)` |
| `snapshot_calculation_inputs` | `(input_snapshot_id, input_name)` |
| `workflow_checkpoints` | `(processing_run_id, node_name)` |

`logical_key` is a stable text the node owns, for example `{document_id}:{field_name}:{page_number}:{occurrence}` or `{rule_code}:{subject}`. Replay uses insert-on-conflict do nothing. A conflicting payload with the same key is not updated (append-only facts). If the existing row’s payload differs, the node fails the run rather than overwriting.

### Retry layers

| Layer | Behaviour |
|---|---|
| Provider call | Bounded retries with timeout; jittered backoff; then node failure |
| Node | Retryable errors may re-enter the node within the same run and same fence, up to a configured cap; then fail the run |
| Processing run | New run from `Processing failed` only; `attempt_number` increments; prior run retained |
| Job lease | Expired `running` rows are reclaimed with a new fence; the run stays `running` |

### Idempotency

- Client/API may supply `Idempotency-Key` on submit/retry/reprocess; stored uniquely on `processing_runs.idempotency_key` (nullable unique per application when present).
- Duplicate enqueue with the same key returns the existing run—no second active run.
- Document upload dedup uses content hash uniqueness (see §7). The denormalized hash must match `stored_files` (ADR-021). Concurrent duplicate uploads lose on the unique constraint and return the warning response.

### Explicit non-goals for MVP workers

- No Redis, Celery, or external broker.
- No silent cross-provider failover inside a retry loop.

---

## 7. Document intelligence pipeline

```text
Upload
  → MIME + extension + magic-byte signature + size checks
  → content-hash (SHA-256)
  → malware scan in quarantine (ADR-025); only a clean result is stored
  → application-scoped dedup: if an *active* document on this application
    already has the same hash → warn; do not insert a second active copy
  → persist original bytes (stored_files) + application_documents row
  → trigger: application_documents.content_sha256 = stored_files.content_sha256
  → classify (supported type or unsupported)
  → OCR / text extract → document_pages rows
  → structured field extraction (provider)
  → store ExtractionRecord with FK to document_pages when page-known:
      field, value, currency?, confidence, document, document_page_id,
      evidence_text, method, review_status
```

**Rules**

- Unsupported file: mark unsupported with reason; insert a **`document_failure_events`** row (`marked_unsupported`); do not process as supported.
- OCR failure: record per-page failure; insert `document_failure_events` (`ocr_failed`); the Loan Officer or Admin may retry the run or upload a replacement while the application is `processing_failed`.
- Unreadable document: `document_failure_events` (`unreadable`) plus an insufficient-evidence finding when an assessment is produced. The finding stays until a §7.2 disposition. The file is never treated as successful evidence.
- Low confidence: flag for Reviewer confirmation (BR-11); retain evidence. Confirmation is an append-only `field_review_actions` row. The extraction row is not updated.
- Field without supporting evidence: **not** stored or presented as a verified fact.
- Alternatives (for example employer letter plus salary credits): flag for Reviewer confirmation per the document matrix.
- Infected or unscanned bytes are not documents. They have no failure-event row because they were never accepted. The upload error and the audit event are the record.

### Document failures vs handling (ADR-013)

Two append-only tables. Events are facts about the file. Resolutions are later decisions. A resolution does not update or delete the event.

**`document_failure_events`** (pipeline; usually `actor_type=system`):

| Kind | Meaning |
|---|---|
| `marked_unsupported` | Type/signature/classification: unsupported |
| `ocr_failed` | OCR/text layer failed |
| `unreadable` | Content present but not usable as evidence |
| `extraction_failed` | Structured extraction failed after OCR |

**`document_failure_resolutions`** (handling; references `document_failure_event_id`):

| Kind | Meaning |
|---|---|
| `replacement_requested` | Replacement upload required |
| `replaced` | A new `application_documents` row superseded this one |
| `retry_requested` | Re-process the same bytes |
| `accepted_insufficient_evidence` | Reviewer records that this file will not be replaced, with a written reason |

Resolutions are file-handling records. They are not the PRD §7.2 dispositions, and none of them satisfies the outcome gate.

| PRD behaviour | Where it is recorded | Closes the failure event | Satisfies the §7.2 outcome gate |
|---|---|---|---|
| Unsupported file: show the reason; do not treat the file as a supported document | Event `marked_unsupported`. Later `replacement_requested` and then `replaced` if a new file is uploaded | No. The event row stays | No |
| OCR failure: retry or replacement upload | Event `ocr_failed`. Resolution `retry_requested`, or `replacement_requested` then `replaced` | No | No |
| Unreadable document: insufficient-evidence finding, plus retry, replacement, or clarification | Event `unreadable` and, when an assessment exists, a finding with `is_insufficient_evidence`. Retry and replacement use the resolution kinds above. Clarification is a `clarifications` row and the `clarification_required` transition, not a resolution kind | No | No |
| Resolved with supporting evidence | `finding_dispositions.disposition = resolved` | No | Yes |
| Overridden with a written reason | `finding_dispositions.disposition = overridden` | No | Yes |
| Decision without the evidence, with a written reason and a visible unresolved status | `finding_dispositions.disposition = decision_without_evidence` and `unresolved_evidence_visible = true`. A companion resolution `accepted_insufficient_evidence` may be stored and does not replace this disposition | No | Yes, the finding disposition only |
| Dismiss finding (FR-13, §11) | `finding_dispositions.disposition = dismissed` | No | No for a Critical or insufficient-evidence finding |

`accepted_insufficient_evidence` does not replace a finding disposition. Critical and insufficient-evidence findings still need a §7.2 disposition before an outcome. Both histories stay visible in the UI and PDF.

**Identity MVP limit:** cross-document consistency/format only—no external Aadhaar/PAN/bureau verification.

---

## 8. Verification & financial analysis

### Verification (deterministic findings)

- Findings produced only by numbered rules **BR-01 … BR-11** and the required-document checklist behaviours described in the PRD (missing mandatory/optional handling).
- Each finding: stable type, severity (`Critical`/`Major`/`Minor`/`Info`), description, evidence refs (document / **document_page** / field ids), detection source = `deterministic` or `checklist`.
- Findings rows are **immutable**. Reviewer actions write **append-only** `finding_dispositions` (ADR-007, ADR-014).
- Disposition types: `resolved`, `dismissed`, `overridden`, `decision_without_evidence`.
- `dismissed` is valid (PRD §11, FR-13). It does **not** close a Critical finding or an insufficient-evidence finding. The outcome gate reads the **latest** disposition only.
- Findings are **not** automatic rejections.
- Conflicting extracted values: show both + pages; withhold verified calculation until Reviewer confirms.
- Policy-dependent thresholds loaded from **versioned policy configuration**, not hard-coded constants in rule code.

### AI observations (not findings)

- **BR-12** anomaly signals and any other model-generated notes are stored as **`ai_observations`**, explicitly labelled `model_generated` (or more specific labels).
- They appear in a separate assessment section and PDF block.
- An observation alone never decides an outcome and is never silently promoted into a deterministic finding.

### Financial

Implemented as pure functions with unit tests:

- Proposed EMI from P, annual rate, tenure (handle zero-interest without division by zero).
- FOIR = (existing EMIs + proposed EMI) / net monthly income × 100.
- Loan-to-annual-income = requested / (net monthly income × 12).
- Annual interest rate for a run comes from the **applicable policy version** and is frozen on the input snapshot / calculation inputs—not silently invented at report time.

**Calculation-input status (ADR-009), frozen on the snapshot:**

| Status | Meaning | Selected value | Ratio use (FOIR, LTI) |
|---|---|---|---|
| `missing` | No numeric value to use (example: no applicable interest rate) | NULL | `not_computable` |
| `unverified` | A single candidate exists (declared and/or extracted) but is not confirmed sufficiently | NULL (`candidate_numeric` may be set) | `not_computable`, or `provisional` only if candidate > 0 and limitation text is set |
| `disputed` | Conflicting values; none is chosen (PRD §9.1) | NULL | `not_computable` only |
| `verified` | One value confirmed for this run | `selected_numeric` NOT NULL | `computed` only if `selected_numeric > 0` and other required inputs are verified; if selected is 0, `not_computable` |

Declared `applicants.net_monthly_income` stays required and non-null (PRD §9). That column is **not** the verification status. A declared amount can still be frozen as `unverified` or `disputed`. Because the declared amount is required, frozen net income is never `missing`. `missing` is for an input that has no number, such as a rate when no policy was selected (that run does not produce an assessment).

| PRD §9.1 condition for a ratio | Frozen net-income status | Allowed calculation status |
|---|---|---|
| Zero | `verified` and `selected_numeric = 0`, or `unverified` and `candidate_numeric <= 0` | `not_computable` |
| Not verified sufficiently, and a single candidate is greater than zero | `unverified`, `candidate_numeric > 0` | `not_computable`, or `provisional` when limitation text is set |
| Conflicting values | `disputed` | `not_computable` only |
| Verified and greater than zero | `verified`, `selected_numeric > 0` | `computed` when the other required inputs are `verified`; otherwise `not_computable` |

**Legal calculation row (ADR-023).** The database accepts only these combinations:

| `status` | `result_numeric` | `is_provisional` | `limitation_text` | `insufficient_evidence_finding_id` |
|---|---|---|---|---|
| `computed` | required | false | null | null |
| `not_computable` | null | false | optional | required, and the finding has `is_insufficient_evidence` |
| `provisional` | required | true | required | null |

**Not computable (normative):**

- The system must **not** divide by zero, substitute 0 or infinity, or coerce null into a numeric ratio.
- A Not computable result creates an **insufficient-evidence** finding and records the frozen input statuses in `inputs_json`. The calculation row stores that finding’s id. The foreign key is deferred to commit so the finding and the calculation can be inserted in either order inside the assessment transaction (ADR-023).
- A trigger rejects `computed` FOIR or loan-to-annual-income unless frozen `net_monthly_income` is `verified` and `selected_numeric > 0`. It rejects `provisional` unless that input is `unverified` with `candidate_numeric > 0`.
- Zero interest uses the principal-divided-by-tenure result when the rate input is `verified` and the value is 0. A missing rate does not produce an assessment, because policy selection has already failed (ADR-016).
- Optional declared fields (other monthly income without evidence, household expenses when unused) stay out of default eligibility calculations. They may appear only as labelled sensitivity lines.
- Provisional results are labelled and are not verified. A provisional result does not by itself create an insufficient-evidence finding.

---

## 9. Policy intelligence

```text
Admin uploads policy document
  → version + effective_from (+ optional effective_to) + active status
  → chunk + lexical index (PostgreSQL full-text search, not BM25)
  → embed only after the embedding model and dimension N are chosen (ADR-020); until then the vector column is not created and dense retrieval is off

On processing start (ADR-016):
  as_of = UTC date of applications.submitted_at   # not upload date
  matches = active versions where effective_from <= as_of
            and (effective_to is null or effective_to >= as_of)
  0 matches → fail run no_applicable_policy; no assessment row
  >1 match → fail run ambiguous_applicable_policy; no assessment row
  1 match  → freeze policy id and annual_interest_rate_pct on the snapshot

Query path (only after a policy was selected):
  → lexical candidates (PostgreSQL FTS) + vector candidates when embeddings exist
  → fuse
  → rerank
  → cite (policy_id, version, section, page/chunk)
  → if insufficient → explicit insufficient-evidence (never invent)
```

Assessment stores that same policy version id. Changing policy creates a new version; past assessments stay unchanged. Overlapping active ranges are a configuration error, not a silent pick.

---

## 10. Assessment, review, and PDF export

### Assessment package (versioned, reproducible)

Each assessment version references:

- `application_input_snapshots` (declared fields, document inventory, and `snapshot_calculation_inputs`)
- `processing_run_id`, policy / business-rules / prompt / model / embedding / reranker / evaluation dataset versions
- Immutable findings for that version + full disposition chain (may span review after generation)
- `assessment_calculations` with inputs, formula, status (`computed` / `not_computable` / `provisional`)
- `ai_observations` (labelled, separate)
- Policy citations
- Reviewer comments, field corrections, outcomes linked to this assessment id

### Review dispositions (Critical / insufficient evidence)

Before outcome recording, every Critical finding and every insufficient-evidence finding needs a **latest** append-only disposition of:

1. Resolved with supporting evidence, or  
2. Overridden with written reason, or  
3. Decision without evidence with written reason **and** visible unresolved-evidence status (prominent in UI and PDF)

`dismissed` remains a stored disposition for other findings and stays in history, but a latest `dismissed` does not pass this gate (ADR-014).

Document failure **events** and **resolutions** both remain visible. A resolution does not close a Critical finding by itself.

### Outcomes (Reviewer only)

`Approved` | `Rejected` | `Referred for more information`

- Outcome rows are insert-only and **always** tied to a specific `assessment_id` (ADR-010). They have no mutable supersession columns.
- The only insert path is `record_review_outcome`. It locks the application row, checks the §7.2 gate and the §10.1 Major comment-or-disposition rule, checks that the caller is the assigned active Reviewer, and inserts. A deferred constraint rejects a second unsurperseded outcome (ADR-022). Direct inserts are revoked.
- Reopen calls `supersede_review_outcome` under the same application lock, then starts a new run. The prior outcome row is unchanged.
- `Referred for more information` ends in `review_completed` like the other outcomes. It does not move the application to Clarification required. The Reviewer reopens, and may then request clarification (ADR-018). The referred outcome stays queryable.

Every Major finding must have a finding-level comment or a disposition before an outcome insert (§10.1). Minor and Info findings do not block the outcome. The Critical and insufficient-evidence gate is unchanged (ADR-014).

Admin assigns, retries, and archives. Admin cannot record a lending outcome, correct an extracted field, or dismiss a finding. A database trigger on the outcome function requires `users.role = reviewer` and `is_active`. Loan Officer cannot correct fields or dismiss findings.

### PDF export

Must preserve source-page refs (via page relationship), calc inputs including frozen verification status, formula, rule version, policy citations, AI observations as a separate labelled section, full review history including finding dispositions, document failure events and resolutions, overrides, outcome supersession events, and unresolved limitations. Must not hide limitations.

---

## 11. API surface (shape)

- Base: `/api/v1`
- OpenAPI published from FastAPI
- Pydantic request/response models; structured error body: `code`, `message`, `details`, `request_id`
- Pagination on collections uses `offset` and `limit`.
- Auth required on all protected routes: a live, unrevoked session. Authorization is checked per operation. Mutating requests also require the CSRF token (ADR-019).
- Long-running submit, retry, clarification response, correction reprocess, and reopen enqueue a run and return the run id. Clients poll processing status.

**Illustrative resource groups**

| Group | Examples |
|---|---|
| Auth | login, me |
| Users (Admin) | CRUD-ish user/role management |
| Applications | create, get, update, submit, withdraw, retry, submit clarification response |
| Documents | upload, list, get metadata, replace, download content (authorized stream) |
| Processing | get run status, list runs, checkpoints (Admin/debug) |
| Assessments | get version, list versions, export PDF |
| Review | claim, assign, correct field, confirm field, dispose finding, comment, clarify, escalate, record outcome, reopen |
| Policies (Admin) | upload, list versions, activate/configure thresholds |
| Audit (Admin; limited others) | query events |
| Health | liveness/readiness (API + worker heartbeat) |

Exact request and response schemas belong in a separate API specification (PRD Appendix B). The transactions below are normative so the API, the worker, and the database agree.

| Operation | Transaction contents |
|---|---|
| Upload | Scan in quarantine. On `clean`, insert `stored_files` and `application_documents` together. The hash trigger runs in that transaction. Then append the audit event. An infected or failed scan inserts neither row. |
| Submit | Validate, set `submitted` and `submitted_at`, freeze the snapshot and calculation inputs, insert the queued run, set the application status transition that submit owns, append audit. Worker claim is a later transaction that moves status to `processing`. |
| Retry, clarification submit, correction, reopen | Lock the application. Check the source status and the role. Reopen also supersedes the current outcome. Insert the next snapshot and a queued run. Move status to `processing` and write state history. Append audit. |
| Worker claim | Separate short transaction: `SKIP LOCKED`, increment `fence_token`, set the run `running` and the lease. If application status is `submitted`, move it to `processing` and write state history. If status is already `processing`, leave it. Node work is not inside this transaction. |
| Each workflow node | One transaction: side effects with `claim_fence`, then the checkpoint. Assembly of the assessment, findings, calculations, and AI observations is one transaction. |
| Record outcome | Lock the application. Run the disposition gates. `record_review_outcome`. Move to `review_completed`. Append audit. |
| Request clarification | Lock the application. Insert `clarifications`. Move to `clarification_required`. Append audit. No outcome row. |

A node that loses its fence writes nothing. Lease expiry does not mark the run failed (ADR-015).

---

## 12. Security, privacy, and AI governance

| Control | MVP expectation |
|---|---|
| Secrets | Env / secret manager only; never in repo |
| Transport | TLS outside local Compose |
| Stored files | Quarantine, then malware scan, then controlled storage. Originals and SHA-256 retained. Volumes encrypted at rest. S3-compatible storage uses server-side encryption (ADR-025) |
| Download | Authenticated API stream only. Active Loan Officer, Reviewer, or Admin. Scan status must be `clean`. Storage URIs are not returned to clients. Each download is audited |
| Logs | No raw ID docs, full account numbers, secrets, or unmasked PAN where avoidable |
| UI | Mask sensitive identifiers |
| Uploads | Validate type/signature/size; treat text as data, not instructions |
| Audit | One global chain (ADR-012). Inserts go through a single DB function that locks `audit_chain_state` `FOR UPDATE`, assigns the next `chain_seq`, and stores `prev_event_hash` / `event_hash`. App role cannot insert or update `audit_events` directly. A verifier rejects gaps, hash mismatches, and a tail that disagrees with `audit_chain_state` |
| Immutability | Findings, extractions, assessments (completed body), comments, outcomes, snapshots, dispositions: no update/delete APIs; DB triggers or grants as defence in depth |
| AuthN | Server-side session (ADR-019). Inactive users and revoked sessions fail closed |
| AuthZ | Backend RBAC: Loan Officer / Reviewer / Admin. Outcome, disposition, correction, comment, and clarification writes re-check the role in the database, not only in the router |
| Prompt injection | Adversarial tests; isolation of system prompts from document text |
| Fairness | No caste/religion/gender as risk features; tests assert absence |
| Demo data | Synthetic only |
| Compliance claims | None (no RBI/privacy/lender certification claims) |

---

## 13. Observability and evaluation

- Structured logs with `request_id`, `application_id`, `processing_run_id`, `assessment_id`, `input_snapshot_id` where applicable.
- Traces spanning API → worker → workflow nodes → provider calls.
- Metrics: API latency/error rate; provider latency/tokens; queue depth; run durations; lease reclaim count.
- Evaluation: versioned synthetic dataset; component metrics per PRD §14; publish only measured results.

---

## 14. Deployment topology (MVP)

```text
docker compose:
  api          # FastAPI
  worker       # claim loop + LangGraph execution (N replicas optional)
  db           # PostgreSQL + pgvector
  scanner      # ClamAV clamd (ADR-025); replaceable behind malware_scan_status
  web          # frontend (optional separate service)
  volumes      # quarantine, clean uploads, postgres data; encrypted at rest
```

- Environment-driven config (providers, retry limits, max upload size, worker lease TTL).
- Migrations via Alembic (or equivalent) applied on deploy/start in controlled fashion.
- CI: unit, integration, retrieval regression, authz, e2e on synthetic fixtures; concurrency tests for dedup, single active run, audit-chain serialization, and stale-fence rejection.

---

## 15. Suggested implementation order

Aligned to PRD “one module at a time against acceptance criteria”:

1. Identity + server-side sessions + RBAC + global audit chain (`audit_chain_state`) + application Draft CRUD + the ten PRD states + `submitted_at`  
2. Document upload validation + malware scan + per-application hash dedup + mandatory hash-equality trigger + mediated download + failure events/resolutions + composite application FKs  
3. Workflow shell + fenced PostgreSQL job claim + processing runs + checkpoints + the §5 transitions  
4. Input snapshots + `snapshot_calculation_inputs` at run start; policy selection failure before assessment  
5. Classification + OCR/extraction + run-scoped `document_pages` + extraction logical keys + append-only field review actions  
6. Verification rules BR-01..BR-11 + immutable findings (`is_insufficient_evidence`) + logical keys  
7. Financial calculators + calculation-status tuple + deferred insufficient-evidence link + net-income trigger  
8. AI observations (including BR-12) separate from findings  
9. Policy ingest + non-overlapping selection + lexical full-text index + citations. Vector column and dense retrieval wait on ADR-020  
10. Assessment assembly in one transaction + finding dispositions (`dismissed` does not close Critical) + outcome and supersession functions under an application row lock  
11. PDF export  
12. Observability, chain verifier, evaluation harness, hardening (injection, authz, fence, hash equality, outcome concurrency, immutability tests)

---

## 16. Explicit non-goals (architecture)

- Autonomous credit decisions  
- Real applicant data in public demos  
- External identity/bureau verification  
- Multi-tenant SaaS isolation beyond single-tenant demo  
- Redis/microservices unless measured need  
- Calling PostgreSQL full-text search BM25, or describing the two as equivalent  
- An eleventh application state, including Needs Documents  
- A `document_requests` checklist in the MVP  
- Fine-tuned local models (Phase 3+)  
- Mutating historical findings, assessments, outcomes, or audit rows in place  
- Choosing an embedding model in this document before the deployment selects one

---

## 17. Related documents (to produce next)

| Document | Role |
|---|---|
| [DATABASE_DESIGN.md](./DATABASE_DESIGN.md) | Schema, ERD, constraints |
| [ARCHITECTURE_REVISION_CHANGELOG.md](./ARCHITECTURE_REVISION_CHANGELOG.md) | v1.0 → v1.3 change log and remaining open decision |
| [CONSISTENCY_REPORT.md](./CONSISTENCY_REPORT.md) | Current cross-check of schema, architecture, and PRD |
| [CONSISTENCY_REPORT_v1.2.md](./CONSISTENCY_REPORT_v1.2.md) | Historical v1.2 cross-check; superseded |
| API OpenAPI / contracts | Request/response schemas |
| Ingestion & extraction pipeline spec | OCR/provider details |
| Verification & calculation spec | Rule algorithms & edge cases |
| Policy retrieval spec | Chunking, fusion, rerank |
| LangGraph workflow design | Node I/O and failure map |
| Provider abstraction spec | Interfaces & config keys |
| Evaluation & regression plan | Dataset + metrics harness |
| Security & RBAC design | Threat model, retention |
| ADRs | Per material technical choice (also embedded above) |

---

*End of Technical Architecture v1.3*

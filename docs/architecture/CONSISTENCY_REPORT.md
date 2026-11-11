# LoanLens consistency report

| Attribute | Value |
|---|---|
| Date | 04 November 2026 |
| Version | 1.3 |
| Compared | `TECHNICAL_ARCHITECTURE.md` v1.3, `DATABASE_DESIGN.md` v1.3, `LoanLens_Final_PRD_v1.0.docx` |
| Supersedes | [CONSISTENCY_REPORT_v1.2.md](./CONSISTENCY_REPORT_v1.2.md) |
| Change log | [ARCHITECTURE_REVISION_CHANGELOG.md](./ARCHITECTURE_REVISION_CHANGELOG.md) |

This report checks the v1.3 design against the PRD. A row is **Pass** only when the architecture and the database say the same thing and that behaviour is the PRD’s behaviour. **Pass, limitation recorded** means the PRD is met and a non-blocking MVP limit is written down. **Open** means an implementer still lacks a fact the design deliberately did not invent.

**Verdict.** The architecture and the database design agree with each other and with PRD v1.0 on product behaviour. They are ready for implementation of every MVP area except the dense-retrieval column. **One item remains open: the embedding model and vector dimension (U-04).** No PRD amendment is required for the decisions in v1.3.

---

## Review items from the v1.2 follow-up

| ID | Required check | v1.3 result | Where |
|---|---|---|---|
| 1 | Document content hash on `application_documents` must match `stored_files`. The trigger is mandatory. | **Pass.** `BEFORE INSERT` trigger. Hash columns are immutable. `stored_files.content_sha256` is unique. | ADR-021. Database §4.3 |
| 2 | Two concurrent transactions must not create two active review outcomes. | **Pass.** Direct insert revoked. Both write functions take `FOR UPDATE` on `applications`. Deferred constraint: at most one unsurperseded outcome. | ADR-022. Database §4.7 |
| 3 | Map document-failure resolutions to the PRD dispositions and next actions. | **Pass.** Same table in both documents. Resolutions do not satisfy §7.2. Clarification is a state and a `clarifications` row, not a resolution kind. | Architecture §7. Database §4.3 |
| 4 | Legal combinations of calculation status, numeric result, and provisional flag. | **Pass.** One check allows only the three shapes below. | ADR-023. Database §4.5 |
| 5 | Malware scanning, encryption at rest, and download authorization. | **Pass.** Quarantine and clean-scan insert. Volume or server-side encryption. Authenticated download stream for the three MVP roles. | ADR-025 |
| U-01 | Count lifecycle states against the PRD. | **Pass.** Ten states. ADR-005 is withdrawn. `needs_documents` is not in the enum. | ADR-017. Architecture §5 |
| U-02 | Reviewer action for more documents versus clarification. | **Pass.** One action: Request clarification. Referral records `review_completed` and does not open clarification. Further information uses Reopen, then clarification if still needed. | ADR-018 |
| U-03 | Authentication approach and why. | **Pass.** Server-side sessions, Argon2id, CSRF on mutating requests. Chosen because revocation stays in PostgreSQL and the PRD does not require JWT. | ADR-019. `user_sessions` |
| U-04 | Embedding model and dimension. | **Open / deferred.** No model and no N. The vector column must not be created until the deployment names the model. | ADR-020 |
| U-06 | Insert order for the insufficient-evidence finding and the calculation. | **Pass.** Finding id is required on `not_computable`. Foreign key is deferred to commit. Flag `is_insufficient_evidence` is checked at commit. One assessment transaction. | ADR-023 |
| U-07 | PostgreSQL full-text search is not BM25 and is not called equivalent. | **Pass.** Both documents say lexical / full-text search. Neither equates `tsvector` or `ts_rank` with BM25. | ADR-002 |
| U-08 | Is `document_requests` in the MVP? | **Pass, limitation recorded.** The table is not in the schema. Clarification text names documents. There is no per-type fulfillment row. | ADR-024 |

Legal calculation shapes, identical in both documents:

| Status | Result | Provisional | Limitation text | Gap finding |
|---|---|---|---|---|
| `computed` | required | false | null | null |
| `not_computable` | null | false | optional | required |
| `provisional` | required | true | required | null |

---

## Product traceability

### Decision support and roles

| PRD requirement | Architecture | Database | Result | Gap |
|---|---|---|---|---|
| No automatic approve or reject | Principle 1. Outcomes are Reviewer functions only | `record_review_outcome` requires `role = reviewer` and the active assignment. Direct insert revoked | Pass | None |
| Loan Officer, Reviewer, Admin only. No Operations Analyst role | Three-role RBAC | `user_role` enum of those three values | Pass | None |
| Reviewer alone records the outcome. Admin assigns and retries and does not record an outcome | §10 and §11 | Role check inside the outcome function | Pass | None |
| Loan Officer does not correct fields or dismiss findings. Reviewer does not upload | §5 upload rule and §10 | Upload trigger allows only Loan Officer or Admin, and only in `draft`, `clarification_required`, or `processing_failed`. Disposition and correction writers require Reviewer | Pass | None |
| Backend enforcement, not UI hiding | Principle 7. Role re-checked in the database for review writes | Triggers and write functions | Pass | None |
| Applicant is not a user | No applicant login | No applicant user role | Pass | None |

### Functional requirements

| PRD | Architecture | Database | Result | Gap |
|---|---|---|---|---|
| FR-01 Create, view, update applications | Intake module. `/api/v1` applications | `applications`, `applicants`, audit via `append_audit_event` | Pass | None |
| FR-02 Multiple documents per application | Upload pipeline | `application_documents` many per application | Pass | None |
| FR-03 Type, size, signature, metadata | Pipeline step before persist | `mime_type`, `byte_size`, `signature_ok` | Pass | None |
| Duplicate content hash warns and does not store a second active copy | §7 and worker idempotency | Partial unique `(application_id, content_sha256)` for non-replaced rows | Pass | None |
| Same hash means the same bytes | ADR-021 | Mandatory hash-equality trigger | Pass | None |
| FR-04 Classify or mark unsupported | Document intelligence | `document_type` includes `unsupported`. Event `marked_unsupported` | Pass | None |
| FR-05 / §6.1 Structured extraction and four value layers | Pipeline and principles 4–5 | `extracted_fields`, `normalized_fields`, snapshot statuses, `field_corrections`. Extraction rows are not updated | Pass | None |
| FR-06 Source page and evidence text | `document_page_id` | Composite FK to run-scoped `document_pages`, plus same-document trigger | Pass | None |
| A field with no evidence is not a verified fact | §7 rule | Verified presentation requires page and evidence text | Pass | None |
| FR-07 / FR-08 Compare application and documents; missing and conflicting information | Verification module. BR-01…BR-11 | `findings` with `detection_source` of `deterministic` or `checklist` | Pass | None |
| Conflicting values are shown, not auto-picked | §8 | `calc_input_status = disputed` stores both values in `evidence_json` and forbids a selected value | Pass | None |
| FR-09 Deterministic indicators. Zero or missing denominator is Not computable | §8 pure functions and the tuple table | Tuple check, deferred finding link, net-income trigger | Pass | None |
| Declared net income required, ≥ 0, ≤ gross, not overwritten by extraction | §8 | `applicants.net_monthly_income` NOT NULL and those CHECKs. Snapshot copies the declared amount. Status lives on `snapshot_calculation_inputs` | Pass | None |
| Net income is never “missing” as a null amount | §8 mapping of §9.1 words onto statuses | Check: this input’s status is not `missing` | Pass | None |
| Provisional only when labelled, and not treated as verified | §8 | `provisional` requires `is_provisional` and limitation text, and forbids a gap-finding id | Pass | None |
| Other income without evidence stays out of default calculations | §8 | `other_monthly_income` nullable; `other_monthly_income_evidenced` | Pass | None |
| Annual rate comes from the policy version | §9. ADR-016 | `policy_versions.annual_interest_rate_pct`. Copied onto the snapshot. Not a free-typed application column | Pass | None |
| Zero interest does not divide by zero | §8 | EMI uses the zero-rate branch when the frozen rate is verified and 0 | Pass | None |
| FR-10 Hybrid lexical and vector retrieval, effective date | §9. Lexical path is specified. Vector path is specified and switched off until N exists | `content_tsv` GIN index. `embedding vector(N)` is not created yet | **Open for the vector half.** Lexical half Pass | U-04. Need the embedding provider and model |
| FR-11 Rerank | Provider interface | Citations store post-rerank `rank` | Pass for the contract. The reranker vendor is configuration, which the PRD allows | None in the product rules |
| Do not call PostgreSQL full-text search BM25 | ADR-002 | §4.6 index note | Pass | None |
| FR-12 Evidence-backed assessment and PDF | §10 | `assessments` and child tables. Reproducibility checklist §11 | Pass | None |
| Record the policy version used. Do not invent a policy | ADR-016. Zero or many matches fail the run and insert no assessment | `policy_version_id` NOT NULL. Must equal the snapshot. Exclusion constraint on overlapping active ranges | Pass | None |
| FR-13 Correct fields, dismiss findings, comment, with reasons | §10. Append-only corrections, dispositions, comments | `field_corrections`, `finding_dispositions`, `review_comments`. Reasons NOT NULL. Comments have no update | Pass | None |
| Dismiss stays visible and does not close Critical or insufficient evidence | ADR-014 | `dismissed` remains in the enum. Outcome function ignores it for that gate | Pass | None |
| FR-14 Human outcome and append-only audit | ADR-010, ADR-012, ADR-022 | `review_outcomes`, `review_outcome_supersessions`, `audit_events`, `audit_chain_state` | Pass | None |
| Previous outcome kept on reopen | ADR-010. Reopen inserts a supersession and does not update the outcome | Unique `(outcome_id)` on supersessions. No superseded columns on the outcome | Pass | None |
| FR-15 Status, failures, retry | §5 and §6. Failure events stay visible | `processing_runs`, `document_failure_events`, `document_failure_resolutions` | Pass | None |
| One active run. Second request rejected | §6 `SKIP LOCKED` and partial unique | Partial unique on `queued`/`running` | Pass | None |
| FR-16 Versions on the assessment | §10 package | Policy, rules, prompt, model snapshot, embedding version, reranker, evaluation dataset columns | Pass | Embedding version is null until a model is selected. That matches “where applicable” |
| FR-17 Metrics and traces | §13 | `llm_usage_events`, `evaluation_runs` | Pass | None |
| FR-18 Configurable providers, no silent fallback | ADR-003 | `provider_snapshot_json` on the run | Pass | None |

### Lifecycle (PRD §7)

The enum in the database and the diagram in the architecture are the same ten states: Draft, Submitted, Processing, Processing failed, Awaiting human review, In review, Clarification required, Escalated, Review completed, Archived.

| PRD rule | Architecture | Database | Result | Gap |
|---|---|---|---|---|
| Ten states, and a person cannot edit a processing state | §5. ADR-017 withdraws the eleventh state | Enum without `needs_documents`. Edge trigger | Pass | None |
| Edges named in §7 | §5 diagram, including Processing → Awaiting human review or Processing failed, Clarification required → Processing or Archived, Escalated → In review or Review completed, Review completed → Archived | The same edge list inside the status trigger | Pass | None |
| Withdrawal in Draft, Processing failed, and Clarification required | §5 | Those three edges to `archived`, plus Admin archive from Awaiting human review and Review completed | Pass | None |
| Retry only from Processing failed, as a new run | §5 and §6 | New `processing_runs` row. Partial unique active run | Pass | None |
| Reopen creates a new assessment and keeps the previous outcome | §5 Reopen row. Status goes `review_completed` → `processing`, then `in_review` when the new assessment exists | Supersession insert, then a new snapshot and run. Outcome row unchanged | Pass | None. See the interpretation note under Remaining items |
| Reprocessing after a new document or a Reviewer correction | §5. Correction edge is `in_review` → `processing` | Edge trigger allows that pair. New snapshot and run | Pass | None |
| History of every transition | §5 | `application_state_history` required by the status trigger | Pass | None |
| `submitted_at` is the application date for policy | §5 and §9 | Set once. Trigger rejects a later change. As-of date is the UTC calendar date | Pass | None |
| Clarification required only through the review flow | Request clarification is from `in_review` only. Processing does not jump there | Edge list has no `processing` → `clarification_required` | Pass | None |
| Escalation pauses ordinary review. An authorized Reviewer may still complete | §5. Clarification and correction are rejected while escalated. The assigned Reviewer may record the outcome | Outcome function allows `escalated` only for the assigned Reviewer | Pass | None |
| Unassigned queue and age. No auto-decision | §5 claim/assign | Partial unique active assignment. Queue age uses status and `updated_at` | Pass | None |
| Review completed is read-only aside from reopen and archive | §5 | Outcome, correction, and clarification writers require `in_review` or, for outcome only, `escalated` | Pass | None |

### Failures, dispositions, and traceability

| PRD | Architecture | Database | Result | Gap |
|---|---|---|---|---|
| Failure or unreadable evidence is never hidden or treated as success | ADR-013. §7 | Append-only events. Resolutions reference the event and do not update it | Pass | None |
| Next actions: retry, replacement, clarification | §7 map | Resolution kinds `retry_requested`, `replacement_requested`, `replaced`. Clarification is `clarifications` plus status `clarification_required` | Pass | None |
| §7.2 dispositions: resolved, overridden, decision without evidence | §10 gate | `finding_disposition_type`. `decision_without_evidence` requires `unresolved_evidence_visible` | Pass | None |
| Outcome blocked while a Critical or insufficient-evidence item lacks one of those three | §10. ADR-014 | `record_review_outcome` reads the latest disposition. `is_insufficient_evidence` marks the second class. `dismissed` does not pass | Pass | None |
| Major needs a comment or a disposition | §10, enforcing §10.1 | Same check in `record_review_outcome` | Pass | None |
| Minor and Info do not block completion | §10 | Gate ignores them | Pass | None |
| Findings are not deleted when dismissed | ADR-007 | `findings` insert-only. Disposition is a new row | Pass | None |
| Comments cannot be edited or deleted | §12 immutability | `review_comments` has no `updated_at`. Revoke update and delete | Pass | None |
| Corrections do not overwrite extraction | Principle 5 | `field_corrections` rows. `extracted_fields.review_status` stays `pending`. Display status comes from corrections and `field_review_actions` | Pass | None |
| Page provenance on the same application | ADR-011 | Composite `(id, application_id)` foreign keys | Pass | None |
| Assessment reproducible from frozen inputs | ADR-008, ADR-009 | `application_input_snapshots`, `snapshot_calculation_inputs`. Checklist in database §11 | Pass | None |
| Audit append-only and protected from a forked chain | ADR-012 | `append_audit_event` locks `audit_chain_state`. Direct insert revoked. Verifier recomputes hashes | Pass | None |
| Worker replay does not duplicate side effects or commit after losing the lease | ADR-015 | `fence_token`, `claim_fence`, logical unique keys | Pass | None |

### Business rules

| Rule | Stored as | Severity gate | Result |
|---|---|---|---|
| BR-01…BR-11 | `findings`, `detection_source` deterministic or checklist | Critical uses the §7.2 gate. Major uses comment or disposition. Minor and Info do not block | Pass |
| BR-01 blocks submission when a mandatory document is missing | Intake validation before `submitted`, and a checklist finding if reached later | Critical | Pass |
| BR-12 anomaly signals | `ai_observations` only, label `anomaly_signal_br12` | Info. Never an outcome | Pass |
| Thresholds from the policy version, not hard-coded | §8 | `policy_threshold_configs` | Pass |
| Identity checks are cross-document only | §7 identity limit | No external-verification table | Pass |

### Security and operations

| PRD | Architecture | Database | Result | Gap |
|---|---|---|---|---|
| Authenticated protected endpoints | ADR-019 session cookie | `user_sessions.token_hash`, expiry, `revoked_at`, user still active | Pass | Idle and absolute timeout values are configuration, not product rules |
| Encryption in transit | TLS outside local Compose | Not a column | Pass | None |
| Protection of stored files, encryption at rest | ADR-025 volume encryption; SSE when the store is S3-compatible | Scan metadata required on `stored_files`. URI is internal | Pass | A specific key-rotation period is not invented. It belongs in the security specification, as the PRD already says for retention |
| Untrusted uploads, including prompt injection | Principle 8. Quarantine, scan, then store. Text is data | No document row until the scan is clean | Pass | None |
| Malware scan before the file is usable | ADR-025. ClamAV in Compose, replaceable | `malware_scanner`, `malware_scanned_at` NOT NULL | Pass | None |
| Download authorization | Mediated `GET` content route. Active Loan Officer, Reviewer, or Admin. Audit the download | No public URI. Integrity-matrix row | Pass | None |
| Masking and log minimization | §12 | Not a schema rule. Operational control | Pass | None |
| Sensitive characteristics are not risk features | §12 fairness tests | No such columns on calculations or findings | Pass | None |
| Synthetic demo data | Principle and non-goals | `policy_documents.lender_label` default `synthetic_demo` | Pass | None |
| No RBI or bureau claim, no external verification | Non-goals | No bureau tables | Pass | None |
| Modular monolith, PostgreSQL, pgvector, `/api/v1`, Compose, no Redis unless measured | §3, ADR-001, ADR-006 | PostgreSQL 16. Job claim is `SKIP LOCKED` | Pass | None |
| Retryable, idempotent background work | §6 | Fence, logical keys, idempotency key | Pass | None |
| API transactions match job transactions | §11 transaction table | Outcome and supersede functions, hash trigger, node `claim_fence`, one transaction for assessment assembly | Pass | OpenAPI field schemas remain a separate document, which PRD Appendix B already requires |

### Reporting

| PRD §16 content | Where it is read from | Result |
|---|---|---|
| Summary and version ids | `assessments` | Pass |
| Document inventory, including unsupported and unreadable | Snapshot `document_inventory_json`, document status, failure events | Pass |
| Provenance and verification status | Extractions, pages, `snapshot_calculation_inputs` | Pass |
| Findings, severity, disposition chain | `findings`, `finding_dispositions` | Pass |
| Calculations, inputs, formula, rule version | `assessment_calculations`, `business_rules_versions` | Pass |
| Unresolved limitations | `unresolved_evidence_summary`, `unresolved_evidence_visible` | Pass |
| Policy citations | `assessment_policy_citations` and `policy_chunks` | Pass |
| AI observations, labelled and separate | `ai_observations` | Pass |
| Comments, corrections, overrides, outcome, reviewer, time, history | `review_comments`, `field_corrections`, dispositions, `review_outcomes`, supersessions | Pass |

---

## Remaining items

| Item | Status | What is needed |
|---|---|---|
| U-04 embedding model and dimension N | **Open.** Deferred on purpose | The deployment’s embedding provider and model. The migration that adds `policy_chunks.embedding` records N and does not change it in place. Until then, retrieval is lexical full-text search plus rerank. That is not yet the full hybrid in FR-10 |
| Retention periods and Argon2id cost parameters | Left to the security specification, as PRD §13.1 already requires | Those documents. v1.3 does not invent the numbers |
| OpenAPI schemas | Separate document, PRD Appendix B | Not a product gap. Transaction boundaries are specified in architecture §11 |
| Reopen and correction pass through `processing` | **Interpretation, same in both design documents.** PRD §7 names `in_review` as the business destination of reopen. Producing a new assessment requires a run, and `processing` is the state in which the system runs that work. Success then enters `in_review` when an assignment is active. The first successful run still enters the unassigned queue | No further decision unless product owners want the application status to stay `review_completed` or `in_review` while a run is active. That alternative was rejected because it hides an active run |

No other U-item from v1.2 remains open. U-05 (one global audit chain) stayed closed.

---

## PRD amendment

None is required for v1.3.

An amendment would be required before any of these, and v1.3 does not do them:

| Change | Why the PRD would have to move first |
|---|---|
| Add an eleventh state such as Needs Documents | §7 says ten states and names them |
| Make `referred_for_more_information` enter Clarification required in the same action | §7 does not list Review completed → Clarification required |
| Let `dismissed` satisfy the Critical or insufficient-evidence gate | §7.2 lists three dispositions |
| Store an assessment when no policy version applies | The assessment must record the policy version used |
| Choose silently among overlapping active policies | The PRD does not allow a silent choice |
| Call the PostgreSQL full-text index BM25 | §12.1 forbids that unless a BM25 engine is selected and benchmarked |

---

## Readiness

Ready for implementation of identity, sessions, intake, scanning, the ten-state workflow, extraction, verification, calculations, review, outcomes, audit, and lexical policy search.

Not ready to create `policy_chunks.embedding` or to turn on dense retrieval. That waits on U-04. The rest of policy ingest, full-text search, citations, and rerank of lexical candidates can proceed.

This revision does not include application code or migrations.

---

*End of consistency report v1.3*

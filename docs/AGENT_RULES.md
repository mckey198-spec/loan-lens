# LoanLens agent and contributor rules

| Attribute | Value |
|---|---|
| Product | LoanLens |
| Document | Mandatory engineering rules |
| Version | 1.0 |
| Date | 04 November 2026 |
| Status | Binding on AI coding agents and human contributors |
| Product source | `LoanLens_Final_PRD_v1.0.docx` |
| Architecture | [TECHNICAL_ARCHITECTURE.md](architecture/TECHNICAL_ARCHITECTURE.md) v1.3 |
| Database | [DATABASE_DESIGN.md](architecture/DATABASE_DESIGN.md) v1.3 |
| Revisions | [ARCHITECTURE_REVISION_CHANGELOG.md](architecture/ARCHITECTURE_REVISION_CHANGELOG.md) — current status is the v1.3 section |
| Tools | [TOOLS.md](TOOLS.md) |
| Plan | [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) |

These are instructions, not suggestions. If a task cannot be completed within them, stop and report the blocker.

---

## 1. Project understanding

Before changing the repository:

1. Read the sections of the documents above that apply to the task. Read [TOOLS.md](TOOLS.md) before adding a dependency. Read the milestone and task in [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) before implementing.
2. State the requested scope in one paragraph. If the request is ambiguous, ask. Do not fill the gap with an invented requirement.
3. Inspect the existing tree. The repository may already contain code. Do not replace an implementation because it differs from a template.
4. Identify the modules, tables, and providers the task depends on.
5. Identify the ADRs that govern the task. ADR-005 is withdrawn. ADR-001 through ADR-004 and ADR-006 through ADR-025 are in force, except where a later ADR narrows an earlier one.
6. Identify unresolved decisions. U-04 (embedding model and dimension) is open. The escalation conflict in §2 is open. The `citext` mismatch in §2 is open. Do not close them in code.

The current design is architecture v1.3, database design v1.3, and the v1.3 section of the changelog. v1.1 and v1.2 changelog sections, and [CONSISTENCY_REPORT_v1.2.md](architecture/CONSISTENCY_REPORT_v1.2.md), are history.

---

## 2. Documentation authority

| Question | Authority |
|---|---|
| What the product does | PRD |
| How the system is structured | Technical Architecture, including in-force ADRs |
| Tables, constraints, and integrity | Database Design |
| What changed, and which decisions are still open | Architecture Revision Change Log, v1.3 section |
| Which tools may be used | [TOOLS.md](TOOLS.md) |
| What to build next | [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) |
| How to behave while building | This document |

If two authorities disagree, stop. Report the documents, the sections, and the behaviour each one requires. Do not pick a side in code or in a silent edit.

Known conflicts and mismatches, already recorded and not resolved by these governance documents:

| Topic | Documents | Required behaviour |
|---|---|---|
| Outcome recording while status is `escalated` | PRD §7.3, §11.1, and Appendix A pause decision recording until return, reassignment, or senior handling. Architecture §5 and the outcome function allow the assigned Reviewer to record an outcome from `escalated`. [CONSISTENCY_REPORT.md](architecture/CONSISTENCY_REPORT.md) marks this Pass. | Do not implement that path. The roadmap task stays Blocked until a person resolves it. Outcome recording from `in_review` is unaffected. |
| `users.email` and `citext` | Database Design §4.1 types the column as `CITEXT`. §8 calls the `citext` extension optional. | Do not enable `citext` in the M01 migration. Do not change the column type to “resolve” the mismatch. |
| Reopen passes through `processing` | The consistency report records this as an interpretation shared by the architecture and the database design. PRD §7 names `in_review` as the business destination. | Follow the architecture until a person directs otherwise. Do not present the interpretation as a new product rule. |
| Vector index wording | Database Design §7 lists a vector index on `embedding`. ADR-020 forbids creating the column until U-04 is decided. | Do not create the column or the index. |

Do not invent business requirements, thresholds, retention periods, Argon2id cost parameters, or session timeouts. Thresholds come from versioned policy configuration. Numeric targets wait for a measured baseline (PRD §14).

---

## 3. Scope management

Every implementation task, before code, names:

- Objective.
- Scope.
- Allowed modifications.
- Prohibited modifications.
- Dependencies.
- Expected deliverables.
- Acceptance criteria.

Rules:

- One task implements one meaningful unit of functionality, matching one roadmap task unless the human request narrows it further.
- Do not implement a later milestone because it would be convenient.
- Do not refactor unrelated code.
- Do not add speculative endpoints, tables, flags, or configuration.
- Do not rewrite a module without approval.
- Do not modify the PRD, the technical architecture, the database design, or the revision changelog unless the human explicitly requests a documentation change to those files.
- Do not modify [TOOLS.md](TOOLS.md), this file, or the roadmap except to record an approved tool decision or a task status that the human asked to update.
- Ask when requirements are ambiguous.

---

## 4. Architecture compliance

- Keep the package boundaries in Architecture §4. Identity, intake, document intelligence, verification, financial, policy, assessment, workflow, and governance do not absorb each other’s responsibilities.
- Routers are thin. Business rules live in module services. Database access, file storage, and providers are injected.
- Follow the patterns the architecture already uses: append-only dispositions, frozen snapshots, provider interfaces, and fenced workers.
- Do not add an abstraction that has one implementation and no ADR.
- Do not duplicate a rule that already exists in another module or in a database constraint.
- Do not introduce circular imports between modules.
- Keep financial and verification logic free of FastAPI, LangGraph, and provider SDK types.
- Keep the SPA, the API, and provider SDKs swappable at the boundaries the architecture names. The SPA is not authorized to be the source of authorization.

A deviation from an ADR needs the change process in §11 before code lands.

---

## 5. Database integrity

The schema in Database Design v1.3 is binding.

- Create foreign keys, unique constraints, check constraints, and nullability as specified, including composite foreign keys that keep child rows on the same application (ADR-011).
- Enforce the calculation-status tuple and the deferred insufficient-evidence finding link (ADR-023).
- Enforce document-hash equality with the mandatory trigger (ADR-021).
- Use transactions that match Architecture §11. Do not commit a partial assessment.
- Respect concurrency controls: one active processing run, application-row locks for outcomes (ADR-022), fence tokens (ADR-015), and the single audit-chain lock (ADR-012).
- Make enqueue and node side effects idempotent under the logical keys in Architecture §6.
- Change schema only through Alembic revisions. Revisions that touch product tables are handwritten SQL. `target_metadata` stays empty. Do not autogenerate. Do not import `sqlalchemy` from application modules. Alembic’s installed SQLAlchemy package is not an ORM approval. See [TOOLS.md](TOOLS.md) §3.
- Do not bypass a constraint to make a test or a demo path succeed. Fix the caller or report the conflict.
- Do not run a destructive migration, a data wipe, or a down-revision against data you were not told to destroy.
- Do not create `policy_chunks.embedding`, a vector index, or `document_requests`.
- Do not enable `citext` until the mismatch in §2 is resolved by a person.
- Preserve append-only tables. Revoke direct update and delete where the database design requires it. Findings, outcomes, snapshots, and audit rows are not updated in place.

---

## 6. Financial correctness

- Do not invent a monetary amount, a rate, a tenure, or a ratio.
- Do not treat a missing optional amount as zero. Null stays null unless a business rule explicitly defines a substitute, and the PRD does not define that substitute for optional incomes and expenses.
- Do not divide by zero. Zero interest uses the principal-divided-by-tenure branch only when that rate input is verified and the value is 0, as specified in Architecture §8.
- Do not emit FOIR or loan-to-annual-income when the frozen net-income status does not allow it (ADR-009).
- Use `NUMERIC` in the database and a fixed-precision decimal type in Python. Do not use binary floats for money or ratios.
- Rounding, when required, is explicit in the calculation specification. Do not invent a rounding mode in an implementation task.
- Calculators are pure functions with their inputs in the arguments. LLM text, explanations, and `ai_observations` do not overwrite a calculator result.
- Persist inputs, formula identity, rule version, and calculation status with the result so a later reader can replay it.
- A `not_computable` row has a null result and links to its insufficient-evidence finding. A `provisional` row is labelled and is not a verified result.

---

## 7. AI engineering standards

- Prompts are versioned. Document text is data, not instructions. System instructions stay isolated from extracted text.
- Structured model output is validated before it is stored. A validation failure is an error on the run, not a silently repaired object.
- Retrieval for policy answers uses the selected policy version only. Insufficient evidence is an explicit result. Do not invent a clause.
- Do not add an embedding model, an embedding column, or dense retrieval while U-04 is open. Lexical retrieval may proceed.
- Call chat, embeddings, extraction, and reranking only through the provider interfaces (ADR-003). No silent fallback to a second provider.
- Separate extracted facts, deterministic findings, and labelled model observations. BR-12 is an `ai_observation`, not a finding.
- Every material model claim that is shown to a reviewer points at a document page, a calculation, or a policy citation. A claim without that link is not presented as a verified fact.
- Validate inputs before a provider call. Validate outputs before insert. Persist provider errors on the run and, where the design says so, as document-failure events.
- Record provider, model, token counts, latency, and status on `llm_usage_events` when a call is made. Do not put document bodies or secrets in that row.
- Evaluation uses the versioned synthetic dataset. Report measured values. Do not publish a target or a score that was not produced by a run.
- Do not add an LLM where a deterministic function or a database constraint already decides the outcome.
- Do not use an LLM to allow or deny an action.

---

## 8. Security requirements

- Secrets live in environment variables or an approved secret manager. They do not appear in source, fixtures, logs, error messages, or documentation.
- Every protected route requires a live, unrevoked session. Mutating routes also require the CSRF token (ADR-019).
- Enforce Loan Officer, Reviewer, and Admin as specified. There is no Operations Analyst role and no applicant login in the MVP.
- UI hiding is not authorization. Review writes re-check the role in the database.
- Treat uploads as hostile, including prompt-injection text. Check type, signature, and size. Scan in quarantine. Insert `stored_files` only after a clean scan (ADR-025).
- Do not return a storage URI. Download streams bytes to an active Loan Officer, Reviewer, or Admin and appends an audit event.
- Logs, test fixtures, traces, and examples use synthetic data. Do not place raw identity documents, full account numbers, or unmasked PAN in them.
- Validate input at the API boundary and again where a database constraint is the real invariant.
- Do not add a dependency with a known unaddressed vulnerability in order to finish a task. If a scanner is not yet approved, say so. Do not claim a scan ran.
- Do not claim regulatory or lender compliance.

---

## 9. Code quality standards

- Use names that match the architecture and the database: module names from Architecture §4, table and column names from the database design, state names from the ten-state enum.
- Keep a module’s public surface small. Callers depend on services, not on provider SDKs.
- Type function boundaries that cross modules. Do not silence a type error with an untyped escape unless the task records why.
- Raise structured errors. Do not swallow provider, constraint, or authorization failures.
- Log the correlation ids from Architecture §13. Log the failure class, not the sensitive payload.
- Comment only what the code and the ADRs cannot say: a non-obvious invariant or a pointer to the ADR.
- Reuse an existing function when it has the same contract. Do not copy it into a second module.
- Prefer straightforward code. Do not add a wrapper, a generic repository framework, or a plugin system for one caller.
- Inject the database, the file store, and providers at the composition root (`apps/api`, `apps/worker`).
- Configuration is environment-driven. Defaults that affect security or money are explicit. Unexplained constants are not allowed for policy thresholds.
- Delete dead code you created in the same task. Do not delete unfamiliar code as a drive-by.
- Do not optimize a path that has no measured problem.

---

## 10. Testing requirements

Verification matches the task.

| Kind | When it is required |
|---|---|
| Unit tests | Deterministic functions: money, rules, state decisions that are pure, hash helpers, prompt-output parsers. |
| Integration tests | Repositories, constraints, triggers, and transactions, once a data-access approach and a test runner are approved. |
| API tests | Each new route: success, validation error, unauthenticated, and wrong role. |
| Database tests | Unique constraints, checks, append-only grants, fence rejection, hash equality, audit-chain gaps, and concurrent outcomes. |
| End-to-end tests | The synthetic journey once the modules it crosses exist. |
| Security tests | Authz matrix, upload rejection, download denial, log redaction, and prompt-injection fixtures when those features exist. |
| AI evaluation | Measured retrieval or extraction runs on the versioned synthetic set. Not a substitute for unit tests of calculators. |
| Regression | A bug fix adds a test that fails before the fix and passes after it. |

M01 is verified by Docker Compose health checks and by applying the extension migration. No test runner is approved yet, so M01 does not claim unit tests.

Agents must not:

- Delete or skip a test to hide a failure.
- Weaken an assertion without stating why the old assertion was wrong.
- Mock the database when the behaviour under test is a constraint, a trigger, or a lock.
- Claim that tests passed without running them in this session.
- Ignore a failing test that already existed. Report it. Do not “fix” it by deleting it.

---

## 11. Change management

A change to an approved architecture decision follows this order. Skip none of the steps.

1. State why the current decision does not work for the task.
2. Quote the existing ADR or section.
3. Present at least one alternative, including keeping the current decision.
4. State the trade-off for integrity, security, and scope.
5. Obtain human approval.
6. Update the relevant ADR. That update is a documentation change the human has approved. It is not done by silently editing the architecture file inside a feature task.
7. Update [TOOLS.md](TOOLS.md) and the roadmap when the decision changes tools or sequencing.

Deferred decisions are not approved decisions. Implementing U-04, JWT, Redis, an eleventh state, or `document_requests` requires a new approved decision first.

---

## 12. Agent communication and reporting

When a task ends, report:

- Objective.
- Implementation summary.
- Files created.
- Files modified.
- Dependencies added or changed, including transitive packages that matter (SQLAlchemy installed by Alembic counts).
- Tests or other checks executed, by command.
- Actual results. If a check was not run, say that.
- Outstanding issues, including any authority conflict.
- Deviations from the architecture. If there are none, say so.

Separate completed work from recommendations. A recommendation is not part of the change.

---

## 13. Prohibited actions

- Implement outside the task scope or ahead of the roadmap milestone.
- Invent requirements, thresholds, providers, or schema.
- Close or implement a deferred decision, including U-04.
- Add an unapproved dependency, provider SDK, ORM, test runner, or frontend framework.
- Import SQLAlchemy from application code or turn on Alembic autogenerate.
- Change schema outside Alembic, or apply a destructive migration without explicit authorization.
- Enable `citext` while §2 still records the mismatch.
- Refactor unrelated modules or rewrite documentation that the task does not own.
- Put secrets or sensitive applicant data in logs, fixtures, errors, or docs.
- Skip the verification the task names.
- Fabricate a successful test result.
- Claim production readiness, compliance, or a measured accuracy number without the evidence in the repository.
- Auto-approve or auto-reject an application.
- Treat a dismissed Critical or insufficient-evidence finding as closing the outcome gate (ADR-014).

---

## 14. Definition of Done

A task is complete only when all of the following hold:

- The acceptance criteria in the task are met.
- Edits stayed inside the allowed files and the milestone scope.
- The code follows the in-force ADRs and [TOOLS.md](TOOLS.md).
- The verification named by the task exists.
- That verification was executed in the session, and the actual result is reported. M01’s verification is Compose and the migration, not a unit-test run.
- Failures that already existed are reported rather than hidden.
- Documentation required by the task is updated. Unrelated specifications are left unchanged.
- Outstanding issues and any blocked conflict are disclosed.
- The change is ready for human review. The agent does not mark the roadmap task Completed. A person does that after review.

Status words used with the roadmap are Planned, In Progress, Blocked, Implemented, Tested, Reviewed, and Completed. Implemented means the code exists. Tested means the named verification ran. Completed means a person has accepted it. No task is Completed because an agent finished typing.

---

*End of AGENT_RULES.md v1.0*

# LoanLens tool governance

| Attribute | Value |
|---|---|
| Product | LoanLens |
| Document | Technology and tooling governance |
| Version | 1.0 |
| Date | 04 November 2026 |
| Status | Engineering baseline. Does not change product behaviour or architecture. |
| Product source | `LoanLens_Final_PRD_v1.0.docx` |
| Architecture | [TECHNICAL_ARCHITECTURE.md](architecture/TECHNICAL_ARCHITECTURE.md) v1.3 |
| Database | [DATABASE_DESIGN.md](architecture/DATABASE_DESIGN.md) v1.3 |
| Revisions | [ARCHITECTURE_REVISION_CHANGELOG.md](architecture/ARCHITECTURE_REVISION_CHANGELOG.md) — current status is the v1.3 section |
| Consistency | [CONSISTENCY_REPORT.md](architecture/CONSISTENCY_REPORT.md) v1.3 |
| Companion rules | [AGENT_RULES.md](AGENT_RULES.md) |
| Companion plan | [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) |

This document says which technologies are approved, why they are used, and where they belong. It does not restate module behaviour, schema, or financial formulas. Those remain in the documents above.

**Status words**

| Status | Meaning |
|---|---|
| Approved | Named by the PRD or by an in-force architecture decision, and allowed for the stated purpose. |
| Conditional | A family or an implementation package is unnamed. A person must approve the specific product before it is added. |
| Deferred | An explicit open decision. Do not select it to complete an inventory. |
| Rejected | Withdrawn or forbidden for the MVP. |

**Changelog reading rule.** [ARCHITECTURE_REVISION_CHANGELOG.md](architecture/ARCHITECTURE_REVISION_CHANGELOG.md) keeps v1.1 and v1.2 text as history. The current design is the v1.3 section at the end of that file, together with architecture v1.3 and database design v1.3. [CONSISTENCY_REPORT_v1.2.md](architecture/CONSISTENCY_REPORT_v1.2.md) is superseded. Do not implement `needs_documents` or `document_requests` from the historical sections.

---

## 1. Technology stack overview

Categories follow the approved MVP defaults in Technical Architecture §3 and the constraints in PRD §12. Empty cells are intentional.

| Category | Approved choice | Boundary |
|---|---|---|
| Backend development | Python 3.12+, FastAPI, Pydantic v2, modular monolith | One deployable API. ADR-001. |
| Frontend development | Vite + React (TypeScript) SPA in `apps/web/` calling `/api/v1` | Approved (M14-T01 / ADR-026). Session cookie + CSRF; authorization stays on the API. |
| Database and data access | PostgreSQL 16 as the system of record | Application data-access library is not selected. See §3. |
| Database migrations | Alembic, one revision chain | Handwritten SQL until a persistence library is approved. |
| AI and LLM integration | Provider interfaces for chat | No vendor, model, or SDK is selected. ADR-003. |
| Document processing | Validation, quarantine, scan, then store | OCR and extraction vendors are not selected. |
| Retrieval and embeddings | PostgreSQL full-text search now; pgvector later | Embedding model and dimension N are deferred. ADR-020. |
| Background processing | Worker process and PostgreSQL `FOR UPDATE SKIP LOCKED` | LangGraph nodes run in the worker. ADR-006. |
| Authentication and security | Server-side sessions, Argon2id, CSRF, backend RBAC | ADR-019. JWT is rejected for the MVP browser API. |
| Testing and quality assurance | Test types required by PRD §12 and §18 | Test-runner package is not selected. |
| Code quality and static analysis | — | Linters and type checkers are not selected. |
| Logging and observability | Standard-library JSON logs; OpenTelemetry traces; `llm_usage_events` | Exporter packages are not selected. |
| Containerization | Docker Compose: `api`, `worker`, `db`, `scanner`, optional `web` | `web` serves the Vite SPA after M14-T01. |
| CI/CD and deployment | CI is required by PRD §18.2 | CI product is not selected. |
| Development and productivity tools | — | Editors, task runners, and extra CLIs are not approved here. |

---

## 2. Tool registry

### 2.1 Approved packages

| Tool | Category | Purpose | Usage | Alternatives | Status | Restrictions |
|---|---|---|---|---|---|---|
| Python 3.12+ | Backend | Language named for the API, worker, and deterministic engines | All backend packages under the layout in Architecture §4 | — | Approved | Do not add a second backend language. |
| FastAPI | Backend | Versioned REST under `/api/v1` and OpenAPI publication | Thin routers only. Business rules stay in module services. | — | Approved | Do not put lending rules, SQL, or provider calls in routers. |
| Pydantic v2 | Backend | Request, response, and settings validation; structured errors | API models and configuration. Error body is `code`, `message`, `details`, `request_id`. | — | Approved | Do not use Pydantic as a persistence layer or as an authorization engine. |
| OpenAPI (from FastAPI) | Backend | Published contract for `/api/v1` | Generated from the FastAPI app. Field schemas are still a separate document (PRD Appendix B). | A hand-maintained spec beside the generated one | Approved | Do not invent response fields that the API specification has not accepted. Do not keep a second hand-written spec that drifts from the generated one. |
| LangGraph | Background processing | Stateful processing graph with checkpoints and human hand-off | Worker executes the nodes sketched in Architecture §5. Checkpoints are rows in `workflow_checkpoints`. | A second orchestrator | Approved | Do not add another workflow engine. Do not let the graph auto-approve or auto-reject. |
| PostgreSQL 16 | Database | System of record, job queue, lexical search, and audit chain | One database. API and worker share it. | A second operational database | Approved | Do not split aggregates into separate databases in the MVP. |
| pgvector | Retrieval | Extension required for a future embedding column | Enable the extension. Do not create `policy_chunks.embedding` until U-04 is decided. | An external vector database | Approved as an extension. Deferred as a column. | Do not create the vector column, a vector index, or dense retrieval while U-04 is open. |
| PostgreSQL full-text search | Retrieval | Lexical half of hybrid retrieval | `tsvector` / `ts_rank` on policy chunks. Architecture ADR-002. | ParadeDB/pg_search or another BM25 engine, only after measurement | Approved | Do not call this BM25. Do not describe `ts_rank` as equivalent to BM25. |
| btree_gist | Database | Exclusion constraint for overlapping active policy date ranges | Enabled before policy-version tables. ADR-016. | An application-only overlap check | Approved | Do not enforce active-range overlap only in Python. |
| pgcrypto | Database | `gen_random_uuid()` for primary keys | Enabled in the first migration. Database Design §8 allows this or `uuid-ossp`. | `uuid-ossp` | Approved | Do not also enable `uuid-ossp`. |
| Docker Compose | Containerization | Reproducible local and demo runtime | Services `api`, `worker`, `db`, `scanner`. Volumes for quarantine, clean files, and database data. | A second local orchestrator | Approved | Do not add Redis or a microservice mesh to Compose. |
| Alembic | Migrations | Sole schema-change path | One linear revision chain of handwritten SQL. Architecture §14. Database Design §8. | Another migration framework, only by ADR | Approved | Do not change schema by hand, by startup DDL, or by a second tool. See §3 for SQLAlchemy. |
| ClamAV (`clamd`) | Security | MVP malware scanner | Compose service. Bytes are scanned in quarantine. Only a clean result inserts `stored_files`. ADR-025. | Another scanner that writes the same status values | Approved | Do not store unscanned or infected bytes as documents. Do not couple the schema to the ClamAV product name beyond the scanner label column. |
| ReportLab | Reporting | Deterministic assessment PDF export (PRD §16; Architecture §10) | `modules/reporting` only. Version-constrained in `requirements.txt`. | Another headless PDF renderer, only by a new tool decision | Approved (M13-T01) | Must keep BR-12 observations in a labelled advisory section. Must not mutate assessment, calculation, or finding rows. Do not use the LLM to assemble decision content. |

### 2.2 Approved controls

These are required behaviours. They are not package names. Do not add a library merely because a control is listed.

| Tool | Category | Purpose | Usage | Alternatives | Status | Restrictions |
|---|---|---|---|---|---|---|
| Argon2id | Authentication | Password hashing algorithm | `users.password_hash`. Parameters come from configuration. ADR-019. | A different password hash | Approved as an algorithm | Do not store plaintext passwords. Do not invent cost parameters; the security specification has not set them. The Python package that implements Argon2id is Conditional. |
| Server-side sessions | Authentication | Browser authentication with revocation in PostgreSQL | HttpOnly cookie. `Secure` outside local Compose. `SameSite=Lax`. Database stores the SHA-256 of the secret. ADR-019. | JWT on the MVP browser API | Approved | Do not accept a bearer JWT on the MVP HTTP API. Do not store the raw session secret. Idle and absolute timeouts are configuration and are not set in this document. |
| CSRF token | Authentication | Extra check on mutating requests | Required together with the session cookie. ADR-019. | — | Approved | Do not treat the cookie alone as sufficient for a mutation. |
| SHA-256 | Security | Content hashes, session-secret hashes, and audit event hashes | File dedup, hash-equality trigger, session storage, `audit_events`. | A second hash for the same purpose | Approved | Do not skip the mandatory hash-equality trigger (ADR-021). |
| Python `logging` (standard library) | Observability | Structured JSON logs | `request_id` and the correlation ids in Architecture §13. | A third-party logging library | Approved until a logging library is separately approved | Do not log raw identity documents, full account numbers, secrets, or unmasked PAN. |
| OpenTelemetry | Observability | Trace standard across API, worker, nodes, and providers | Architecture §3 and §13. FR-17. | A proprietary tracing SDK as the primary API | Approved as the standard | Exporter, collector, and auto-instrumentation packages are Conditional. |
| `llm_usage_events` | Observability | Token, latency, and provider-call record | Table in Database Design §4.8. Written by provider adapters. | A second usage store | Approved as a table | Do not add an LLM observability SaaS product without approval. Do not put prompt or document text in this table by default. |
| Environment variables | Security | Secret and runtime configuration | Providers, retry limits, upload size, lease TTL, passwords for seed users. | A hosted secret manager | Approved | Do not commit secrets. A hosted secret manager is Conditional. |
| TLS | Security | Encryption in transit outside local Compose | Architecture §12. | Plain HTTP outside Compose | Approved | Do not claim a certificate product; none is named. |
| Volume or disk encryption | Security | Encryption at rest for database and upload volumes in local Compose | ADR-025. | — | Approved as a deployment control | Do not invent a key-rotation period. |
| S3-compatible server-side encryption | Security | Encryption at rest when the object store is S3-compatible | ADR-025. Local Compose uses the filesystem on an encrypted volume. | A specific cloud SDK | Approved as a requirement | Do not add an object-storage SDK until that milestone. Clients never receive a storage URI. |

### 2.3 Conditional technologies

A Conditional row is not permission to install the package. An agent stops and asks for approval. Record the reason in the task that introduces it.

| Tool | Category | Purpose | Usage | Alternatives | Status | Restrictions |
|---|---|---|---|---|---|---|
| SQLAlchemy | Data access | Possible ORM or Core persistence layer | Not used by application code. | A database driver with explicit SQL; another ORM | Conditional | Alembic may install SQLAlchemy. That does not approve importing it from LoanLens modules. No autogenerate. See §3. |
| Database driver | Data access | Python connectivity for queries | Required before an API readiness check runs SQL, and before M02. | — | Conditional | No driver is named. Do not choose one inside a task. M01 uses `pg_isready` in the database container. |
| Argon2id implementation package | Authentication | Library that performs Argon2id | Identity milestone, after approval | Any maintained implementation of the same algorithm | Conditional | Do not substitute a different algorithm. |
| OpenTelemetry exporter and instrumentation packages | Observability | How spans leave the process | Hardening milestone | — | Conditional | The standard is approved. The packages are not. |
| SPA framework | Frontend | Browser client for the three MVP roles | Vite + React + TypeScript in `apps/web/`; Vitest for UI tests | Next.js or another SPA | Approved (M14-T01 / ADR-026) | Do not hide authorization in the UI. Mutating calls must send the CSRF token with the session cookie. |
| Chat provider and SDK | AI | Configurable LLM behind an interface | Assessment narrative and labelled observations only | Commercial or local, per PRD | Conditional | No silent fallback. No vendor is selected. ADR-003. |
| Embedding provider and model | Retrieval | Dense half of hybrid retrieval | Blocked by U-04 / ADR-020 | — | Conditional and deferred | Do not select a model in order to fill this row. |
| Rerank provider | Retrieval | Rerank after candidate retrieval. FR-11. | Provider interface. Vendor is configuration. | — | Conditional | Do not rerank without citations. Do not invent clauses when evidence is insufficient. |
| OCR and extraction provider | Document processing | Text and structured fields | Document-intelligence module, behind an interface | — | Conditional | Do not let extraction overwrite human corrections or decide findings. |
| Magic-byte / file-signature library | Document processing | Signature check required by FR-03 | Upload validation | — | Conditional | Do not trust extension or MIME alone. |
| PDF export library | Reporting | Assessment PDF required by PRD §16 | See ReportLab in §2.1 | — | Approved as ReportLab | Export must keep the traceability required by Architecture §10. |
| Test runner | Testing | Unit, integration, API, database, and end-to-end tests | After human approval, before M02 implementation | — | Conditional | PRD requires the test types. It does not name the runner. M01 is verified without one. |
| Linters and type checkers | Code quality | Static checks | After human approval | — | Conditional | Do not add them as drive-by dependencies. |
| CI product | CI/CD | PRD §18.2 requires tests in CI | After the runner exists and a product is approved | — | Conditional | Do not add a pipeline whose only effect is to install unapproved tools. |
| Dependency vulnerability scanner | Security | Notice known-vulnerable packages | After approval | — | Conditional | None is named. Do not claim a scan occurred if it did not. |
| Hosted secret manager | Security | Alternative to env-only secrets | Production-shaped deploys, if approved | Environment variables remain approved | Conditional | Do not commit secrets while waiting for a manager. |
| Second malware scanner | Security | Replacement for ClamAV if it writes the same statuses | ADR-025 allows a replacement | ClamAV remains the MVP default | Conditional | Schema must stay vendor-neutral. |
| Logging library beyond the standard library | Observability | Structured logging helper | Only if the standard library is shown to be insufficient | Python `logging` is the current choice | Conditional | Must still redact sensitive values. |

### 2.4 Deferred technologies

| Tool | Category | Purpose | Usage | Alternatives | Status | Restrictions |
|---|---|---|---|---|---|---|
| Embedding model and dimension N | Retrieval | Dense retrieval | U-04 / ADR-020. The deployment must name the provider and model. The migration that adds `policy_chunks.embedding` fixes N. | — | Deferred | Do not create the column. A later dimension is a new column or a new table. |
| Vector index on `policy_chunks.embedding` | Retrieval | Nearest-neighbour search | Database Design §7 mentions it | — | Deferred | Follow ADR-020. The index is not part of the baseline migration. |
| BM25 engine (ParadeDB/pg_search or another) | Retrieval | Possible later lexical upgrade | Only after measurement. PRD §12.1. | PostgreSQL full-text search stays the lexical engine | Deferred | Do not relabel current search as BM25. |
| Redis | Background processing | Possible later queue | Only if queue latency or lock contention is measured. ADR-006. | PostgreSQL `SKIP LOCKED` | Deferred | Forbidden in the MVP. |
| Celery | Background processing | Possible later worker framework | Same gate as Redis. | The Compose worker | Deferred | Forbidden in the MVP. ADR-006. |
| ARQ | Background processing | Possible later worker framework | Same gate as Redis. | The Compose worker | Deferred | Forbidden in the MVP. |

### 2.5 Rejected for the MVP

| Tool | Category | Purpose | Usage | Alternatives | Status | Restrictions |
|---|---|---|---|---|---|---|
| Bearer JWT on the browser API | Authentication | Token auth | — | Server-side sessions (ADR-019) | Rejected | A future non-browser client would be a separate decision. |
| `needs_documents` and any eleventh application state | Workflow | Extra wait state | — | The ten PRD §7 states (ADR-017) | Rejected | ADR-005 is withdrawn. |
| `document_requests` | Intake | Per-type document checklist | — | Clarification text (ADR-024) | Rejected | A later checklist needs a new decision. |
| Redis, Celery, ARQ, and any external broker | Background processing | Queue | — | PostgreSQL-backed worker | Rejected for MVP | See Deferred for the measured-need gate. |
| Microservices | Architecture | Split deployables | — | Modular monolith (ADR-001) | Rejected for MVP | — |
| Silent provider fallback | AI | Hide an outage by switching vendors inside a retry | — | Bounded retries, then a recorded failure (ADR-003) | Rejected | — |
| LLM arithmetic or LLM authorization | AI | Replace calculators, constraints, or RBAC | — | Deterministic code and database checks | Rejected | — |
| Calling PostgreSQL full-text search “BM25” | Retrieval | Terminology | — | “Lexical” or “full-text search” | Rejected | — |
| `uuid-ossp` beside `pgcrypto` | Database | Second UUID extension | — | `pgcrypto` alone | Rejected as a second extension | — |

---

## 3. Single-responsibility principle

One primary tool owns each function. Overlap needs an explicit reason in an ADR or in this document.

| Function | Primary tool | Does not own it |
|---|---|---|
| HTTP API | FastAPI | LangGraph, the database, the SPA |
| Request and response validation | Pydantic v2 | Database constraints |
| Schema change | Alembic | Application startup code, SQLAlchemy autogenerate, hand-edited production databases |
| System of record | PostgreSQL 16 | The ORM, files on disk, the LLM |
| Application queries | Unassigned | SQLAlchemy is not approved. Alembic does not own queries. |
| Lexical retrieval | PostgreSQL full-text search | pgvector, an LLM |
| Dense retrieval | pgvector, after U-04 | The lexical index. The column does not exist yet. |
| Reranking | The approved rerank provider interface | The lexical rank alone is not the final citation order once a reranker is configured. |
| Workflow orchestration | LangGraph in the worker | FastAPI request threads, Redis |
| Job claim and fencing | PostgreSQL `SKIP LOCKED` and `fence_token` | LangGraph’s memory, an external broker |
| Malware scanning | ClamAV `clamd` | The API process |
| Password hashing | Argon2id | Sessions, RBAC |
| Session authentication | Server-side session cookie | JWT |
| Authorization | Backend RBAC, re-checked in the database for review writes | The SPA, the LLM |
| Financial calculation | Deterministic functions in the financial module | The LLM, the PDF library |
| Document extraction | The extraction provider behind its interface | Verification rules |
| Policy answers | Retrieved chunks plus citations | Ungrounded generation |
| Audit append | `append_audit_event` | Direct inserts |
| Traces | OpenTelemetry | Ad hoc print debugging as the trace record |
| Local runtime | Docker Compose | A second compose file that adds unapproved services |

### Alembic and SQLAlchemy

Alembic versions and applies schema changes. SQLAlchemy is a Python SQL toolkit and ORM that would declare tables, open sessions, and run queries. The architecture names Alembic. It does not name SQLAlchemy.

Current Alembic releases install SQLAlchemy because Alembic is built on it. That install is a packaging fact. It does not authorize any of the following:

- Importing `sqlalchemy` from LoanLens application modules.
- ORM models or `Session` as the persistence layer.
- SQLAlchemy Core as the repository layer.
- Alembic autogenerate. Autogenerate would make model metadata a second schema source.

Until a person approves a data-access approach, revisions are handwritten SQL and Alembic `target_metadata` stays empty. M02 in the roadmap stays blocked on that decision.

---

## 4. Approved versus deferred technologies

**Approved** rows are in §2.1 and §2.2. They may be used for the purpose and under the restrictions in those rows.

**Conditional** rows are in §2.3. They stay out of the dependency set until a person approves a named package and the task records why.

**Deferred** rows are in §2.4. The embedding decision (U-04) blocks only the vector column, its index, and dense retrieval. Lexical retrieval, citations, and rerank wiring of lexical candidates can proceed. Redis, Celery, and ARQ stay out until measurement justifies them. A BM25 engine stays out until it is selected and benchmarked.

**Rejected** rows are in §2.5. Do not reintroduce them from the historical changelog.

This document does not choose an embedding model, a vector dimension, an OCR vendor, an LLM vendor, a reranker vendor, a PDF library, a frontend framework, a database driver, a test runner, or a CI product.

---

## 5. Dependency management

1. Every new dependency has one stated purpose that an approved tool does not already cover.
2. Prefer an approved package over a new one when the approved package can do the job.
3. Do not add a package for an unused feature, a tutorial default, or a transitive convenience.
4. Do not add a second package that solves the same problem. Alembic’s SQLAlchemy install is not a second migration tool and is not an ORM approval.
5. Before adding a significant dependency, record maintenance status, licence compatibility with the project, runtime compatibility with Python 3.12, and known security posture. Reject unmaintained or abandoned packages.
6. Pin or constrain versions in the project’s dependency manifest. Do not float unbounded ranges for direct dependencies.
7. Direct dependencies that implement an approved control (for example an Argon2id library) still need the Conditional approval in §2.3.
8. An agent does not add a major dependency, a provider SDK, or a framework inside an implementation task. See §8.
9. Removing a dependency that this document lists as Approved requires the change process in [AGENT_RULES.md](AGENT_RULES.md).

There is no dependency manifest in the repository yet. The first manifest may contain only packages this document marks Approved, plus packages a person has approved for a Conditional row.

---

## 6. AI-specific tooling governance

Deterministic business logic and probabilistic model output stay apart. Architecture principles 3, 5, and 12 are binding.

| Concern | Rule |
|---|---|
| LLM providers | Chat sits behind a provider interface. Configuration selects the provider. Outage behaviour is bounded retries, timeout, then a recorded failure. No silent fallback. ADR-003. |
| Structured outputs | Model output that is stored is validated against an explicit schema before insert. Invalid output is a failure, not a guessed repair. |
| Prompt management | Prompt version is recorded on the assessment where a prompt affected it. Prompts are data separate from document text. |
| Retrieval | Lexical search is PostgreSQL full-text search. Dense search waits on U-04. Queries are scoped to the one selected policy version. |
| Embedding models | Not selected. Do not add an embedding SDK to force the decision. |
| Reranking | Required by FR-11 once candidates exist. The vendor is Conditional. Citations store the post-rerank rank. |
| Document intelligence | Classification, OCR, and field extraction sit behind interfaces. Extracted values keep page, evidence text, method, and confidence. Confidence is not authenticity. |
| Evaluation | Versioned synthetic dataset. Publish measured results only. PRD §14. No numeric target is invented here. |
| Observability | Provider, model, operation, token counts, latency, and status go to `llm_usage_events`. Traces use OpenTelemetry once exporter packages are approved. |

The LLM must not compute EMI, FOIR, or loan-to-annual-income, must not choose between conflicting amounts, must not write `findings` for BR-01…BR-11, must not satisfy an authorization check, and must not insert or waive a database constraint. BR-12 and other model notes are `ai_observations` only.

---

## 7. Security and compliance tooling

LoanLens handles sensitive financial information in design. The MVP uses synthetic data in public demonstrations. This document claims no RBI, privacy-law, or lender certification. PRD §13.2 forbids those claims.

| Control | How it is implemented | Package status |
|---|---|---|
| Sensitive financial information | Mask identifiers in the UI. Keep them out of logs, fixtures, error messages, and docs. | Control. No extra package approved. |
| Authentication | Server-side sessions and Argon2id. ADR-019. | Algorithm approved. Implementation package Conditional. |
| Authorization | Backend RBAC for Loan Officer, Reviewer, and Admin. Review writes re-check the role in the database. | Application and database. No policy-engine product approved. |
| Secret management | Environment variables. No secrets in the repo. | Hosted manager is Conditional. |
| File security | MIME, extension, magic bytes, and size checks. Quarantine, then scan, then store. SHA-256 retained. ADR-025. | Magic-byte library is Conditional. |
| Malware scanning | ClamAV `clamd` in Compose. | Approved. |
| Encryption | TLS outside local Compose. Encrypted volumes. SSE when the store is S3-compatible. | Controls. No KMS product named. |
| Dependency vulnerability scanning | Required as a practice once dependencies exist. | Scanner package is Conditional. |
| Audit logging | One global hash chain via `append_audit_event`. ADR-012. | PostgreSQL. No external SIEM approved. |
| Download | Mediated `GET` content route for an active Loan Officer, Reviewer, or Admin. Audited. Storage URI is not returned. | API behaviour. |

Retention periods and Argon2id cost parameters belong to the security specification named in PRD §13.1. That specification is not written. Do not invent the numbers.

---

## 8. Tool selection policy

An agent or contributor who wants to add, replace, or remove a technology follows this sequence. Implementation does not start until step 6 allows it.

1. Name the function the tool would own and the existing row in §3 that already owns it, if any.
2. State why the approved tool cannot do the job, with a concrete gap.
3. List alternatives, including “do not add a tool”.
4. Record maintenance, compatibility with Python 3.12 and PostgreSQL 16, security, and licence.
5. If the change contradicts an ADR or the PRD, stop. Follow the change process in [AGENT_RULES.md](AGENT_RULES.md).
6. Obtain human approval. Conditional and Deferred rows always need this step. Approved rows do not need a new approval for their stated purpose.
7. Update this document in the same change that introduces the dependency. A major architectural change also updates the relevant ADR.
8. Pin the version and add the verification the task requires.

No agent independently introduces a new major dependency, framework, provider SDK, or datastore. Tutorial defaults, boilerplate, and “commonly used with FastAPI” are not approval.

---

*End of TOOLS.md v1.0*

## ADR-005: Container Topology & Worker Entrypoints (M01-T03)
- **Status:** Accepted
- **Context:** System requires API server, background execution, database with vector support, and anti-malware quarantine.
- **Decision:** Use Docker Compose orchestrating `api`, `worker`, `db` (`pgvector/pgvector:pg16`), and `scanner` (`clamav/clamav`). `api` and `worker` share the same Dockerfile/image with distinct entrypoints.
- **Why Chosen:** Conforms to ADR-001 (modular monolith) and ADR-006 (PostgreSQL SKIP LOCKED queue without Redis), eliminating distributed runtime overhead while maintaining clean process-level isolation.

## ADR-026: Frontend SPA Framework (M14-T01)
- **Status:** Accepted
- **Context:** TOOLS.md listed the SPA framework as Conditional. M14 requires Loan Officer and Reviewer screens against `/api/v1` without making the UI the authorization source.
- **Decision:** Use **Vite + React + TypeScript** in `apps/web/`. Dev server proxies `/api` to the FastAPI process. Session cookie authentication with CSRF on mutations (ADR-019). Vitest + Testing Library for component/route tests.
- **Why Chosen:** Matches the architecture’s separate-SPA model, keeps Next.js SSR complexity out of the MVP, and was selected explicitly for M14 implementation.
- **Consequences:** Compose may add an optional `web` service. Role-based route guards are UX only; the API continues to enforce maker-checker RBAC.

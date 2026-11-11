import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { StatusBadge } from "../../components/StatusBadge";
import { useWorkspace } from "../../context/useWorkspace";
import { apiClient } from "../../services/apiClient";
import { calculateFoir } from "../../services/foir";
import type { Application, ApplicationStatus } from "../../types/domain";
import { formatInr } from "../../utils/currency";

function countByStatus(applications: Application[], status: ApplicationStatus): number {
  return applications.filter((app) => app.status === status).length;
}

function creditScoreFoirLabel(app: Application): string {
  const { foirPct } = calculateFoir({
    amount: app.amount,
    monthlyNetIncome: app.income,
    existingMonthlyEmis: 0,
  });
  return `${app.cibil} CIBIL • ${Math.round(foirPct)}% FOIR`;
}

function actionLabel(status: ApplicationStatus): "Review" | "Open" {
  return status === "Submitted" ? "Review" : "Open";
}

export function ReviewerWorkspacePage() {
  const { dataMode, revision } = useWorkspace();
  const [items, setItems] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      setError(null);
      try {
        const response = await apiClient.fetchApplications();
        if (!cancelled) {
          setItems(response);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load applications");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [dataMode, revision]);

  const pendingCount = useMemo(() => countByStatus(items, "Submitted"), [items]);
  const approvedCount = useMemo(() => countByStatus(items, "Approved"), [items]);
  const rejectedCount = useMemo(() => countByStatus(items, "Rejected"), [items]);

  return (
    <div className="page" data-testid="reviewer-workspace">
      <div className="page-header">
        <div>
          <h1>Reviewer workspace</h1>
          <p className="page-subtitle">
            Evaluate submitted applications, verify risk rules &amp; debt ratios, and issue final
            credit decisions.
          </p>
        </div>
        <p className="pending-review-badge" data-testid="pending-review-badge">
          Pending review:{" "}
          <span className="pending-review-count">{pendingCount}</span>
        </p>
      </div>

      <section
        className="summary-card-grid"
        data-testid="reviewer-summary-cards"
        aria-label="Metric summary"
      >
        <article className="soft-card summary-card">
          <p className="summary-card-label">Awaiting Decision</p>
          <p className="summary-card-value">{pendingCount} applications</p>
          <p className="summary-card-note">Submitted by Loan Officers</p>
        </article>
        <article className="soft-card summary-card">
          <p className="summary-card-label">Approved Today</p>
          <p className="summary-card-value summary-card-value--ok">
            {approvedCount} applications
          </p>
          <p className="summary-card-note">Audit hash verified</p>
        </article>
        <article className="soft-card summary-card">
          <p className="summary-card-label">Returned / Rejected</p>
          <p className="summary-card-value">{rejectedCount} applications</p>
          <p className="summary-card-note">Policy threshold check</p>
        </article>
      </section>

      {loading && <p className="muted">Loading underwriting queue…</p>}
      {error && <p className="error-text">{error}</p>}

      {!loading && !error && (
        <div className="soft-card soft-card--flush">
          <table
            className="data-table data-table--hoverable"
            data-testid="reviewer-underwriting-queue"
          >
            <thead>
              <tr>
                <th>Reference</th>
                <th>Applicant</th>
                <th>Amount</th>
                <th>Credit Score / FOIR</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((app) => (
                <tr key={app.ref}>
                  <td>
                    <span className="ref-mono">{app.ref}</span>
                  </td>
                  <td>{app.applicant}</td>
                  <td>{formatInr(app.amount)}</td>
                  <td>{creditScoreFoirLabel(app)}</td>
                  <td>
                    <StatusBadge status={app.status} />
                  </td>
                  <td>
                    <Link
                      className="table-action-link"
                      to={`/applications/${encodeURIComponent(app.ref)}`}
                    >
                      {actionLabel(app.status)}
                    </Link>
                  </td>
                </tr>
              ))}
              {items.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted">
                    No applications in the underwriting queue.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

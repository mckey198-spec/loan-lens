import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { NewApplicationModal } from "../../components/modals/NewApplicationModal";
import { StatusBadge } from "../../components/StatusBadge";
import { useWorkspace } from "../../context/useWorkspace";
import { apiClient } from "../../services/apiClient";
import type { Application, ApplicationStatus } from "../../types/domain";
import { formatInr } from "../../utils/currency";

type StatusFilter = "All" | ApplicationStatus;

const FILTERS: readonly StatusFilter[] = [
  "All",
  "Draft",
  "Submitted",
  "Approved",
  "Rejected",
];

const TOAST_DURATION_MS = 3500;

function countByStatus(applications: Application[], status: StatusFilter): number {
  if (status === "All") {
    return applications.length;
  }
  return applications.filter((app) => app.status === status).length;
}

function matchesSearch(app: Application, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) {
    return true;
  }
  return (
    app.applicant.toLowerCase().includes(q) || app.ref.toLowerCase().includes(q)
  );
}

function canCreateApplication(role: string | undefined): boolean {
  return role === "loan_officer" || role === "admin";
}

export function ApplicationListPage() {
  const { dataMode, revision, currentUser } = useWorkspace();
  const [items, setItems] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("All");
  const [searchQuery, setSearchQuery] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

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

  useEffect(() => {
    if (!toast) {
      return;
    }
    const timer = window.setTimeout(() => setToast(null), TOAST_DURATION_MS);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const filteredItems = useMemo(() => {
    return items.filter((app) => {
      if (statusFilter !== "All" && app.status !== statusFilter) {
        return false;
      }
      return matchesSearch(app, searchQuery);
    });
  }, [items, statusFilter, searchQuery]);

  function handleCreated(application: Application): void {
    setToast(`Created draft ${application.ref}`);
    // Update in place so the success toast survives (Layout remounts Outlet on revision bump).
    setItems((prev) => [application, ...prev.filter((a) => a.ref !== application.ref)]);
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>Loan Officer workspace</h1>
        {canCreateApplication(currentUser?.role) && (
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setModalOpen(true)}
          >
            New application
          </button>
        )}
      </div>

      <section className="soft-card officer-workspace-controls">
        <div className="filter-toolbar">
          <div className="filter-pills" role="group" aria-label="Filter by status">
            {FILTERS.map((filter) => {
              const count = countByStatus(items, filter);
              const active = statusFilter === filter;
              const label = filter === "All" ? `All (${count})` : `${filter} (${count})`;
              return (
                <button
                  key={filter}
                  type="button"
                  className={active ? "filter-pill filter-pill--active" : "filter-pill"}
                  aria-pressed={active}
                  onClick={() => setStatusFilter(filter)}
                >
                  {label}
                </button>
              );
            })}
          </div>
          <label className="officer-search">
            <span className="sr-only">Search applications</span>
            <input
              type="search"
              className="officer-search-input"
              placeholder="Search by name or reference ID…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              data-testid="officer-application-search"
            />
          </label>
        </div>
      </section>

      {loading && <p className="muted">Loading applications…</p>}
      {error && <p className="error-text">{error}</p>}

      {!loading && !error && (
        <div className="soft-card soft-card--flush">
          <table className="data-table data-table--hoverable" data-testid="officer-application-list">
            <thead>
              <tr>
                <th>Reference ID</th>
                <th>Applicant</th>
                <th>Requested amount</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {filteredItems.map((app) => (
                <tr key={app.ref}>
                  <td>
                    <span className="ref-mono">{app.ref}</span>
                  </td>
                  <td>{app.applicant}</td>
                  <td>{formatInr(app.amount)}</td>
                  <td>
                    <StatusBadge status={app.status} />
                  </td>
                  <td>
                    <Link
                      className="btn btn-ghost btn-sm"
                      to={`/applications/${encodeURIComponent(app.ref)}`}
                    >
                      {app.status === "Draft" ? "Open" : "View"}
                    </Link>
                  </td>
                </tr>
              ))}
              {filteredItems.length === 0 && (
                <tr>
                  <td colSpan={5} className="muted">
                    {items.length === 0
                      ? "No applications yet."
                      : "No applications match the current filters."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {toast && (
        <div className="toast toast--success" role="status">
          {toast}
        </div>
      )}

      {modalOpen && (
        <NewApplicationModal
          onClose={() => setModalOpen(false)}
          onCreated={handleCreated}
        />
      )}
    </div>
  );
}

export { ApplicationListPage as OfficerWorkspacePage };

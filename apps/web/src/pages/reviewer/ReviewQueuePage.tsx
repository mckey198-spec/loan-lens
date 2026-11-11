import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { listApplications } from "../../api/applications";
import { claimApplication } from "../../api/assessments";
import type { Application } from "../../api/types";
import { StatusTag } from "../../components/StatusTag";
import { useAuth } from "../../auth/AuthProvider";

export function ReviewQueuePage() {
  const { hasRole } = useAuth();
  const navigate = useNavigate();
  const [items, setItems] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [claimingId, setClaimingId] = useState<string | null>(null);

  const canClaim = hasRole("reviewer", "admin");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      try {
        const [awaiting, inReview] = await Promise.all([
          listApplications({ status: "awaiting_human_review", limit: 100 }),
          listApplications({ status: "in_review", limit: 100 }),
        ]);
        if (!cancelled) {
          setItems([...awaiting.items, ...inReview.items]);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load queue");
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
  }, []);

  async function handleClaim(applicationId: string) {
    if (!canClaim) {
      return;
    }
    setClaimingId(applicationId);
    setError(null);
    try {
      await claimApplication(applicationId);
      navigate(`/reviewer/applications/${applicationId}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Claim failed");
    } finally {
      setClaimingId(null);
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>Review queue</h1>
      </div>
      {loading && <p>Loading queue…</p>}
      {error && <p className="error-text">{error}</p>}
      {!loading && (
        <table className="data-table" data-testid="review-queue">
          <thead>
            <tr>
              <th>Reference</th>
              <th>Applicant</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {items.map((app) => (
              <tr key={app.id}>
                <td>{app.reference_code}</td>
                <td>{app.applicant.full_name}</td>
                <td>
                  <StatusTag status={app.status} />
                </td>
                <td className="action-cell">
                  <Link to={`/reviewer/applications/${app.id}`}>Open</Link>
                  {canClaim && app.status === "awaiting_human_review" && (
                    <button
                      type="button"
                      className="btn btn-primary"
                      data-testid={`claim-btn-${app.id}`}
                      disabled={claimingId === app.id}
                      onClick={() => void handleClaim(app.id)}
                    >
                      {claimingId === app.id ? "Claiming…" : "Claim Application"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td colSpan={4} className="muted">
                  No applications awaiting review.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
      {/* Maker-checker: no New Application / Submit intake controls here */}
    </div>
  );
}

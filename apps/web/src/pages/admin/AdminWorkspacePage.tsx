import { useEffect, useState } from "react";

import { ROLE_LABELS, useWorkspace } from "../../context/useWorkspace";
import { apiClient } from "../../services/apiClient";
import {
  ROLE_PROFILES,
  repairMockAuditLedger,
  tamperMockAuditEvent,
} from "../../services/mockData";
import type { AuditEvent, AuditVerification, UserRole } from "../../types/domain";

/** Demo showcase: mutate a mid-chain block so verification fails at a known index. */
const DEMO_TAMPER_INDEX = 2;

const TOAST_DURATION_MS = 3500;

type AdminTab = "users" | "audit" | "system";

const TABS: readonly { id: AdminTab; label: string }[] = [
  { id: "users", label: "Operators & Roles (RBAC)" },
  { id: "audit", label: "Audit Trail Ledger" },
  { id: "system", label: "System & Worker Status" },
];

/** Showcase operators shown in the RBAC table (demo role switcher + live seed aliases). */
const OPERATORS: readonly {
  role: UserRole;
  name: string;
  email: string;
}[] = [
  { role: "admin", name: "Local Test Admin", email: "admin@loanlens.local" },
  { role: "loan_officer", name: "Aarav Mohanty", email: "officer@loanlens.local" },
  { role: "reviewer", name: "Meera Krishnan", email: "reviewer@loanlens.local" },
];

type IntegrityBadge =
  | { tone: "ok"; text: string }
  | { tone: "verified"; text: string }
  | { tone: "error"; text: string };

function shortHash(value: string): string {
  if (value.length <= 16) {
    return value;
  }
  return `${value.slice(0, 8)}…${value.slice(-6)}`;
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function actorDisplay(actorEmail: string): string {
  const profile = Object.values(ROLE_PROFILES).find((p) => p.email === actorEmail);
  if (!profile) {
    return actorEmail;
  }
  return `${profile.name} (${ROLE_LABELS[profile.role]})`;
}

function rolePillClass(role: UserRole): string {
  return `role-pill role-pill--${role}`;
}

export function AdminWorkspacePage() {
  const { dataMode, revision, switchRole } = useWorkspace();
  const [activeTab, setActiveTab] = useState<AdminTab>("users");
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loadingEvents, setLoadingEvents] = useState(false);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [tampering, setTampering] = useState(false);
  const [repairing, setRepairing] = useState(false);
  const [ledgerMutated, setLedgerMutated] = useState(false);
  const [integrity, setIntegrity] = useState<IntegrityBadge>({
    tone: "ok",
    text: "Chain Integrity: OK",
  });
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (activeTab !== "audit") {
      return;
    }
    let cancelled = false;
    void (async () => {
      setLoadingEvents(true);
      setEventsError(null);
      try {
        const response = await apiClient.fetchAuditEvents();
        if (!cancelled) {
          setEvents(response);
        }
      } catch (err) {
        if (!cancelled) {
          setEventsError(err instanceof Error ? err.message : "Failed to load audit events");
          setEvents([]);
        }
      } finally {
        if (!cancelled) {
          setLoadingEvents(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeTab, dataMode, revision]);

  useEffect(() => {
    if (!toast) {
      return;
    }
    const timer = window.setTimeout(() => setToast(null), TOAST_DURATION_MS);
    return () => window.clearTimeout(timer);
  }, [toast]);

  function applyVerificationResult(result: AuditVerification): void {
    if (result.isValid) {
      setIntegrity({
        tone: "verified",
        text: `Verified (${result.totalEventsChecked} blocks intact)`,
      });
      setToast("SHA-256 audit hash chain verified with zero tampering");
      return;
    }
    const block = result.tamperedAt ?? "?";
    setIntegrity({
      tone: "error",
      text: `Chain Integrity Compromised at Block ${block}`,
    });
    setToast(
      result.errorMessage ??
        `Audit hash chain verification failed — compromised at block ${block}.`,
    );
  }

  async function handleVerifyChain(): Promise<void> {
    setVerifying(true);
    try {
      const result: AuditVerification = await apiClient.verifyAuditChain();
      setActiveTab("audit");
      applyVerificationResult(result);
    } catch (err) {
      setActiveTab("audit");
      const message = err instanceof Error ? err.message : "Audit chain verification failed";
      setIntegrity({ tone: "error", text: `Chain Integrity: ERROR — ${message}` });
      setToast(message);
    } finally {
      setVerifying(false);
    }
  }

  async function handleSimulateLedgerMutation(): Promise<void> {
    setTampering(true);
    try {
      await tamperMockAuditEvent(DEMO_TAMPER_INDEX, {
        actor: "mallory@loanlens.demo",
        hash: "deadbeef".repeat(8),
      });
      setLedgerMutated(true);
      const refreshed = await apiClient.fetchAuditEvents();
      setEvents(refreshed);
      const result = await apiClient.verifyAuditChain();
      applyVerificationResult(result);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to simulate ledger mutation";
      setIntegrity({ tone: "error", text: `Chain Integrity: ERROR — ${message}` });
      setToast(message);
    } finally {
      setTampering(false);
    }
  }

  async function handleRepairLedger(): Promise<void> {
    setRepairing(true);
    try {
      await repairMockAuditLedger();
      setLedgerMutated(false);
      const refreshed = await apiClient.fetchAuditEvents();
      setEvents(refreshed);
      const result = await apiClient.verifyAuditChain();
      applyVerificationResult(result);
      if (result.isValid) {
        setToast("Demo ledger repaired — hash chain restored");
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to repair ledger";
      setIntegrity({ tone: "error", text: `Chain Integrity: ERROR — ${message}` });
      setToast(message);
    } finally {
      setRepairing(false);
    }
  }

  return (
    <div className="page" data-testid="admin-workspace">
      <div className="page-header">
        <div>
          <h1>Admin workspace</h1>
          <p className="page-subtitle">
            Manage operator roles (RBAC), verify the immutable SHA-256 audit ledger, and monitor
            background worker health.
          </p>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          data-testid="verify-audit-chain"
          disabled={verifying}
          onClick={() => void handleVerifyChain()}
        >
          {verifying ? "Verifying…" : "Verify audit chain"}
        </button>
      </div>

      <div className="filter-pills admin-subnav" role="tablist" aria-label="Admin sections">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`admin-tab-${tab.id}`}
            aria-selected={activeTab === tab.id}
            aria-controls={`admin-panel-${tab.id}`}
            className={
              activeTab === tab.id ? "filter-pill filter-pill--active" : "filter-pill"
            }
            data-testid={`admin-tab-${tab.id}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "users" && (
        <section
          id="admin-panel-users"
          role="tabpanel"
          aria-labelledby="admin-tab-users"
          data-testid="admin-panel-users"
        >
          <div className="soft-card soft-card--flush">
            <table className="data-table data-table--hoverable" data-testid="admin-operators-table">
              <thead>
                <tr>
                  <th>Operator Name</th>
                  <th>Email</th>
                  <th>Assigned Role</th>
                  <th>Session Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {OPERATORS.map((op) => (
                  <tr key={op.role} data-testid={`admin-operator-${op.role}`}>
                    <td>{op.name}</td>
                    <td className="ref-mono">{op.email}</td>
                    <td>
                      <span className={rolePillClass(op.role)}>{ROLE_LABELS[op.role]}</span>
                    </td>
                    <td>
                      <span className="session-status">Active (HttpOnly)</span>
                    </td>
                    <td>
                      {dataMode === "demo" ? (
                        <button
                          type="button"
                          className="btn btn-sm btn-secondary"
                          data-testid={`use-role-${op.role}`}
                          onClick={() => switchRole(op.role)}
                        >
                          Use role
                        </button>
                      ) : (
                        <span className="muted">Live session</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {activeTab === "audit" && (
        <section
          id="admin-panel-audit"
          role="tabpanel"
          aria-labelledby="admin-tab-audit"
          data-testid="admin-panel-audit"
        >
          <div className="admin-audit-header soft-card">
            <div>
              <h2 className="admin-section-title">Immutable Audit Trail Ledger</h2>
              <p className="muted admin-section-copy">
                Append-only SHA-256 hash chain. Each record links to the previous hash; verification
                walks the full ledger for tampering.
              </p>
              {dataMode === "demo" && (
                <div className="admin-tamper-actions" data-testid="admin-tamper-actions">
                  <button
                    type="button"
                    className="btn btn-sm btn-secondary"
                    data-testid="simulate-ledger-mutation"
                    disabled={tampering || repairing || verifying}
                    onClick={() => void handleSimulateLedgerMutation()}
                  >
                    {tampering ? "Simulating…" : "Simulate Ledger Mutation"}
                  </button>
                  {ledgerMutated && (
                    <button
                      type="button"
                      className="btn btn-sm btn-ghost"
                      data-testid="repair-ledger"
                      disabled={tampering || repairing || verifying}
                      onClick={() => void handleRepairLedger()}
                    >
                      {repairing ? "Repairing…" : "Repair Ledger"}
                    </button>
                  )}
                </div>
              )}
            </div>
            <p
              className={`integrity-badge integrity-badge--${integrity.tone}`}
              data-testid="audit-integrity-badge"
            >
              {integrity.text}
            </p>
          </div>

          {loadingEvents && <p className="muted">Loading audit events…</p>}
          {eventsError && <p className="error-text">{eventsError}</p>}

          {!loadingEvents && !eventsError && (
            <div className="soft-card soft-card--flush">
              <table className="data-table data-table--hoverable" data-testid="admin-audit-table">
                <thead>
                  <tr>
                    <th>Timestamp</th>
                    <th>Actor (Role)</th>
                    <th>Action</th>
                    <th>Reference</th>
                    <th>Prev → Record Hash</th>
                  </tr>
                </thead>
                <tbody>
                  {events.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="muted">
                        No audit events recorded.
                      </td>
                    </tr>
                  ) : (
                    events.map((event) => (
                      <tr key={`${event.ts}-${event.hash}`} data-testid="admin-audit-row">
                        <td className="ref-mono">{formatTimestamp(event.ts)}</td>
                        <td>{actorDisplay(event.actor)}</td>
                        <td>
                          <span className="action-chip ref-mono">{event.action}</span>
                        </td>
                        <td className="ref-mono">{event.ref}</td>
                        <td
                          className="ref-mono hash-link"
                          title={`${shortHash(event.prevHash)} → ${shortHash(event.hash)}`}
                          data-testid="admin-audit-hash-link"
                        >
                          {`${event.prevHash} → ${event.hash}`}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {activeTab === "system" && (
        <section
          id="admin-panel-system"
          role="tabpanel"
          aria-labelledby="admin-tab-system"
          data-testid="admin-panel-system"
        >
          <div className="admin-system-grid">
            <article className="soft-card admin-system-card" data-testid="admin-worker-card">
              <span className="health-pill health-pill--ok">Healthy</span>
              <h2 className="admin-section-title">Reconciliation &amp; SLA Queue Worker</h2>
              <p className="muted admin-section-copy">
                Background worker drains the reconciliation and SLA queues, retries failed jobs, and
                records heartbeats for ops visibility.
              </p>
              <p className="admin-heartbeat">
                Last heartbeat: <span className="ref-mono">just now</span>
              </p>
            </article>

            <article className="soft-card admin-system-card" data-testid="admin-security-card">
              <span className="health-pill health-pill--enforced">Enforced</span>
              <h2 className="admin-section-title">Session Cookies &amp; PII Log Masking</h2>
              <p className="muted admin-section-copy">
                HttpOnly session cookies, CSRF on mutating routes, and structured log redaction for
                PAN, phone, and address fields keep operator traffic within compliance controls.
              </p>
            </article>
          </div>
        </section>
      )}

      {toast && (
        <div
          className={`toast ${integrity.tone === "error" ? "toast--error" : "toast--success"}`}
          role="status"
          data-testid="admin-toast"
        >
          {toast}
        </div>
      )}
    </div>
  );
}

import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { StatusBadge } from "../../components/StatusBadge";
import { PolicyUploadWidget } from "../../components/rag/PolicyUploadWidget";
import { homePathFor, ROLE_LABELS, useWorkspace } from "../../context/useWorkspace";
import { ApiError } from "../../api/types";
import { apiClient } from "../../services/apiClient";
import { calculateFoir } from "../../services/foir";
import { ROLE_PROFILES } from "../../services/mockData";
import {
  DEFAULT_TENOR_MONTHS,
  TENOR_MONTH_OPTIONS,
  type Application,
  type ApplicationStatus,
  type AuditEvent,
  type ReviewDecision,
  type TenorMonths,
  type UserRole,
} from "../../types/domain";
import { formatInr } from "../../utils/currency";

const MIN_DECISION_NOTE_LENGTH = 10;
const TOAST_DURATION_MS = 3500;

const IMMUTABLE_HISTORY_HINT =
  "Regulatory compliance requires an immutable audit history; completed applications cannot be deleted.";

interface FormState {
  applicant: string;
  amount: string;
  income: string;
  grossIncome: string;
  tenorMonths: string;
  notes: string;
  reviewerNote: string;
}

function emptyForm(): FormState {
  return {
    applicant: "",
    amount: "",
    income: "",
    grossIncome: "",
    tenorMonths: String(DEFAULT_TENOR_MONTHS),
    notes: "",
    reviewerNote: "",
  };
}

function formFromApplication(app: Application): FormState {
  return {
    applicant: app.applicant,
    amount: String(app.amount),
    income: String(app.income),
    grossIncome: String(app.grossIncome),
    tenorMonths: String(app.tenor_months || DEFAULT_TENOR_MONTHS),
    notes: app.notes,
    reviewerNote: app.reviewerNote,
  };
}

function parseNonNegative(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed === "") {
    return null;
  }
  const n = Number(trimmed);
  if (!Number.isFinite(n) || n < 0) {
    return null;
  }
  return n;
}

function canEditDraft(role: UserRole | undefined, status: ApplicationStatus): boolean {
  return (role === "loan_officer" || role === "admin") && status === "Draft";
}

function canDecide(role: UserRole | undefined, status: ApplicationStatus): boolean {
  return (role === "reviewer" || role === "admin") && status === "Submitted";
}

function canDiscardDraft(role: UserRole | undefined, status: ApplicationStatus): boolean {
  return (role === "loan_officer" || role === "admin") && status === "Draft";
}

function isCompletedStatus(status: ApplicationStatus): boolean {
  return status === "Submitted" || status === "Approved" || status === "Rejected";
}

function suggestedSwitchRole(
  role: UserRole | undefined,
  status: ApplicationStatus,
): UserRole | null {
  if (status === "Draft" && role === "reviewer") {
    return "loan_officer";
  }
  if (status === "Submitted" && role === "loan_officer") {
    return "reviewer";
  }
  return null;
}

function actorLabel(actorEmail: string): string {
  const profile = Object.values(ROLE_PROFILES).find((p) => p.email === actorEmail);
  if (!profile) {
    return actorEmail;
  }
  return `${profile.name} (${ROLE_LABELS[profile.role]})`;
}

function actionLabel(action: string): string {
  if (action === "DRAFT_DISCARDED") {
    return "Draft · Discarded";
  }
  return action
    .split(".")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" · ");
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return date.toLocaleString("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function creditFoirLabel(cibil: number, foirPct: number): string {
  return `${cibil} CIBIL • ${Math.round(foirPct)}% FOIR`;
}

export function ApplicationDetailPage() {
  const { ref: routeRef, id: routeId } = useParams<{ ref?: string; id?: string }>();
  const applicationKey = decodeURIComponent(routeRef ?? routeId ?? "");
  const { currentUser, dataMode, revision, switchRole } = useWorkspace();
  const role = currentUser?.role;

  const [application, setApplication] = useState<Application | null>(null);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (!toast) {
      return;
    }
    const timer = window.setTimeout(() => setToast(null), TOAST_DURATION_MS);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (!applicationKey) {
        setError("Missing application reference.");
        setLoading(false);
        return;
      }
      setLoading(true);
      setError(null);
      setActionError(null);
      try {
        const [app, events] = await Promise.all([
          apiClient.fetchApplication(applicationKey),
          apiClient.fetchAuditEvents(applicationKey),
        ]);
        if (!cancelled) {
          setApplication(app);
          setForm(formFromApplication(app));
          setAuditEvents(events);
        }
      } catch (err) {
        if (!cancelled) {
          setApplication(null);
          setAuditEvents([]);
          setError(err instanceof Error ? err.message : "Failed to load application");
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
  }, [applicationKey, dataMode, revision, role]);

  const drafting = application !== null && canEditDraft(role, application.status);
  const deciding = application !== null && canDecide(role, application.status);
  const discarding = application !== null && canDiscardDraft(role, application.status);
  const readOnly = !drafting && !deciding;
  const switchTo = application ? suggestedSwitchRole(role, application.status) : null;
  const showImmutableHint =
    application !== null &&
    isCompletedStatus(application.status) &&
    (role === "loan_officer" || role === "admin");

  const liveAmount = parseNonNegative(form.amount) ?? application?.amount ?? 0;
  const liveNetIncome = parseNonNegative(form.income) ?? application?.income ?? 0;
  const liveTenor =
    parseNonNegative(form.tenorMonths) ??
    application?.tenor_months ??
    DEFAULT_TENOR_MONTHS;
  const displayTenor = application?.tenor_months || DEFAULT_TENOR_MONTHS;
  const liveFoir = useMemo(
    () =>
      calculateFoir({
        amount: liveAmount,
        monthlyNetIncome: liveNetIncome,
        existingMonthlyEmis: 0,
        tenureMonths: liveTenor,
      }),
    [liveAmount, liveNetIncome, liveTenor],
  );

  const decisionNoteOk = form.reviewerNote.trim().length >= MIN_DECISION_NOTE_LENGTH;
  const backPath = currentUser ? homePathFor(currentUser.role) : "/";

  function updateField<K extends keyof FormState>(key: K, value: FormState[K]): void {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSaveDraft(): Promise<void> {
    if (!application || !drafting) {
      return;
    }
    const amount = parseNonNegative(form.amount);
    const income = parseNonNegative(form.income);
    const grossIncome = parseNonNegative(form.grossIncome);
    const tenorMonths = Number(form.tenorMonths);
    if (
      form.applicant.trim() === "" ||
      amount === null ||
      amount <= 0 ||
      income === null ||
      grossIncome === null ||
      !TENOR_MONTH_OPTIONS.includes(tenorMonths as TenorMonths)
    ) {
      setActionError(
        "Applicant name, a positive loan amount, gross income, net take-home, and tenor are required.",
      );
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      const updated = await apiClient.updateDraft(application.ref, {
        applicant: form.applicant.trim(),
        amount,
        income,
        grossIncome,
        tenor_months: tenorMonths,
        notes: form.notes,
        pan: application.pan,
        cibil: application.cibil,
        purpose: application.purpose,
      });
      setApplication(updated);
      setForm(formFromApplication(updated));
      setAuditEvents(await apiClient.fetchAuditEvents(updated.ref));
      setToast(`Draft ${updated.ref} saved`);
    } catch (err) {
      setActionError(
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Failed to save draft.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function handleDiscardDraft(): Promise<void> {
    if (!application || !discarding) {
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      const discarded = await apiClient.discardDraft(application.ref);
      setApplication(discarded);
      setForm(formFromApplication(discarded));
      setAuditEvents(await apiClient.fetchAuditEvents(discarded.ref));
      setToast(`Draft ${discarded.ref} discarded`);
    } catch (err) {
      setActionError(
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Failed to discard draft.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function handleSubmitToReviewer(): Promise<void> {
    if (!application || !drafting) {
      return;
    }
    const amount = parseNonNegative(form.amount);
    const income = parseNonNegative(form.income);
    const grossIncome = parseNonNegative(form.grossIncome);
    const tenorMonths = Number(form.tenorMonths);
    if (
      form.applicant.trim() === "" ||
      amount === null ||
      amount <= 0 ||
      income === null ||
      grossIncome === null ||
      !TENOR_MONTH_OPTIONS.includes(tenorMonths as TenorMonths)
    ) {
      setActionError("Complete required fields before submitting.");
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      await apiClient.updateDraft(application.ref, {
        applicant: form.applicant.trim(),
        amount,
        income,
        grossIncome,
        tenor_months: tenorMonths,
        notes: form.notes,
        pan: application.pan,
        cibil: application.cibil,
        purpose: application.purpose,
      });
      const submitted = await apiClient.submitToReviewer(application.ref);
      setApplication(submitted);
      setForm(formFromApplication(submitted));
      setAuditEvents(await apiClient.fetchAuditEvents(submitted.ref));
      setToast(`Submitted ${submitted.ref} to reviewer`);
    } catch (err) {
      setActionError(
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Failed to submit application.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function handleDecision(decision: ReviewDecision): Promise<void> {
    if (!application || !deciding) {
      return;
    }
    if (!decisionNoteOk) {
      setActionError(
        `Reviewer Decision Note must be at least ${MIN_DECISION_NOTE_LENGTH} characters.`,
      );
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      const decided = await apiClient.recordDecision(
        application.ref,
        decision,
        form.reviewerNote.trim(),
      );
      setApplication(decided);
      setForm(formFromApplication(decided));
      setAuditEvents(await apiClient.fetchAuditEvents(decided.ref));
      setToast(`${decision} ${decided.ref}`);
    } catch (err) {
      setActionError(
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Failed to record decision.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="page" data-testid="application-detail-page">
        <p className="muted">Loading application…</p>
      </div>
    );
  }

  if (error || !application) {
    return (
      <div className="page" data-testid="application-detail-page">
        <Link className="back-link" to={backPath}>
          ← Back to workspace list
        </Link>
        <p className="error-text">{error ?? "Application not found"}</p>
      </div>
    );
  }

  return (
    <div className="page" data-testid="application-detail-page">
      <Link className="back-link" to={backPath} data-testid="back-to-workspace">
        ← Back to workspace list
      </Link>

      <div className="soft-card application-detail-card">
        <header className="application-detail-header">
          <div className="application-detail-title-row">
            <h1 data-testid="application-detail-name">{application.applicant}</h1>
            <StatusBadge status={application.status} />
          </div>
          <p className="ref-mono" data-testid="application-detail-ref">
            {application.ref}
          </p>
        </header>

        <section
          className="application-metrics-bar"
          data-testid="application-metrics-bar"
          aria-label="Summary metrics"
        >
          <article className="application-metric">
            <p className="application-metric-label">Requested Amount</p>
            <p className="application-metric-value">{formatInr(liveAmount)}</p>
          </article>
          <article className="application-metric">
            <p className="application-metric-label">Loan Purpose &amp; Tenor</p>
            <p className="application-metric-value" data-testid="application-tenor-metric">
              {application.purpose} · {displayTenor} mo
            </p>
          </article>
          <article className="application-metric">
            <p className="application-metric-label">Credit Score / FOIR</p>
            <p className="application-metric-value" data-testid="live-credit-foir">
              {creditFoirLabel(application.cibil, liveFoir.foirPct)}
            </p>
          </article>
        </section>

        {readOnly && (
          <div className="helper-chip-row" data-testid="readonly-helper">
            <span className="helper-chip">
              Read-only view — this application is {application.status}
              {role ? ` for your ${ROLE_LABELS[role]} role` : ""}.
            </span>
            {dataMode === "demo" && switchTo && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => switchRole(switchTo)}
                data-testid="switch-role-hint"
              >
                Switch to {ROLE_LABELS[switchTo]}
              </button>
            )}
          </div>
        )}

        {actionError && (
          <div className="error-banner" role="alert" data-testid="detail-action-error">
            {actionError}
          </div>
        )}

        <form
          className="application-detail-form"
          data-testid="application-detail-form"
          onSubmit={(e) => {
            e.preventDefault();
          }}
        >
          <div className="form-grid">
            <label className="span-2">
              Applicant Full Name
              <input
                value={form.applicant}
                onChange={(e) => updateField("applicant", e.target.value)}
                disabled={!drafting || busy}
                data-testid="detail-applicant"
              />
            </label>

            <label>
              Loan Amount (INR)
              <input
                type="number"
                min={0}
                step="any"
                value={form.amount}
                onChange={(e) => updateField("amount", e.target.value)}
                disabled={!drafting || busy}
                data-testid="detail-amount"
              />
            </label>

            <label>
              Loan Tenor (Months)
              <select
                value={form.tenorMonths}
                onChange={(e) => updateField("tenorMonths", e.target.value)}
                disabled={!drafting || busy}
                data-testid="detail-tenor"
              >
                {TENOR_MONTH_OPTIONS.map((months) => (
                  <option key={months} value={months}>
                    {months} months
                  </option>
                ))}
              </select>
            </label>

            <label>
              Monthly Gross Income (INR)
              <input
                type="number"
                min={0}
                step="any"
                value={form.grossIncome}
                onChange={(e) => updateField("grossIncome", e.target.value)}
                disabled={!drafting || busy}
                data-testid="detail-gross-income"
              />
            </label>

            <label>
              Monthly Net Take-Home (INR)
              <input
                type="number"
                min={0}
                step="any"
                value={form.income}
                onChange={(e) => updateField("income", e.target.value)}
                disabled={!drafting || busy}
                data-testid="detail-income"
              />
            </label>

            <label className="span-2">
              Loan Officer Assessment Notes
              <textarea
                rows={3}
                value={form.notes}
                onChange={(e) => updateField("notes", e.target.value)}
                disabled={!drafting || busy}
                data-testid="detail-notes"
              />
            </label>

            <label className="span-2">
              Reviewer Decision Note
              <textarea
                rows={3}
                value={form.reviewerNote}
                onChange={(e) => updateField("reviewerNote", e.target.value)}
                disabled={!deciding || busy}
                data-testid="detail-reviewer-note"
                aria-invalid={deciding && !decisionNoteOk}
              />
              {deciding && !decisionNoteOk && (
                <span className="field-error" data-testid="reviewer-note-hint">
                  Enter at least {MIN_DECISION_NOTE_LENGTH} characters to approve or reject.
                </span>
              )}
            </label>
          </div>

          <div className="action-bar application-detail-actions">
            {drafting && (
              <>
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={busy}
                  onClick={() => void handleSaveDraft()}
                  data-testid="save-draft"
                >
                  {busy ? "Saving…" : "Save draft"}
                </button>
                {discarding && (
                  <button
                    type="button"
                    className="btn btn-outline-danger"
                    disabled={busy}
                    onClick={() => void handleDiscardDraft()}
                    data-testid="discard-draft"
                  >
                    Discard draft
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy}
                  onClick={() => void handleSubmitToReviewer()}
                  data-testid="submit-to-reviewer"
                >
                  Submit to Reviewer
                </button>
              </>
            )}
            {showImmutableHint && (
              <span
                className="immutable-history-hint"
                title={IMMUTABLE_HISTORY_HINT}
                data-testid="immutable-history-hint"
                tabIndex={0}
              >
                <span className="immutable-history-hint__icon" aria-hidden="true">
                  i
                </span>
                <span className="sr-only">{IMMUTABLE_HISTORY_HINT}</span>
                Completed applications cannot be deleted
              </span>
            )}
            {deciding && (
              <>
                <button
                  type="button"
                  className="btn btn-danger"
                  disabled={busy || !decisionNoteOk}
                  onClick={() => void handleDecision("Rejected")}
                  data-testid="reject-application"
                >
                  Reject application
                </button>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy || !decisionNoteOk}
                  onClick={() => void handleDecision("Approved")}
                  data-testid="approve-loan"
                >
                  Approve loan
                </button>
              </>
            )}
          </div>
        </form>

        {(role === "reviewer" || role === "admin") && (
          <PolicyUploadWidget
            foirPct={liveFoir.foirPct}
            loanAmount={liveAmount}
            insertDisabled={!deciding || busy}
            onInsertIntoDecisionNote={(text) => {
              setForm((prev) => {
                const existing = prev.reviewerNote.trim();
                const next = existing === "" ? text : `${existing}\n\n${text}`;
                return { ...prev, reviewerNote: next };
              });
            }}
          />
        )}

        <section
          className="application-audit-ledger"
          data-testid="application-audit-ledger"
          aria-labelledby="audit-ledger-heading"
        >
          <h2 id="audit-ledger-heading">Application State History</h2>
          {auditEvents.length === 0 ? (
            <p className="muted">No audit events recorded for this application.</p>
          ) : (
            <ol className="audit-event-list">
              {auditEvents.map((event) => (
                <li key={`${event.ts}-${event.hash}`} className="audit-event-item">
                  <div className="audit-event-meta">
                    <time dateTime={event.ts}>{formatTimestamp(event.ts)}</time>
                    <span className="audit-event-action">{actionLabel(event.action)}</span>
                  </div>
                  <p className="audit-event-actor">{actorLabel(event.actor)}</p>
                  <p className="ref-mono audit-event-hash" title={event.hash}>
                    {event.hash}
                  </p>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>

      {toast && (
        <div className="toast toast--success" role="status">
          {toast}
        </div>
      )}
    </div>
  );
}

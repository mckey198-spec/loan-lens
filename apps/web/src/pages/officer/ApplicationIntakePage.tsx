import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  createApplication,
  getApplication,
  submitApplication,
  updateApplication,
} from "../../api/applications";
import { listDocuments } from "../../api/documents";
import type {
  ApplicantType,
  Application,
  CreateApplicationPayload,
  DocumentMeta,
} from "../../api/types";
import { DocumentUpload } from "../../components/DocumentUpload";
import { StatusTag } from "../../components/StatusTag";
import { useAuth } from "../../auth/AuthProvider";

interface IntakeFormState {
  applicant_type: ApplicantType;
  requested_amount: string;
  tenure_months: string;
  loan_purpose: string;
  full_name: string;
  date_of_birth: string;
  pan: string;
  current_address: string;
  employer_or_business_name: string;
  time_in_job_or_business_months: string;
  gross_monthly_income: string;
  net_monthly_income: string;
  total_existing_monthly_emis: string;
}

const EMPTY_FORM: IntakeFormState = {
  applicant_type: "salaried",
  requested_amount: "",
  tenure_months: "36",
  loan_purpose: "personal",
  full_name: "",
  date_of_birth: "",
  pan: "",
  current_address: "",
  employer_or_business_name: "",
  time_in_job_or_business_months: "12",
  gross_monthly_income: "",
  net_monthly_income: "",
  total_existing_monthly_emis: "0",
};

function applicationToForm(app: Application): IntakeFormState {
  return {
    applicant_type: app.applicant_type,
    requested_amount: app.requested_amount,
    tenure_months: String(app.tenure_months),
    loan_purpose: app.loan_purpose,
    full_name: app.applicant.full_name,
    date_of_birth: app.applicant.date_of_birth,
    pan: app.applicant.pan,
    current_address: app.applicant.current_address,
    employer_or_business_name: app.applicant.employer_or_business_name,
    time_in_job_or_business_months: String(
      app.applicant.time_in_job_or_business_months,
    ),
    gross_monthly_income: app.applicant.gross_monthly_income,
    net_monthly_income: app.applicant.net_monthly_income,
    total_existing_monthly_emis: app.applicant.total_existing_monthly_emis,
  };
}

function toPayload(form: IntakeFormState): CreateApplicationPayload {
  return {
    applicant_type: form.applicant_type,
    requested_amount: form.requested_amount,
    tenure_months: Number(form.tenure_months),
    loan_purpose: form.loan_purpose,
    applicant: {
      full_name: form.full_name,
      date_of_birth: form.date_of_birth,
      pan: form.pan.trim().toUpperCase(),
      current_address: form.current_address,
      employer_or_business_name: form.employer_or_business_name,
      time_in_job_or_business_months: Number(
        form.time_in_job_or_business_months,
      ),
      gross_monthly_income: form.gross_monthly_income,
      net_monthly_income: form.net_monthly_income,
      total_existing_monthly_emis: form.total_existing_monthly_emis,
    },
  };
}

function requiredFieldsComplete(form: IntakeFormState): boolean {
  return (
    form.full_name.trim().length > 0 &&
    form.date_of_birth.trim().length > 0 &&
    /^[A-Z]{5}[0-9]{4}[A-Z]$/i.test(form.pan.trim()) &&
    form.current_address.trim().length > 0 &&
    form.employer_or_business_name.trim().length > 0 &&
    form.requested_amount.trim().length > 0 &&
    Number(form.requested_amount) > 0 &&
    form.gross_monthly_income.trim().length > 0 &&
    form.net_monthly_income.trim().length > 0 &&
    Number(form.tenure_months) > 0
  );
}

export function ApplicationIntakePage() {
  const { applicationId } = useParams();
  const isNew = !applicationId || applicationId === "new";
  const navigate = useNavigate();
  const { hasRole } = useAuth();

  const [form, setForm] = useState<IntakeFormState>(EMPTY_FORM);
  const [application, setApplication] = useState<Application | null>(null);
  const [documents, setDocuments] = useState<DocumentMeta[]>([]);
  const [loading, setLoading] = useState(!isNew);
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const canInitiate = hasRole("loan_officer", "admin");
  const isDraft = application === null || application.status === "draft";
  const readOnly = !isDraft || !canInitiate;

  useEffect(() => {
    if (isNew) {
      return;
    }
    let cancelled = false;
    void (async () => {
      setLoading(true);
      try {
        const [app, docs] = await Promise.all([
          getApplication(applicationId!),
          listDocuments(applicationId!),
        ]);
        if (!cancelled) {
          setApplication(app);
          setForm(applicationToForm(app));
          setDocuments(docs);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load");
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
  }, [applicationId, isNew]);

  const canSubmit = useMemo(() => {
    return (
      canInitiate &&
      isDraft &&
      requiredFieldsComplete(form) &&
      documents.length > 0 &&
      !submitting &&
      !saving
    );
  }, [canInitiate, isDraft, form, documents.length, submitting, saving]);

  function updateField<K extends keyof IntakeFormState>(
    key: K,
    value: IntakeFormState[K],
  ) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function saveDraft(event?: FormEvent) {
    event?.preventDefault();
    if (!canInitiate) {
      return;
    }
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const payload = toPayload(form);
      if (isNew) {
        const created = await createApplication(payload);
        setMessage("Draft created.");
        navigate(`/officer/applications/${created.id}`, { replace: true });
      } else {
        const updated = await updateApplication(applicationId!, payload);
        setApplication(updated);
        setMessage("Draft saved.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleSubmit() {
    if (!applicationId || isNew || !canSubmit) {
      return;
    }
    setSubmitting(true);
    setError(null);
    setMessage(null);
    try {
      if (isDraft) {
        await updateApplication(applicationId, toPayload(form));
      }
      const submitted = await submitApplication(applicationId);
      setApplication(submitted);
      setMessage("Application submitted for processing.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Submit failed");
    } finally {
      setSubmitting(false);
    }
  }

  if (!canInitiate && isNew) {
    return (
      <div className="page">
        <p className="error-text">
          Reviewers cannot initiate or submit loan applications.
        </p>
        <Link to="/reviewer/queue">Back to review queue</Link>
      </div>
    );
  }

  if (loading) {
    return <div className="page">Loading application…</div>;
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>{isNew ? "New application" : "Application intake"}</h1>
          {application && (
            <p className="muted">
              {application.reference_code}{" "}
              <StatusTag status={application.status} />
            </p>
          )}
        </div>
        <Link to="/officer/applications">Back to list</Link>
      </div>

      <form
        className="intake-form"
        data-testid="officer-intake-form"
        onSubmit={(e) => void saveDraft(e)}
      >
        <section className="panel">
          <h3>Applicant</h3>
          <div className="form-grid">
            <label>
              Full name
              <input
                value={form.full_name}
                disabled={readOnly}
                onChange={(e) => updateField("full_name", e.target.value)}
                required
              />
            </label>
            <label>
              PAN
              <input
                value={form.pan}
                disabled={readOnly}
                onChange={(e) => updateField("pan", e.target.value.toUpperCase())}
                maxLength={10}
                required
              />
            </label>
            <label>
              Date of birth
              <input
                type="date"
                value={form.date_of_birth}
                disabled={readOnly}
                onChange={(e) => updateField("date_of_birth", e.target.value)}
                required
              />
            </label>
            <label>
              Net monthly income
              <input
                value={form.net_monthly_income}
                disabled={readOnly}
                onChange={(e) =>
                  updateField("net_monthly_income", e.target.value)
                }
                required
              />
            </label>
            <label>
              Gross monthly income
              <input
                value={form.gross_monthly_income}
                disabled={readOnly}
                onChange={(e) =>
                  updateField("gross_monthly_income", e.target.value)
                }
                required
              />
            </label>
            <label>
              Existing monthly EMIs
              <input
                value={form.total_existing_monthly_emis}
                disabled={readOnly}
                onChange={(e) =>
                  updateField("total_existing_monthly_emis", e.target.value)
                }
              />
            </label>
            <label className="span-2">
              Current address
              <textarea
                value={form.current_address}
                disabled={readOnly}
                onChange={(e) => updateField("current_address", e.target.value)}
                required
              />
            </label>
            <label>
              Employer / business
              <input
                value={form.employer_or_business_name}
                disabled={readOnly}
                onChange={(e) =>
                  updateField("employer_or_business_name", e.target.value)
                }
                required
              />
            </label>
            <label>
              Time in job (months)
              <input
                type="number"
                min={0}
                value={form.time_in_job_or_business_months}
                disabled={readOnly}
                onChange={(e) =>
                  updateField(
                    "time_in_job_or_business_months",
                    e.target.value,
                  )
                }
              />
            </label>
          </div>
        </section>

        <section className="panel">
          <h3>Loan request</h3>
          <div className="form-grid">
            <label>
              Applicant type
              <select
                value={form.applicant_type}
                disabled={readOnly}
                onChange={(e) =>
                  updateField(
                    "applicant_type",
                    e.target.value as ApplicantType,
                  )
                }
              >
                <option value="salaried">Salaried</option>
                <option value="self_employed">Self employed</option>
              </select>
            </label>
            <label>
              Requested amount
              <input
                value={form.requested_amount}
                disabled={readOnly}
                onChange={(e) =>
                  updateField("requested_amount", e.target.value)
                }
                required
              />
            </label>
            <label>
              Tenure (months)
              <input
                type="number"
                min={1}
                value={form.tenure_months}
                disabled={readOnly}
                onChange={(e) => updateField("tenure_months", e.target.value)}
              />
            </label>
            <label>
              Loan purpose
              <input
                value={form.loan_purpose}
                disabled={readOnly}
                onChange={(e) => updateField("loan_purpose", e.target.value)}
              />
            </label>
          </div>
        </section>

        {!isNew && applicationId && (
          <DocumentUpload
            applicationId={applicationId}
            documents={documents}
            disabled={readOnly}
            onUploaded={(doc) => setDocuments((prev) => [...prev, doc])}
          />
        )}

        {error && <p className="error-text">{error}</p>}
        {message && <p className="success-text">{message}</p>}

        {canInitiate && isDraft && (
          <div className="action-bar">
            <button
              type="submit"
              className="btn btn-secondary"
              disabled={saving}
            >
              {saving ? "Saving…" : isNew ? "Create draft" : "Save draft"}
            </button>
            {!isNew && (
              <button
                type="button"
                className="btn btn-primary"
                data-testid="submit-application-btn"
                disabled={!canSubmit}
                title={
                  !canSubmit
                    ? "Complete required fields and upload at least one document"
                    : undefined
                }
                onClick={() => void handleSubmit()}
              >
                {submitting ? "Submitting…" : "Submit application"}
              </button>
            )}
          </div>
        )}

        {/* Maker-checker: claim/decide actions are never rendered for officers */}
      </form>
    </div>
  );
}

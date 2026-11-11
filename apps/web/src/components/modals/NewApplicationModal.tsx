import { useMemo, useState, type FormEvent } from "react";

import { ApiError, type ApplicantType } from "../../api/types";
import { apiClient, type IntakeDetails } from "../../services/apiClient";
import {
  DEFAULT_TENOR_MONTHS,
  isValidPan,
  TENOR_MONTH_OPTIONS,
  toPan,
  type Application,
  type ApplicationDraftInput,
  type TenorMonths,
} from "../../types/domain";
import { ModalShell } from "./ModalShell";

const DEFAULT_CIBIL = 750;
const DEFAULT_TIME_IN_JOB_MONTHS = 12;
const DEFAULT_DATE_OF_BIRTH = "1990-01-01";
const DEFAULT_ADDRESS = "To be confirmed";
const DEFAULT_EMPLOYER = "To be confirmed";

interface FormState {
  applicant: string;
  pan: string;
  amount: string;
  income: string;
  grossIncome: string;
  tenorMonths: string;
  purpose: string;
  dateOfBirth: string;
  currentAddress: string;
  employer: string;
  timeInJobMonths: string;
  existingEmis: string;
  applicantType: ApplicantType;
}

const EMPTY_FORM: FormState = {
  applicant: "",
  pan: "",
  amount: "",
  income: "",
  grossIncome: "",
  tenorMonths: String(DEFAULT_TENOR_MONTHS),
  purpose: "",
  dateOfBirth: DEFAULT_DATE_OF_BIRTH,
  currentAddress: DEFAULT_ADDRESS,
  employer: DEFAULT_EMPLOYER,
  timeInJobMonths: String(DEFAULT_TIME_IN_JOB_MONTHS),
  existingEmis: "0",
  applicantType: "salaried",
};

interface NewApplicationModalProps {
  onClose: () => void;
  onCreated: (application: Application) => void;
}

function parsePositiveNumber(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed === "") {
    return null;
  }
  const n = Number(trimmed);
  if (!Number.isFinite(n) || n <= 0) {
    return null;
  }
  return n;
}

function parseNonNegativeNumber(value: string): number | null {
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

export function NewApplicationModal({ onClose, onCreated }: NewApplicationModalProps) {
  const dataMode = apiClient.getDataMode();
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [panTouched, setPanTouched] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [showLiveDetails, setShowLiveDetails] = useState(false);

  const panError = useMemo(() => {
    const pan = form.pan.trim();
    if (!panTouched && pan.length === 0) {
      return null;
    }
    if (pan.length === 0) {
      return "PAN is required.";
    }
    if (!isValidPan(pan)) {
      return "PAN must match ABCDE1234F.";
    }
    return null;
  }, [form.pan, panTouched]);

  const panValid = isValidPan(form.pan.trim());

  function updateField<K extends keyof FormState>(key: K, value: FormState[K]): void {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function buildDraft(): ApplicationDraftInput | null {
    const amount = parsePositiveNumber(form.amount);
    const income = parsePositiveNumber(form.income);
    const grossIncome = parsePositiveNumber(form.grossIncome);
    const tenorMonths = Number(form.tenorMonths);
    if (
      form.applicant.trim() === "" ||
      !isValidPan(form.pan.trim()) ||
      amount === null ||
      income === null ||
      grossIncome === null ||
      !TENOR_MONTH_OPTIONS.includes(tenorMonths as TenorMonths) ||
      form.purpose.trim() === ""
    ) {
      return null;
    }
    return {
      applicant: form.applicant.trim(),
      pan: toPan(form.pan),
      amount,
      income,
      grossIncome,
      tenor_months: tenorMonths,
      cibil: DEFAULT_CIBIL,
      purpose: form.purpose.trim(),
      notes: "",
    };
  }

  function buildIntake(draft: ApplicationDraftInput): IntakeDetails | null {
    const timeInJob = parseNonNegativeNumber(form.timeInJobMonths);
    const existingEmis = parseNonNegativeNumber(form.existingEmis);
    if (
      form.dateOfBirth.trim() === "" ||
      form.currentAddress.trim() === "" ||
      form.employer.trim() === "" ||
      timeInJob === null ||
      existingEmis === null
    ) {
      return null;
    }
    return {
      totalExistingMonthlyEmis: existingEmis,
      applicantType: form.applicantType,
      tenureMonths: draft.tenor_months,
      dateOfBirth: form.dateOfBirth.trim(),
      currentAddress: form.currentAddress.trim(),
      employerOrBusinessName: form.employer.trim(),
      timeInJobOrBusinessMonths: Math.trunc(timeInJob),
      grossMonthlyIncome: draft.grossIncome,
    };
  }

  async function handleSubmit(event: FormEvent): Promise<void> {
    event.preventDefault();
    setPanTouched(true);
    setApiError(null);

    const draft = buildDraft();
    if (!draft) {
      setApiError("Complete all required fields with valid values before creating a draft.");
      return;
    }

    let intake: IntakeDetails | undefined;
    if (dataMode === "live") {
      const built = buildIntake(draft);
      if (!built) {
        setShowLiveDetails(true);
        setApiError("Live mode requires the additional intake details below.");
        return;
      }
      intake = built;
    }

    setSubmitting(true);
    try {
      const created = await apiClient.createApplication(draft, intake);
      onCreated(created);
      onClose();
    } catch (err) {
      if (err instanceof ApiError) {
        setApiError(err.message);
      } else {
        setApiError(err instanceof Error ? err.message : "Failed to create application.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <ModalShell
      title="New application"
      subtitle="Create a draft loan application. Required documents can be attached after the draft is saved."
      wide
      onClose={submitting ? () => undefined : onClose}
    >
      <form
        className="new-application-form"
        data-testid="new-application-form"
        onSubmit={(e) => void handleSubmit(e)}
      >
        {apiError && (
          <div className="error-banner" role="alert">
            {apiError}
          </div>
        )}

        <div className="form-grid">
          <label className="span-2">
            Applicant Full Legal Name
            <input
              value={form.applicant}
              onChange={(e) => updateField("applicant", e.target.value)}
              autoComplete="name"
              required
              disabled={submitting}
              data-testid="new-app-applicant"
            />
          </label>

          <label>
            PAN Identification
            <div className="pan-field">
              <input
                className={`font-mono uppercase tracking-wider${panValid ? " pan-input--valid" : ""}`}
                value={form.pan}
                onChange={(e) => {
                  updateField("pan", e.target.value.toUpperCase());
                  setPanTouched(true);
                }}
                onBlur={() => setPanTouched(true)}
                maxLength={10}
                autoComplete="off"
                spellCheck={false}
                required
                disabled={submitting}
                aria-invalid={panError !== null}
                aria-describedby={
                  panError ? "new-app-pan-error" : panValid ? "new-app-pan-valid" : undefined
                }
                data-testid="new-app-pan"
              />
              {panValid && (
                <span
                  id="new-app-pan-valid"
                  className="field-valid-chip"
                  data-testid="new-app-pan-valid"
                >
                  Valid PAN
                </span>
              )}
            </div>
            {panError && (
              <span id="new-app-pan-error" className="field-error" data-testid="new-app-pan-error">
                {panError}
              </span>
            )}
          </label>

          <label>
            Requested Amount (INR)
            <div className="currency-input">
              <span className="currency-input__prefix" aria-hidden="true">
                ₹
              </span>
              <input
                type="number"
                min={1}
                step="any"
                value={form.amount}
                onChange={(e) => updateField("amount", e.target.value)}
                required
                disabled={submitting}
                data-testid="new-app-amount"
              />
            </div>
          </label>

          <label>
            Monthly Gross Income (INR)
            <div className="currency-input">
              <span className="currency-input__prefix" aria-hidden="true">
                ₹
              </span>
              <input
                type="number"
                min={1}
                step="any"
                value={form.grossIncome}
                onChange={(e) => updateField("grossIncome", e.target.value)}
                required
                disabled={submitting}
                data-testid="new-app-gross-income"
              />
            </div>
          </label>

          <label>
            Monthly Net Take-Home (INR)
            <div className="currency-input">
              <span className="currency-input__prefix" aria-hidden="true">
                ₹
              </span>
              <input
                type="number"
                min={1}
                step="any"
                value={form.income}
                onChange={(e) => updateField("income", e.target.value)}
                required
                disabled={submitting}
                data-testid="new-app-income"
              />
            </div>
          </label>

          <label>
            Loan Tenor (Months)
            <select
              value={form.tenorMonths}
              onChange={(e) => updateField("tenorMonths", e.target.value)}
              required
              disabled={submitting}
              data-testid="new-app-tenor"
            >
              {TENOR_MONTH_OPTIONS.map((months) => (
                <option key={months} value={months}>
                  {months} months
                </option>
              ))}
            </select>
          </label>

          <label className="span-2">
            Loan Purpose
            <input
              value={form.purpose}
              onChange={(e) => updateField("purpose", e.target.value)}
              placeholder="Micro-Enterprise Working Capital"
              required
              disabled={submitting}
              data-testid="new-app-purpose"
            />
          </label>
        </div>

        {dataMode === "live" && (
          <details
            className="live-intake-details"
            open={showLiveDetails}
            onToggle={(e) => setShowLiveDetails((e.target as HTMLDetailsElement).open)}
          >
            <summary>Additional intake details (Live API)</summary>
            <p className="muted live-intake-hint">
              These fields are required by the live backend. Defaults are pre-filled so a draft can
              be created; expand to adjust before submitting.
            </p>
            <div className="form-grid">
              <label>
                Date of birth
                <input
                  type="date"
                  value={form.dateOfBirth}
                  onChange={(e) => updateField("dateOfBirth", e.target.value)}
                  required
                  disabled={submitting}
                  data-testid="new-app-dob"
                />
              </label>
              <label>
                Applicant type
                <select
                  value={form.applicantType}
                  onChange={(e) =>
                    updateField("applicantType", e.target.value as ApplicantType)
                  }
                  disabled={submitting}
                >
                  <option value="salaried">Salaried</option>
                  <option value="self_employed">Self employed</option>
                </select>
              </label>
              <label className="span-2">
                Current address
                <textarea
                  value={form.currentAddress}
                  onChange={(e) => updateField("currentAddress", e.target.value)}
                  required
                  disabled={submitting}
                  rows={2}
                />
              </label>
              <label>
                Employer / business
                <input
                  value={form.employer}
                  onChange={(e) => updateField("employer", e.target.value)}
                  required
                  disabled={submitting}
                />
              </label>
              <label>
                Time in job (months)
                <input
                  type="number"
                  min={0}
                  value={form.timeInJobMonths}
                  onChange={(e) => updateField("timeInJobMonths", e.target.value)}
                  required
                  disabled={submitting}
                />
              </label>
              <label>
                Existing monthly EMIs (INR)
                <input
                  type="number"
                  min={0}
                  step="any"
                  value={form.existingEmis}
                  onChange={(e) => updateField("existingEmis", e.target.value)}
                  required
                  disabled={submitting}
                />
              </label>
            </div>
          </details>
        )}

        <div className="modal-actions">
          <button
            type="button"
            className="btn btn-ghost"
            disabled={submitting}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={submitting || panError !== null}
            data-testid="new-app-submit"
          >
            {submitting ? "Creating draft..." : "Create draft"}
          </button>
        </div>
      </form>
    </ModalShell>
  );
}

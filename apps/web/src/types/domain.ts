/**
 * Mode-agnostic domain model shared by the live API adapter and the
 * showcase (demo) mock engine. Pages should depend on these types rather
 * than on the raw `/api/v1` contracts in `src/api/types.ts`.
 */

export type UserRole = "admin" | "loan_officer" | "reviewer";

export type ApplicationStatus =
  | "Draft"
  | "Submitted"
  | "Approved"
  | "Rejected"
  | "Discarded";

export type ReviewDecision = Extract<ApplicationStatus, "Approved" | "Rejected">;

/** Allowed loan tenor options (months) for intake. */
export const TENOR_MONTH_OPTIONS = [12, 24, 36, 48, 60] as const;
export type TenorMonths = (typeof TENOR_MONTH_OPTIONS)[number];
export const DEFAULT_TENOR_MONTHS: TenorMonths = 36;

declare const panBrand: unique symbol;

/** PAN in the canonical `ABCDE1234F` form; obtain one via `toPan`. */
export type Pan = string & { readonly [panBrand]: true };

export const PAN_PATTERN = /^[A-Z]{5}[0-9]{4}[A-Z]$/;

export function isValidPan(value: string): value is Pan {
  return PAN_PATTERN.test(value);
}

export function toPan(value: string): Pan {
  const normalized = value.trim().toUpperCase();
  if (!isValidPan(normalized)) {
    throw new RangeError(`Invalid PAN "${value}": expected format ABCDE1234F`);
  }
  return normalized;
}

export interface Application {
  ref: string;
  applicant: string;
  pan: Pan;
  amount: number;
  /** Monthly net take-home income (INR); FOIR divisor. */
  income: number;
  /** Monthly gross income (INR). */
  grossIncome: number;
  /** Loan tenor in months. */
  tenor_months: number;
  cibil: number;
  status: ApplicationStatus;
  purpose: string;
  notes: string;
  reviewerNote: string;
  created_at: string;
}

/** Fields an officer supplies when opening or editing a draft. */
export type ApplicationDraftInput = Pick<
  Application,
  | "applicant"
  | "pan"
  | "amount"
  | "income"
  | "grossIncome"
  | "tenor_months"
  | "cibil"
  | "purpose"
  | "notes"
>;

export interface AuditEvent {
  ts: string;
  actor: string;
  action: string;
  ref: string;
  prevHash: string;
  hash: string;
}

export interface AuditVerification {
  isValid: boolean;
  totalEventsChecked: number;
  /** Index (demo) or event id (live) of the first broken link, if any. */
  tamperedAt: string | null;
  errorMessage: string | null;
}

export interface SessionUser {
  id: string;
  name: string;
  email: string;
  role: UserRole;
}

export interface FoirResult {
  proposedEmi: number;
  totalObligations: number;
  foirPct: number;
  foirThresholdPct: number;
  foirPassed: boolean;
  netDisposableIncome: number;
}

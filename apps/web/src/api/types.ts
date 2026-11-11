/** Shared API types aligned to LoanLens `/api/v1` contracts. */

export type UserRole = "loan_officer" | "reviewer" | "admin";

export type ApplicationStatus =
  | "draft"
  | "submitted"
  | "processing"
  | "processing_failed"
  | "awaiting_human_review"
  | "in_review"
  | "clarification_required"
  | "escalated"
  | "review_completed"
  | "archived";

export type ApplicantType = "salaried" | "self_employed";

export type DocumentType =
  | "identity_proof"
  | "pan_card"
  | "address_proof"
  | "salary_slip"
  | "bank_statement"
  | "form_16"
  | "itr"
  | "gst_return"
  | "business_proof"
  | "existing_loan_statement"
  | "unsupported"
  | "other";

export type DocumentStatus =
  | "uploaded"
  | "classified"
  | "extracting"
  | "extracted"
  | "failed"
  | "unsupported"
  | "replaced";

export type AssessmentRecommendation =
  | "approve"
  | "reject"
  | "refer"
  | "exception_approved";

export type FindingDispositionAction =
  | "accepted_risk"
  | "overridden"
  | "confirmed_issue"
  | "false_positive";

export type RuleStatus = "passed" | "flagged" | "failed";
export type RuleSeverity = "info" | "low" | "medium" | "high" | "critical";

export interface UserProfile {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
}

export interface LoginResponse {
  csrf_token: string;
  user: UserProfile;
}

export interface Applicant {
  id: string;
  application_id: string;
  full_name: string;
  date_of_birth: string;
  pan: string;
  current_address: string;
  employer_or_business_name: string;
  time_in_job_or_business_months: number;
  gross_monthly_income: string;
  net_monthly_income: string;
  other_monthly_income: string | null;
  other_monthly_income_evidenced: boolean;
  monthly_household_expenses: string | null;
  total_existing_monthly_emis: string;
  outstanding_existing_loan_amount: string | null;
  created_at: string;
  updated_at: string;
}

export interface Application {
  id: string;
  reference_code: string;
  status: ApplicationStatus;
  created_by_user_id: string;
  assigned_reviewer_user_id: string | null;
  applicant_type: ApplicantType;
  requested_amount: string;
  currency: string;
  tenure_months: number;
  loan_purpose: string;
  loan_purpose_other: string | null;
  applicable_policy_version_id: string | null;
  withdraw_reason: string | null;
  submitted_at: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  applicant: Applicant;
}

export interface ApplicationListResponse {
  items: Application[];
  offset: number;
  limit: number;
  total: number;
}

export interface CreateApplicationPayload {
  applicant_type: ApplicantType;
  requested_amount: string;
  tenure_months: number;
  loan_purpose: string;
  loan_purpose_other?: string | null;
  applicant: {
    full_name: string;
    date_of_birth: string;
    pan: string;
    current_address: string;
    employer_or_business_name: string;
    time_in_job_or_business_months: number;
    gross_monthly_income: string;
    net_monthly_income: string;
    total_existing_monthly_emis: string;
    other_monthly_income?: string | null;
    other_monthly_income_evidenced?: boolean;
    monthly_household_expenses?: string | null;
    outstanding_existing_loan_amount?: string | null;
  };
}

export interface DocumentMeta {
  id: string;
  application_id: string;
  content_sha256: string;
  document_type: DocumentType;
  status: DocumentStatus;
  original_filename: string;
  mime_type: string;
  byte_size: number;
  is_alternative_evidence: boolean;
  uploaded_by_user_id: string;
  created_at: string;
  updated_at: string;
}

export interface Assessment {
  id: string;
  application_id: string;
  processing_run_id: string;
  input_snapshot_id: string;
  applicable_policy_version_id: string;
  assigned_reviewer_id: string | null;
  recommendation: AssessmentRecommendation | null;
  reviewer_notes: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AssessmentListResponse {
  items: Assessment[];
  total: number;
}

export interface VerificationFinding {
  id: string;
  rule_code: string;
  rule_name: string;
  status: RuleStatus;
  severity: RuleSeverity;
  message: string;
  diff_payload: Record<string, unknown>;
  provenance_sources: Array<Record<string, unknown>>;
  created_at: string;
}

export interface VerificationRunSummary {
  id: string;
  application_id: string;
  processing_run_id: string;
  total_rules_evaluated: number;
  passed_count: number;
  flagged_count: number;
  failed_count: number;
  created_at: string;
  findings: VerificationFinding[];
}

export interface FinancialCalculation {
  id: string;
  application_id: string;
  processing_run_id: string;
  input_snapshot_id: string;
  proposed_emi: string;
  total_obligations: string;
  foir_pct: string;
  foir_threshold_pct: string;
  foir_passed: boolean;
  net_disposable_income: string;
  max_qualifying_loan_amount: string;
  calculation_details: Record<string, unknown>;
  created_at: string;
}

export interface AiObservation {
  id: string;
  application_id: string;
  processing_run_id: string;
  model_id: string;
  observation_text: string;
  risk_factors: string[];
  strengths: string[];
  cited_chunk_ids: string[];
  is_ai_generated: boolean;
  created_at: string;
}

export interface PolicyChunkMatch {
  chunk_id: string;
  section_label: string;
  content_text: string;
  rank_score: number;
}

export interface PolicyCitationsResponse {
  application_id: string;
  policy_version_id: string;
  citations: Record<string, PolicyChunkMatch[]>;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  request_id?: string;
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;
  readonly requestId: string | null;

  constructor(
    status: number,
    body: ApiErrorBody | null,
    fallbackMessage: string,
  ) {
    super(body?.message ?? fallbackMessage);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.code ?? "UNKNOWN_ERROR";
    this.details = body?.details ?? {};
    this.requestId = body?.request_id ?? null;
  }
}

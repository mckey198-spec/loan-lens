import {
  createApplication as createLiveApplication,
  getApplication,
  listApplications,
  submitApplication,
  updateApplication,
} from "../api/applications";
import { decideAssessment, listAssessments } from "../api/assessments";
import { apiFetch } from "../api/client";
import {
  ApiError,
  type ApplicantType,
  type Application as LiveApplication,
  type Assessment,
  type AssessmentRecommendation,
} from "../api/types";
import {
  toPan,
  type Application,
  type ApplicationDraftInput,
  type ApplicationStatus,
  type AuditEvent,
  type AuditVerification,
  type ReviewDecision,
} from "../types/domain";
import {
  createMockDraft,
  decideMockApplication,
  discardMockDraft,
  getMockApplication,
  getMockApplicationAuditEvents,
  getMockApplications,
  getMockAuditEvents,
  submitMockApplication,
  updateMockDraft,
  verifyMockAuditChain,
} from "./mockData";

// ---------------------------------------------------------------------------
// Mode selection
// ---------------------------------------------------------------------------

export type DataMode = "demo" | "live";

export const IS_DEMO: boolean = import.meta.env.VITE_DEMO_MODE === "true";

export const MODE_STORAGE_KEY = "loanlens_mode";

function readModeOverride(): DataMode | null {
  try {
    const stored = localStorage.getItem(MODE_STORAGE_KEY);
    return stored === "demo" || stored === "live" ? stored : null;
  } catch {
    return null;
  }
}

export function getDataMode(): DataMode {
  return readModeOverride() ?? (IS_DEMO ? "demo" : "live");
}

export function isDemoMode(): boolean {
  return getDataMode() === "demo";
}

/** Persists a runtime override; pass `null` to fall back to `VITE_DEMO_MODE`. */
export function setDataMode(mode: DataMode | null): void {
  try {
    if (mode === null) {
      localStorage.removeItem(MODE_STORAGE_KEY);
    } else {
      localStorage.setItem(MODE_STORAGE_KEY, mode);
    }
  } catch {
    // Storage unavailable (e.g. privacy mode); the build-time default applies.
  }
}

// ---------------------------------------------------------------------------
// Live adapter: `/api/v1` contract <-> domain model
// ---------------------------------------------------------------------------

/** Intake fields the live backend requires beyond the domain draft. */
export interface IntakeDetails {
  totalExistingMonthlyEmis: number;
  applicantType: ApplicantType;
  tenureMonths: number;
  dateOfBirth: string;
  currentAddress: string;
  employerOrBusinessName: string;
  timeInJobOrBusinessMonths: number;
  grossMonthlyIncome: number;
}

interface LiveAuditVerifyResponse {
  is_valid: boolean;
  total_events_checked: number;
  tampered_event_id: string | null;
  error_message: string | null;
}

const LIVE_PAGE_SIZE = 100;

const DECISION_TO_RECOMMENDATION: Record<ReviewDecision, AssessmentRecommendation> = {
  Approved: "approve",
  Rejected: "reject",
};

const refToId = new Map<string, string>();

function clientError(
  status: number,
  code: string,
  message: string,
  details: Record<string, unknown> = {},
): ApiError {
  return new ApiError(
    status,
    { code, message, details, request_id: `client-${crypto.randomUUID()}` },
    message,
  );
}

function toMoney(value: number): string {
  return value.toFixed(2);
}

function toDomainStatus(
  application: LiveApplication,
  assessment: Assessment | null,
): ApplicationStatus {
  switch (application.status) {
    case "draft":
      return "Draft";
    case "archived":
      return "Discarded";
    case "review_completed":
      if (
        assessment?.recommendation === "approve" ||
        assessment?.recommendation === "exception_approved"
      ) {
        return "Approved";
      }
      if (assessment?.recommendation === "reject") {
        return "Rejected";
      }
      return "Submitted";
    default:
      return "Submitted";
  }
}

function toDomain(
  application: LiveApplication,
  assessment: Assessment | null = null,
): Application {
  refToId.set(application.reference_code, application.id);
  return {
    ref: application.reference_code,
    applicant: application.applicant.full_name,
    pan: toPan(application.applicant.pan),
    amount: Number(application.requested_amount),
    income: Number(application.applicant.net_monthly_income),
    grossIncome: Number(application.applicant.gross_monthly_income),
    tenor_months: application.tenure_months,
    // Bureau score is not exposed by `/api/v1/applications`.
    cibil: 0,
    status: toDomainStatus(application, assessment),
    purpose: application.loan_purpose,
    notes: application.loan_purpose_other ?? "",
    reviewerNote: assessment?.reviewer_notes ?? "",
    created_at: application.created_at,
  };
}

async function listAllLiveApplications(): Promise<LiveApplication[]> {
  const items: LiveApplication[] = [];
  let offset = 0;
  for (;;) {
    const page = await listApplications({ offset, limit: LIVE_PAGE_SIZE });
    items.push(...page.items);
    offset += page.items.length;
    if (page.items.length === 0 || offset >= page.total) {
      return items;
    }
  }
}

async function latestAssessment(applicationId: string): Promise<Assessment | null> {
  const { items } = await listAssessments(applicationId);
  return items.reduce<Assessment | null>(
    (latest, item) => (latest === null || item.created_at > latest.created_at ? item : latest),
    null,
  );
}

async function resolveApplicationId(ref: string): Promise<string> {
  const cached = refToId.get(ref);
  if (cached) {
    return cached;
  }
  const match = (await listAllLiveApplications()).find((a) => a.reference_code === ref);
  if (!match) {
    throw clientError(404, "APPLICATION_NOT_FOUND", `Application ${ref} was not found.`, { ref });
  }
  refToId.set(ref, match.id);
  return match.id;
}

const live = {
  async fetchApplications(): Promise<Application[]> {
    const applications = await listAllLiveApplications();
    return Promise.all(
      applications.map(async (application) =>
        toDomain(
          application,
          application.status === "review_completed"
            ? await latestAssessment(application.id)
            : null,
        ),
      ),
    );
  },

  async fetchApplication(ref: string): Promise<Application> {
    const applicationId = await resolveApplicationId(ref);
    const application = await getApplication(applicationId);
    return toDomain(application, await latestAssessment(applicationId));
  },

  async createApplication(
    input: ApplicationDraftInput,
    intake?: IntakeDetails,
  ): Promise<Application> {
    if (!intake) {
      throw clientError(
        422,
        "INTAKE_DETAILS_REQUIRED",
        "Live mode requires full intake details to create an application.",
      );
    }
    const created = await createLiveApplication({
      applicant_type: intake.applicantType,
      requested_amount: toMoney(input.amount),
      tenure_months: input.tenor_months || intake.tenureMonths,
      loan_purpose: input.purpose,
      loan_purpose_other: input.notes || null,
      applicant: {
        full_name: input.applicant,
        date_of_birth: intake.dateOfBirth,
        pan: input.pan,
        current_address: intake.currentAddress,
        employer_or_business_name: intake.employerOrBusinessName,
        time_in_job_or_business_months: intake.timeInJobOrBusinessMonths,
        gross_monthly_income: toMoney(input.grossIncome || intake.grossMonthlyIncome),
        net_monthly_income: toMoney(input.income),
        total_existing_monthly_emis: toMoney(intake.totalExistingMonthlyEmis),
      },
    });
    return toDomain(created);
  },

  async updateDraft(
    ref: string,
    updates: Partial<ApplicationDraftInput>,
  ): Promise<Application> {
    const applicationId = await resolveApplicationId(ref);
    const applicantPatch: Record<string, string> = {};
    if (updates.applicant !== undefined) {
      applicantPatch.full_name = updates.applicant;
    }
    if (updates.pan !== undefined) {
      applicantPatch.pan = updates.pan;
    }
    if (updates.income !== undefined) {
      applicantPatch.net_monthly_income = toMoney(updates.income);
    }
    if (updates.grossIncome !== undefined) {
      applicantPatch.gross_monthly_income = toMoney(updates.grossIncome);
    }
    const payload: Record<string, unknown> = {};
    if (updates.amount !== undefined) {
      payload.requested_amount = toMoney(updates.amount);
    }
    if (updates.purpose !== undefined) {
      payload.loan_purpose = updates.purpose;
    }
    if (updates.notes !== undefined) {
      payload.loan_purpose_other = updates.notes || null;
    }
    if (updates.tenor_months !== undefined) {
      payload.tenure_months = updates.tenor_months;
    }
    if (Object.keys(applicantPatch).length > 0) {
      payload.applicant = applicantPatch;
    }
    const updated = await updateApplication(
      applicationId,
      payload as Parameters<typeof updateApplication>[1],
    );
    return toDomain(updated);
  },

  async discardDraft(ref: string): Promise<Application> {
    const applicationId = await resolveApplicationId(ref);
    const archived = await apiFetch<LiveApplication>(
      `/api/v1/applications/${applicationId}/withdraw`,
      {
        method: "POST",
        body: JSON.stringify({ reason: "Draft discarded by loan officer." }),
      },
    );
    return toDomain(archived);
  },

  async submitToReviewer(ref: string): Promise<Application> {
    const submitted = await submitApplication(await resolveApplicationId(ref));
    return toDomain(submitted);
  },

  async recordDecision(
    ref: string,
    decision: ReviewDecision,
    note: string,
  ): Promise<Application> {
    const applicationId = await resolveApplicationId(ref);
    const { items } = await listAssessments(applicationId);
    const open = items
      .filter((a) => a.decided_at === null)
      .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
    if (!open) {
      throw clientError(
        409,
        "NO_OPEN_ASSESSMENT",
        `Application ${ref} has no open assessment to decide. Claim it first.`,
        { ref },
      );
    }
    const decided = await decideAssessment(applicationId, open.id, {
      recommendation: DECISION_TO_RECOMMENDATION[decision],
      reviewer_notes: note,
    });
    return toDomain(await getApplication(applicationId), decided);
  },

  async fetchAuditEvents(_ref?: string): Promise<AuditEvent[]> {
    // Live `/api/v1` exposes chain verification, not an event list endpoint.
    return [];
  },

  async verifyAuditChain(): Promise<AuditVerification> {
    const body = await apiFetch<LiveAuditVerifyResponse>("/api/v1/audit/verify");
    return {
      isValid: body.is_valid,
      totalEventsChecked: body.total_events_checked,
      tamperedAt: body.tampered_event_id,
      errorMessage: body.error_message,
    };
  },
};

// ---------------------------------------------------------------------------
// Public, mode-aware API
// ---------------------------------------------------------------------------

export function fetchApplications(): Promise<Application[]> {
  return isDemoMode() ? getMockApplications() : live.fetchApplications();
}

export function fetchApplication(ref: string): Promise<Application> {
  return isDemoMode() ? getMockApplication(ref) : live.fetchApplication(ref);
}

export function createApplication(
  input: ApplicationDraftInput,
  intake?: IntakeDetails,
): Promise<Application> {
  return isDemoMode()
    ? createMockDraft(input, intake?.totalExistingMonthlyEmis ?? 0)
    : live.createApplication(input, intake);
}

export function updateDraft(
  ref: string,
  updates: Partial<ApplicationDraftInput>,
): Promise<Application> {
  return isDemoMode() ? updateMockDraft(ref, updates) : live.updateDraft(ref, updates);
}

export function submitToReviewer(ref: string): Promise<Application> {
  return isDemoMode() ? submitMockApplication(ref) : live.submitToReviewer(ref);
}

export function recordDecision(
  ref: string,
  decision: ReviewDecision,
  note: string,
): Promise<Application> {
  return isDemoMode()
    ? decideMockApplication(ref, decision, note)
    : live.recordDecision(ref, decision, note);
}

export function discardDraft(ref: string): Promise<Application> {
  return isDemoMode() ? discardMockDraft(ref) : live.discardDraft(ref);
}

/** When `ref` is omitted, returns the global ledger (admin). With `ref`, application-scoped. */
export function fetchAuditEvents(ref?: string): Promise<AuditEvent[]> {
  if (isDemoMode()) {
    return ref === undefined
      ? getMockAuditEvents()
      : getMockApplicationAuditEvents(ref);
  }
  return live.fetchAuditEvents(ref);
}

export function verifyAuditChain(): Promise<AuditVerification> {
  return isDemoMode() ? verifyMockAuditChain() : live.verifyAuditChain();
}

export const apiClient = {
  getDataMode,
  isDemoMode,
  setDataMode,
  fetchApplications,
  fetchApplication,
  createApplication,
  updateDraft,
  submitToReviewer,
  recordDecision,
  discardDraft,
  fetchAuditEvents,
  verifyAuditChain,
} as const;

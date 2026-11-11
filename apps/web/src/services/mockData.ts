import { ApiError } from "../api/types";
import {
  isValidPan,
  toPan,
  type Application,
  type ApplicationDraftInput,
  type AuditEvent,
  type AuditVerification,
  type FoirResult,
  type ReviewDecision,
  type SessionUser,
  type UserRole,
} from "../types/domain";
import { calculateFoir } from "./foir";

export const GENESIS_HASH = "0".repeat(64);

export const ROLE_PROFILES: Readonly<Record<UserRole, Readonly<SessionUser>>> = {
  admin: {
    id: "usr-admin-01",
    name: "Ananya Mishra",
    email: "ananya.mishra@loanlens.demo",
    role: "admin",
  },
  loan_officer: {
    id: "usr-officer-01",
    name: "Rakesh Panda",
    email: "rakesh.panda@loanlens.demo",
    role: "loan_officer",
  },
  reviewer: {
    id: "usr-reviewer-01",
    name: "Sushree Sahoo",
    email: "sushree.sahoo@loanlens.demo",
    role: "reviewer",
  },
};

interface MockRecord {
  application: Application;
  existingMonthlyEmis: number;
}

interface SeedProfile {
  record: MockRecord;
  submittedAt?: string;
  decidedAt?: string;
}

function seedProfiles(): SeedProfile[] {
  return [
    {
      record: {
        application: {
          ref: "LL-5284DB1327C6",
          applicant: "Pratibha Rani Pattanayak",
          pan: toPan("BQRPP5821M"),
          amount: 1_500_000,
          income: 95_000,
          cibil: 782,
          status: "Approved",
          purpose: "Home renovation",
          notes: "Salaried, State Govt. teacher, 11 years in service.",
          reviewerNote:
            "FOIR within policy; income verified against 6 months of salary credits.",
          created_at: "2026-10-27T05:12:40.000Z",
        },
        existingMonthlyEmis: 12_000,
      },
      submittedAt: "2026-10-27T06:03:11.000Z",
      decidedAt: "2026-10-29T09:41:27.000Z",
    },
    {
      record: {
        application: {
          ref: "LL-9A71C04E2B58",
          applicant: "Biswajit Nayak",
          pan: toPan("CHNPN7340D"),
          amount: 800_000,
          income: 62_000,
          cibil: 731,
          status: "Submitted",
          purpose: "Vehicle purchase",
          notes: "Self-employed, hardware retail outlet in Cuttack.",
          reviewerNote: "",
          created_at: "2026-11-03T07:25:02.000Z",
        },
        existingMonthlyEmis: 8_500,
      },
      submittedAt: "2026-11-03T08:10:45.000Z",
    },
    {
      record: {
        application: {
          ref: "LL-3F0E8D6A91C2",
          applicant: "Kalyani Devi Behera",
          pan: toPan("DKBPB2196H"),
          amount: 450_000,
          income: 38_000,
          cibil: 648,
          status: "Rejected",
          purpose: "Medical expenses",
          notes: "Two active personal loans disclosed at intake.",
          reviewerNote:
            "FOIR exceeds 50% policy ceiling after existing obligations; CIBIL below 700.",
          created_at: "2026-11-05T04:48:19.000Z",
        },
        existingMonthlyEmis: 14_500,
      },
      submittedAt: "2026-11-05T05:30:00.000Z",
      decidedAt: "2026-11-06T11:15:52.000Z",
    },
    {
      record: {
        application: {
          ref: "LL-C7B2E5F04D19",
          applicant: "Satyabrata Swain",
          pan: toPan("EYSPS6603Q"),
          amount: 1_200_000,
          income: 110_000,
          cibil: 805,
          status: "Draft",
          purpose: "Business expansion",
          notes: "Awaiting latest ITR before submission.",
          reviewerNote: "",
          created_at: "2026-11-10T10:02:33.000Z",
        },
        existingMonthlyEmis: 0,
      },
    },
  ];
}

let currentUser: SessionUser = ROLE_PROFILES.loan_officer;
let records = new Map<string, MockRecord>();
let ledger: AuditEvent[] = [];
let ledgerTail: Promise<unknown> = Promise.resolve();

// ---------------------------------------------------------------------------
// Errors & RBAC
// ---------------------------------------------------------------------------

function mockRequestId(): string {
  return `demo-${crypto.randomUUID()}`;
}

function mockError(
  status: number,
  code: string,
  message: string,
  details: Record<string, unknown> = {},
): ApiError {
  return new ApiError(
    status,
    { code, message, details, request_id: mockRequestId() },
    message,
  );
}

function requireRole(...allowed: UserRole[]): SessionUser {
  if (!allowed.includes(currentUser.role)) {
    throw mockError(403, "FORBIDDEN", "Your role is not permitted to perform this action.", {
      role: currentUser.role,
      allowed_roles: allowed,
    });
  }
  return currentUser;
}

export function getMockSession(): SessionUser {
  return { ...currentUser };
}

export function getMockRole(): UserRole {
  return currentUser.role;
}

export function setMockRole(role: UserRole): SessionUser {
  currentUser = ROLE_PROFILES[role];
  return getMockSession();
}

// ---------------------------------------------------------------------------
// Audit ledger (SHA-256 hash chain)
// ---------------------------------------------------------------------------

async function sha256Hex(input: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(input),
  );
  return Array.from(new Uint8Array(digest), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
}

function computeEventHash(
  event: Omit<AuditEvent, "hash">,
): Promise<string> {
  const canonical = JSON.stringify([
    event.prevHash,
    event.ts,
    event.actor,
    event.action,
    event.ref,
  ]);
  return sha256Hex(canonical);
}

async function appendAuditEvent(
  actor: string,
  action: string,
  ref: string,
  ts: string = new Date().toISOString(),
): Promise<AuditEvent> {
  const prevHash = ledger.at(-1)?.hash ?? GENESIS_HASH;
  const unsigned = { ts, actor, action, ref, prevHash };
  const event: AuditEvent = { ...unsigned, hash: await computeEventHash(unsigned) };
  ledger.push(event);
  return event;
}

/**
 * Serializes ledger-affecting operations so validation, state mutation and
 * hash-chain append happen atomically relative to each other.
 */
function serialized<T>(operation: () => Promise<T>): Promise<T> {
  const result = ledgerTail.then(operation);
  ledgerTail = result.catch(() => undefined);
  return result;
}

async function seedLedger(profiles: SeedProfile[]): Promise<void> {
  const seedEvents: Array<{ ts: string; actor: string; action: string; ref: string }> = [];
  for (const { record, submittedAt, decidedAt } of profiles) {
    const { ref, status, created_at } = record.application;
    seedEvents.push({
      ts: created_at,
      actor: ROLE_PROFILES.loan_officer.email,
      action: "application.created",
      ref,
    });
    if (submittedAt) {
      seedEvents.push({
        ts: submittedAt,
        actor: ROLE_PROFILES.loan_officer.email,
        action: "application.submitted",
        ref,
      });
    }
    if (decidedAt) {
      seedEvents.push({
        ts: decidedAt,
        actor: ROLE_PROFILES.reviewer.email,
        action: `application.${status.toLowerCase()}`,
        ref,
      });
    }
  }
  seedEvents.sort((a, b) => a.ts.localeCompare(b.ts));
  for (const e of seedEvents) {
    await appendAuditEvent(e.actor, e.action, e.ref, e.ts);
  }
}

// ---------------------------------------------------------------------------
// Store lifecycle
// ---------------------------------------------------------------------------

/** Restores the seeded demo dataset and genesis audit chain. */
export function resetMockStore(): Promise<void> {
  return serialized(async () => {
    const profiles = seedProfiles();
    currentUser = ROLE_PROFILES.loan_officer;
    records = new Map(profiles.map((p) => [p.record.application.ref, p.record]));
    ledger = [];
    await seedLedger(profiles);
  });
}

void resetMockStore();

// ---------------------------------------------------------------------------
// Applications
// ---------------------------------------------------------------------------

function generateRef(): string {
  let ref: string;
  do {
    const bytes = crypto.getRandomValues(new Uint8Array(6));
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0"))
      .join("")
      .toUpperCase();
    ref = `LL-${hex}`;
  } while (records.has(ref));
  return ref;
}

function findRecord(ref: string): MockRecord {
  const record = records.get(ref);
  if (!record) {
    throw mockError(404, "APPLICATION_NOT_FOUND", `Application ${ref} was not found.`, { ref });
  }
  return record;
}

function assertStatus(application: Application, expected: Application["status"]): void {
  if (application.status !== expected) {
    throw mockError(
      409,
      "INVALID_STATUS_TRANSITION",
      `Application ${application.ref} is ${application.status}; expected ${expected}.`,
      { ref: application.ref, current_status: application.status, expected_status: expected },
    );
  }
}

function validateDraftFields(fields: Partial<ApplicationDraftInput>): void {
  const errors: Record<string, string> = {};
  if (fields.applicant !== undefined && fields.applicant.trim() === "") {
    errors.applicant = "Applicant name is required.";
  }
  if (fields.pan !== undefined && !isValidPan(fields.pan)) {
    errors.pan = "PAN must match ABCDE1234F.";
  }
  if (fields.amount !== undefined && !(Number.isFinite(fields.amount) && fields.amount > 0)) {
    errors.amount = "Loan amount must be a positive number.";
  }
  if (fields.income !== undefined && !(Number.isFinite(fields.income) && fields.income >= 0)) {
    errors.income = "Monthly income must be zero or positive.";
  }
  if (
    fields.cibil !== undefined &&
    !(Number.isInteger(fields.cibil) && fields.cibil >= 300 && fields.cibil <= 900)
  ) {
    errors.cibil = "CIBIL score must be an integer between 300 and 900.";
  }
  if (Object.keys(errors).length > 0) {
    throw mockError(422, "VALIDATION_ERROR", "Application fields are invalid.", {
      fields: errors,
    });
  }
}

export async function getMockApplications(): Promise<Application[]> {
  return Array.from(records.values(), (r) => structuredClone(r.application)).sort(
    (a, b) => b.created_at.localeCompare(a.created_at),
  );
}

export async function getMockApplication(ref: string): Promise<Application> {
  return structuredClone(findRecord(ref).application);
}

export function createMockDraft(
  data: ApplicationDraftInput,
  existingMonthlyEmis: number = 0,
): Promise<Application> {
  return serialized(async () => {
    const actor = requireRole("loan_officer", "admin");
    validateDraftFields(data);
    const application: Application = {
      ...data,
      applicant: data.applicant.trim(),
      ref: generateRef(),
      status: "Draft",
      reviewerNote: "",
      created_at: new Date().toISOString(),
    };
    await appendAuditEvent(actor.email, "application.created", application.ref, application.created_at);
    records.set(application.ref, { application, existingMonthlyEmis });
    return structuredClone(application);
  });
}

export function updateMockDraft(
  ref: string,
  updates: Partial<ApplicationDraftInput>,
): Promise<Application> {
  return serialized(async () => {
    const actor = requireRole("loan_officer", "admin");
    const record = findRecord(ref);
    assertStatus(record.application, "Draft");
    const defined = Object.fromEntries(
      Object.entries(updates).filter(([, value]) => value !== undefined),
    ) as Partial<ApplicationDraftInput>;
    validateDraftFields(defined);
    await appendAuditEvent(actor.email, "application.updated", ref);
    record.application = { ...record.application, ...defined };
    return structuredClone(record.application);
  });
}

export function submitMockApplication(ref: string): Promise<Application> {
  return serialized(async () => {
    const actor = requireRole("loan_officer", "admin");
    const record = findRecord(ref);
    assertStatus(record.application, "Draft");
    validateDraftFields(record.application);
    await appendAuditEvent(actor.email, "application.submitted", ref);
    record.application = { ...record.application, status: "Submitted" };
    return structuredClone(record.application);
  });
}

export function decideMockApplication(
  ref: string,
  decision: ReviewDecision,
  note: string,
): Promise<Application> {
  return serialized(async () => {
    const actor = requireRole("reviewer", "admin");
    const record = findRecord(ref);
    assertStatus(record.application, "Submitted");
    const reviewerNote = note.trim();
    if (reviewerNote.length < 10) {
      throw mockError(422, "VALIDATION_ERROR", "A reviewer note of at least 10 characters is required.", {
        fields: { note: "Reviewer note must be at least 10 characters." },
      });
    }
    await appendAuditEvent(actor.email, `application.${decision.toLowerCase()}`, ref);
    record.application = { ...record.application, status: decision, reviewerNote };
    return structuredClone(record.application);
  });
}

export async function getMockFoir(ref: string): Promise<FoirResult> {
  const { application, existingMonthlyEmis } = findRecord(ref);
  return calculateFoir({
    amount: application.amount,
    monthlyNetIncome: application.income,
    existingMonthlyEmis,
  });
}

// ---------------------------------------------------------------------------
// Audit read / verify
// ---------------------------------------------------------------------------

export function getMockAuditEvents(): Promise<AuditEvent[]> {
  return serialized(async () => {
    requireRole("admin");
    return ledger.map((e) => ({ ...e }));
  });
}

/** Application-scoped ledger slice; any authenticated persona may read it. */
export function getMockApplicationAuditEvents(ref: string): Promise<AuditEvent[]> {
  return serialized(async () => {
    findRecord(ref);
    return ledger.filter((e) => e.ref === ref).map((e) => ({ ...e }));
  });
}

export function verifyMockAuditChain(): Promise<AuditVerification> {
  return serialized(async () => {
    requireRole("admin");
    let expectedPrev = GENESIS_HASH;
    for (const [index, event] of ledger.entries()) {
      if (event.prevHash !== expectedPrev) {
        return {
          isValid: false,
          totalEventsChecked: index + 1,
          tamperedAt: String(index),
          errorMessage: `Event ${index} prevHash does not link to the preceding event.`,
        };
      }
      const { hash, ...unsigned } = event;
      if ((await computeEventHash(unsigned)) !== hash) {
        return {
          isValid: false,
          totalEventsChecked: index + 1,
          tamperedAt: String(index),
          errorMessage: `Event ${index} content does not match its recorded hash.`,
        };
      }
      expectedPrev = hash;
    }
    return {
      isValid: true,
      totalEventsChecked: ledger.length,
      tamperedAt: null,
      errorMessage: null,
    };
  });
}

/**
 * Showcase helper: rewrites a ledger entry in place without re-hashing, so
 * `verifyMockAuditChain` can demonstrate tamper detection.
 */
export function tamperMockAuditEvent(
  index: number,
  patch: Partial<Pick<AuditEvent, "ts" | "actor" | "action" | "ref" | "hash">>,
): Promise<void> {
  return serialized(async () => {
    const event = ledger[index];
    if (!event) {
      throw mockError(404, "AUDIT_EVENT_NOT_FOUND", `No audit event at index ${index}.`, { index });
    }
    ledger[index] = { ...event, ...patch };
  });
}

/**
 * Showcase helper: rebuilds prevHash links and recomputes SHA-256 hashes so the
 * demo ledger verifies cleanly again after a tamper simulation.
 */
export function repairMockAuditLedger(): Promise<void> {
  return serialized(async () => {
    let expectedPrev = GENESIS_HASH;
    for (let index = 0; index < ledger.length; index++) {
      const event = ledger[index];
      if (!event) {
        continue;
      }
      const unsigned = {
        ts: event.ts,
        actor: event.actor,
        action: event.action,
        ref: event.ref,
        prevHash: expectedPrev,
      };
      const hash = await computeEventHash(unsigned);
      ledger[index] = { ...unsigned, hash };
      expectedPrev = hash;
    }
  });
}

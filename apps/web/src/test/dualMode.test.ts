import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/types";
import {
  MODE_STORAGE_KEY,
  createApplication,
  fetchApplications,
  getDataMode,
  setDataMode,
  verifyAuditChain,
} from "../services/apiClient";
import { calculateFoir } from "../services/foir";
import {
  GENESIS_HASH,
  createMockDraft,
  decideMockApplication,
  getMockAuditEvents,
  getMockFoir,
  resetMockStore,
  setMockRole,
  submitMockApplication,
  repairMockAuditLedger,
  tamperMockAuditEvent,
  updateMockDraft,
  verifyMockAuditChain,
} from "../services/mockData";
import { toPan, type ApplicationDraftInput } from "../types/domain";

const draft: ApplicationDraftInput = {
  applicant: "Sanjukta Mohanty",
  pan: toPan("fgmpm1234k"),
  amount: 600_000,
  income: 70_000,
  grossIncome: 90_000,
  tenor_months: 36,
  cibil: 760,
  purpose: "Education",
  notes: "",
};

describe("demo mock store", () => {
  beforeEach(async () => {
    await resetMockStore();
  });

  it("seeds four profiles and a valid genesis-linked audit chain", async () => {
    setMockRole("admin");
    const events = await getMockAuditEvents();
    expect(events[0]?.prevHash).toBe(GENESIS_HASH);
    for (let i = 1; i < events.length; i += 1) {
      expect(events[i]?.prevHash).toBe(events[i - 1]?.hash);
    }
    expect(await verifyMockAuditChain()).toMatchObject({
      isValid: true,
      totalEventsChecked: events.length,
    });
  });

  it("runs draft -> submit -> decide with RBAC and extends the chain", async () => {
    const created = await createMockDraft(draft, 5_000);
    expect(created.ref).toMatch(/^LL-[0-9A-F]{12}$/);
    expect(created.pan).toBe("FGMPM1234K");

    await updateMockDraft(created.ref, { amount: 650_000, notes: undefined });
    await submitMockApplication(created.ref);

    await expect(
      decideMockApplication(created.ref, "Approved", "ok"),
    ).rejects.toMatchObject({ status: 403, code: "FORBIDDEN" });

    setMockRole("reviewer");
    const decided = await decideMockApplication(created.ref, "Approved", "Within policy.");
    expect(decided).toMatchObject({ status: "Approved", amount: 650_000, notes: "" });

    setMockRole("admin");
    const actions = (await getMockAuditEvents()).slice(-4).map((e) => e.action);
    expect(actions).toEqual([
      "application.created",
      "application.updated",
      "application.submitted",
      "application.approved",
    ]);
    expect((await verifyMockAuditChain()).isValid).toBe(true);
  });

  it("rejects invalid status transitions with structured errors", async () => {
    const error = await submitMockApplication("LL-5284DB1327C6").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: "INVALID_STATUS_TRANSITION" });
    expect((error as ApiError).requestId).toMatch(/^demo-/);
  });

  it("detects tampering", async () => {
    await tamperMockAuditEvent(2, { actor: "mallory@loanlens.demo" });
    setMockRole("admin");
    expect(await verifyMockAuditChain()).toMatchObject({
      isValid: false,
      tamperedAt: "2",
      totalEventsChecked: 3,
    });
  });

  it("repairs a tampered ledger by re-hashing the chain", async () => {
    await tamperMockAuditEvent(2, {
      actor: "mallory@loanlens.demo",
      hash: "deadbeef".repeat(8),
    });
    setMockRole("admin");
    expect((await verifyMockAuditChain()).isValid).toBe(false);

    await repairMockAuditLedger();
    expect(await verifyMockAuditChain()).toMatchObject({
      isValid: true,
      tamperedAt: null,
      errorMessage: null,
    });
  });

  it("computes FOIR consistent with seeded decisions", async () => {
    expect((await getMockFoir("LL-5284DB1327C6")).foirPassed).toBe(true);
    expect((await getMockFoir("LL-3F0E8D6A91C2")).foirPassed).toBe(false);
  });
});

describe("calculateFoir", () => {
  it("matches the backend amortizing EMI formula", () => {
    const result = calculateFoir({
      amount: 1_000_000,
      monthlyNetIncome: 80_000,
      existingMonthlyEmis: 10_000,
      annualRatePct: 12,
      tenureMonths: 120,
    });
    expect(result.proposedEmi).toBe(14_347.09);
    expect(result.foirPct).toBe(30.43);
    expect(result.foirPassed).toBe(true);
  });

  it("saturates FOIR when income is zero", () => {
    expect(
      calculateFoir({ amount: 100_000, monthlyNetIncome: 0, existingMonthlyEmis: 0 }).foirPct,
    ).toBe(999.99);
  });
});

describe("apiClient mode routing", () => {
  beforeEach(async () => {
    localStorage.clear();
    await resetMockStore();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("uses the localStorage override", () => {
    setDataMode("demo");
    expect(localStorage.getItem(MODE_STORAGE_KEY)).toBe("demo");
    expect(getDataMode()).toBe("demo");
    setDataMode("live");
    expect(getDataMode()).toBe("live");
  });

  it("routes to the mock store in demo mode without network", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    setDataMode("demo");
    expect(await fetchApplications()).toHaveLength(4);
    setMockRole("admin");
    expect((await verifyAuditChain()).isValid).toBe(true);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("calls /api/v1 in live mode and maps the response", async () => {
    const fetchSpy = vi.fn(async () =>
      new Response(
        JSON.stringify({
          is_valid: true,
          total_events_checked: 12,
          tampered_event_id: null,
          error_message: null,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchSpy);
    setDataMode("live");
    expect(await verifyAuditChain()).toEqual({
      isValid: true,
      totalEventsChecked: 12,
      tamperedAt: null,
      errorMessage: null,
    });
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/v1/audit/verify",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("requires intake details to create in live mode", async () => {
    setDataMode("live");
    await expect(createApplication(draft)).rejects.toMatchObject({
      code: "INTAKE_DETAILS_REQUIRED",
    });
  });
});

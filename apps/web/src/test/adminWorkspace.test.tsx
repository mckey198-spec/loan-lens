import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import * as apiClientModule from "../services/apiClient";
import { MODE_STORAGE_KEY } from "../services/apiClient";
import { getMockAuditEvents, resetMockStore, setMockRole } from "../services/mockData";
import { renderWithProviders } from "./testUtils";

function stubUnauthenticatedApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/api/v1/auth/me")) {
        return new Response(JSON.stringify({ code: "AUTH_UNAUTHORIZED", message: "No session" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ items: [], offset: 0, limit: 50, total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

beforeEach(async () => {
  localStorage.clear();
  localStorage.setItem(MODE_STORAGE_KEY, "demo");
  await resetMockStore();
  setMockRole("admin");
  stubUnauthenticatedApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("admin workspace", () => {
  it("switches between Users, Audit, and System tabs", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/admin" });
    await screen.findByTestId("admin-workspace");

    expect(screen.getByTestId("admin-panel-users")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-panel-audit")).not.toBeInTheDocument();
    expect(screen.queryByTestId("admin-panel-system")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("admin-tab-audit"));
    expect(await screen.findByTestId("admin-panel-audit")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-panel-users")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("admin-tab-system"));
    expect(await screen.findByTestId("admin-panel-system")).toBeInTheDocument();
    expect(screen.getByTestId("admin-worker-card")).toHaveTextContent("Healthy");
    expect(screen.getByTestId("admin-security-card")).toHaveTextContent("Enforced");
    expect(screen.queryByTestId("admin-panel-audit")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("admin-tab-users"));
    expect(await screen.findByTestId("admin-panel-users")).toBeInTheDocument();
  });

  it("lists all three standard operators and roles", async () => {
    renderWithProviders(<App />, { route: "/admin" });
    const table = within(await screen.findByTestId("admin-operators-table"));

    expect(table.getByText("Local Test Admin")).toBeInTheDocument();
    expect(table.getByText("admin@loanlens.local")).toBeInTheDocument();
    expect(table.getByText("Aarav Mohanty")).toBeInTheDocument();
    expect(table.getByText("officer@loanlens.local")).toBeInTheDocument();
    expect(table.getByText("Meera Krishnan")).toBeInTheDocument();
    expect(table.getByText("reviewer@loanlens.local")).toBeInTheDocument();

    expect(table.getByText("Admin")).toBeInTheDocument();
    expect(table.getByText("Loan Officer")).toBeInTheDocument();
    expect(table.getByText("Reviewer")).toBeInTheDocument();
    expect(table.getAllByText("Active (HttpOnly)")).toHaveLength(3);
  });

  it("verifies the audit chain, switches to the ledger tab, and updates the integrity badge", async () => {
    const user = userEvent.setup();
    const verifySpy = vi.spyOn(apiClientModule.apiClient, "verifyAuditChain");

    renderWithProviders(<App />, { route: "/admin" });
    await screen.findByTestId("admin-workspace");

    await user.click(screen.getByTestId("verify-audit-chain"));

    expect(verifySpy).toHaveBeenCalledTimes(1);
    expect(await screen.findByTestId("admin-panel-audit")).toBeInTheDocument();

    const events = await getMockAuditEvents();
    expect(await screen.findByTestId("audit-integrity-badge")).toHaveTextContent(
      `Verified (${events.length} blocks intact)`,
    );
    expect(screen.getByTestId("admin-toast")).toHaveTextContent(
      "SHA-256 audit hash chain verified with zero tampering",
    );
  });

  it("renders audit event rows with prev → record hash links", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/admin" });
    await screen.findByTestId("admin-workspace");

    await user.click(screen.getByTestId("admin-tab-audit"));
    const table = within(await screen.findByTestId("admin-audit-table"));
    const rows = await table.findAllByTestId("admin-audit-row");
    expect(rows.length).toBeGreaterThan(0);

    const events = await getMockAuditEvents();
    const first = events[0];
    expect(first).toBeDefined();
    if (!first) {
      throw new Error("expected seeded audit events");
    }

    const hashCell = within(rows[0]!).getByTestId("admin-audit-hash-link");
    expect(hashCell).toHaveTextContent(`${first.prevHash} → ${first.hash}`);
    expect(within(rows[0]!).getByText(first.action)).toBeInTheDocument();
    expect(within(rows[0]!).getByText(first.ref)).toBeInTheDocument();
  });

  it("simulates a ledger mutation and repairs the demo hash chain", async () => {
    const user = userEvent.setup();
    const verifySpy = vi.spyOn(apiClientModule.apiClient, "verifyAuditChain");

    renderWithProviders(<App />, { route: "/admin" });
    await screen.findByTestId("admin-workspace");
    await user.click(screen.getByTestId("admin-tab-audit"));

    expect(await screen.findByTestId("admin-tamper-actions")).toBeInTheDocument();
    expect(screen.queryByTestId("repair-ledger")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("simulate-ledger-mutation"));

    expect(await screen.findByTestId("audit-integrity-badge")).toHaveTextContent(
      "Chain Integrity Compromised at Block 2",
    );
    expect(verifySpy).toHaveBeenCalled();
    expect(await screen.findByTestId("repair-ledger")).toBeInTheDocument();

    await user.click(screen.getByTestId("repair-ledger"));

    const events = await getMockAuditEvents();
    expect(await screen.findByTestId("audit-integrity-badge")).toHaveTextContent(
      `Verified (${events.length} blocks intact)`,
    );
    expect(screen.queryByTestId("repair-ledger")).not.toBeInTheDocument();
  });
});

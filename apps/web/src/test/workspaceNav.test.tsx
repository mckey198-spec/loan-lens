import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { MODE_STORAGE_KEY } from "../services/apiClient";
import { getMockRole, resetMockStore } from "../services/mockData";
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

function roleSimulator() {
  return within(screen.getByRole("group", { name: "Role simulator" }));
}

beforeEach(async () => {
  localStorage.clear();
  await resetMockStore();
  stubUnauthenticatedApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("demo mode navigation", () => {
  beforeEach(() => {
    localStorage.setItem(MODE_STORAGE_KEY, "demo");
  });

  it("switches persona, badge, and workspace from the role simulator", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/" });

    expect(await screen.findByTestId("user-badge")).toHaveTextContent(
      "Rakesh Panda (Loan Officer)",
    );
    expect(screen.getByText(/^Loan Officer Workspace:/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ New Application" })).toBeInTheDocument();

    await user.click(roleSimulator().getByRole("button", { name: "Reviewer" }));

    expect(screen.getByTestId("user-badge")).toHaveTextContent("Sushree Sahoo (Reviewer)");
    expect(await screen.findByText(/^Reviewer Workspace:/)).toBeInTheDocument();
    expect(roleSimulator().getByRole("button", { name: "Reviewer" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("link", { name: "Reviewer" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("button", { name: "+ New Application" })).not.toBeInTheDocument();
    expect(getMockRole()).toBe("reviewer");

    await user.click(roleSimulator().getByRole("button", { name: "Admin" }));
    expect(await screen.findByTestId("admin-workspace")).toBeInTheDocument();
    expect(screen.getByText(/^Admin Workspace:/)).toBeInTheDocument();
    for (const name of ["Officer", "Reviewer", "Admin"]) {
      expect(screen.getByRole("link", { name })).toBeInTheDocument();
    }
  });

  it("resets demo data, keeps the simulated persona, and shows a toast", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/" });

    await user.click(await screen.findByRole("button", { name: "Reviewer" }));
    await user.click(screen.getByRole("button", { name: "Reset Demo Data" }));

    expect(await screen.findByRole("status")).toHaveTextContent(/Demo data reset/);
    expect(getMockRole()).toBe("reviewer");
    expect(screen.getByTestId("user-badge")).toHaveTextContent("(Reviewer)");
  });

  it("opens the account selector on log out", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/" });

    await user.click(await screen.findByRole("button", { name: "Log out" }));
    const dialog = screen.getByRole("dialog", { name: "Switch demo account" });
    await user.click(within(dialog).getByRole("button", { name: /Ananya Mishra/ }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await screen.findByTestId("admin-workspace")).toBeInTheDocument();
    expect(screen.getByTestId("user-badge")).toHaveTextContent("Ananya Mishra (Admin)");
  });

  it("shows the architecture spec modal and closes it with Escape", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/" });

    await user.click(await screen.findByRole("button", { name: "Architecture Spec" }));
    const spec = within(screen.getByTestId("architecture-spec"));
    expect(spec.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      "Strict Role-Based State Machine (RBAC Maker-Checker)",
      "Tamper-Evident SHA-256 Audit Ledger",
      "Deterministic Underwriting Engine & FOIR",
    ]);

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("data mode toggle", () => {
  it("enters demo mode from the login page without a live session", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/officer/applications" });

    await user.click(await screen.findByRole("button", { name: "Open Demo Showcase" }));

    expect(localStorage.getItem(MODE_STORAGE_KEY)).toBe("demo");
    expect(await screen.findByTestId("user-badge")).toHaveTextContent("Rakesh Panda");
    expect(screen.getByRole("group", { name: "Role simulator" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Demo Showcase" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("switches back to live mode, re-checks /auth/me, and hides the simulator", async () => {
    localStorage.setItem(MODE_STORAGE_KEY, "demo");
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/" });

    await user.click(await screen.findByRole("button", { name: "Live API" }));

    expect(localStorage.getItem(MODE_STORAGE_KEY)).toBe("live");
    await waitFor(() => expect(screen.getByLabelText("Email")).toBeInTheDocument());
    expect(screen.queryByRole("group", { name: "Role simulator" })).not.toBeInTheDocument();
    const meCalls = vi
      .mocked(fetch)
      .mock.calls.filter(([input]) => String(input).includes("/api/v1/auth/me"));
    expect(meCalls.length).toBeGreaterThanOrEqual(2);
  });
});

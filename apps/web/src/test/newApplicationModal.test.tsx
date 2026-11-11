import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { MODE_STORAGE_KEY } from "../services/apiClient";
import { getMockApplications, resetMockStore } from "../services/mockData";
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

function applicationTable() {
  return within(screen.getByTestId("officer-application-list"));
}

async function openNewApplicationModal(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await screen.findByTestId("officer-application-list");
  await user.click(screen.getByRole("button", { name: "New application" }));
  expect(await screen.findByTestId("new-application-form")).toBeInTheDocument();
}

async function fillValidDraft(
  user: ReturnType<typeof userEvent.setup>,
  overrides: Partial<{
    applicant: string;
    pan: string;
    amount: string;
    income: string;
    purpose: string;
  }> = {},
): Promise<void> {
  const values = {
    applicant: "Sanjukta Mohanty",
    pan: "ABCDE1234F",
    amount: "500000",
    income: "65000",
    purpose: "Micro-Enterprise Working Capital (24 mos)",
    ...overrides,
  };
  await user.type(screen.getByTestId("new-app-applicant"), values.applicant);
  await user.type(screen.getByTestId("new-app-pan"), values.pan);
  await user.type(screen.getByTestId("new-app-amount"), values.amount);
  await user.type(screen.getByTestId("new-app-income"), values.income);
  await user.type(screen.getByTestId("new-app-purpose"), values.purpose);
}

beforeEach(async () => {
  localStorage.clear();
  localStorage.setItem(MODE_STORAGE_KEY, "demo");
  await resetMockStore();
  stubUnauthenticatedApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("NewApplicationModal", () => {
  it("opens from the page CTA", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/officer/applications" });

    await openNewApplicationModal(user);

    expect(screen.getByRole("dialog", { name: "New application" })).toBeInTheDocument();
    expect(screen.getByTestId("new-app-submit")).toHaveTextContent("Create draft");
  });

  it("opens from the context-bar CTA", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/officer/applications" });
    await screen.findByTestId("officer-application-list");

    await user.click(screen.getByRole("button", { name: "+ New Application" }));

    expect(await screen.findByTestId("new-application-form")).toBeInTheDocument();
  });

  it("rejects an invalid PAN and accepts a valid one", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/officer/applications" });
    await openNewApplicationModal(user);

    const pan = screen.getByTestId("new-app-pan");
    expect(pan).toHaveClass("font-mono", "uppercase", "tracking-wider");
    expect(screen.getByTestId("new-app-amount").closest(".currency-input")).not.toBeNull();
    expect(screen.getByTestId("new-app-income").closest(".currency-input")).not.toBeNull();

    await user.type(pan, "ABCDEFGH");

    expect(await screen.findByTestId("new-app-pan-error")).toHaveTextContent(
      /PAN must match ABCDE1234F/i,
    );
    expect(screen.queryByTestId("new-app-pan-valid")).not.toBeInTheDocument();
    expect(screen.getByTestId("new-app-submit")).toBeDisabled();

    await user.clear(pan);
    await user.type(pan, "ABCDE1234F");

    await waitFor(() => {
      expect(screen.queryByTestId("new-app-pan-error")).not.toBeInTheDocument();
    });
    expect(await screen.findByTestId("new-app-pan-valid")).toHaveTextContent("Valid PAN");
    expect(pan).toHaveClass("pan-input--valid");
    expect(screen.getByTestId("new-app-submit")).not.toBeDisabled();
  });

  it("creates a Draft in demo mode and appends it to the table", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/officer/applications" });
    await openNewApplicationModal(user);
    await fillValidDraft(user);

    await user.click(screen.getByTestId("new-app-submit"));

    await waitFor(() => {
      expect(screen.queryByTestId("new-application-form")).not.toBeInTheDocument();
    });

    expect(await screen.findByRole("status")).toHaveTextContent(/Created draft LL-/i);

    const table = applicationTable();
    expect(await table.findByText("Sanjukta Mohanty")).toBeInTheDocument();
    const draftRow = table.getByText("Sanjukta Mohanty").closest("tr");
    expect(draftRow).not.toBeNull();
    expect(within(draftRow as HTMLElement).getByText("Draft")).toBeInTheDocument();

    const apps = await getMockApplications();
    expect(apps.some((a) => a.applicant === "Sanjukta Mohanty" && a.status === "Draft")).toBe(
      true,
    );
    expect(screen.getByRole("button", { name: "All (5)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Draft (2)" })).toBeInTheDocument();
  });

  it("closes on Cancel without creating an application", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/officer/applications" });
    await openNewApplicationModal(user);
    await fillValidDraft(user, { applicant: "Should Not Persist" });

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByTestId("new-application-form")).not.toBeInTheDocument();
    expect(applicationTable().queryByText("Should Not Persist")).not.toBeInTheDocument();
    expect(await getMockApplications()).toHaveLength(4);
  });

  it("closes on Escape without creating an application", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/officer/applications" });
    await openNewApplicationModal(user);
    await fillValidDraft(user, { applicant: "Escape Close Candidate" });

    await user.keyboard("{Escape}");

    expect(screen.queryByTestId("new-application-form")).not.toBeInTheDocument();
    expect(applicationTable().queryByText("Escape Close Candidate")).not.toBeInTheDocument();
    expect(await getMockApplications()).toHaveLength(4);
  });
});

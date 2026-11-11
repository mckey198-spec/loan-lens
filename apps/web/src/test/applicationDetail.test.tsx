import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { MODE_STORAGE_KEY } from "../services/apiClient";
import { calculateFoir } from "../services/foir";
import { resetMockStore, setMockRole } from "../services/mockData";
import { renderWithProviders } from "./testUtils";

const DRAFT_REF = "LL-C7B2E5F04D19";
const SUBMITTED_REF = "LL-9A71C04E2B58";

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
  setMockRole("loan_officer");
  stubUnauthenticatedApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("application detail page", () => {
  it("lets a loan officer edit and save a draft", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: `/applications/${DRAFT_REF}` });

    await screen.findByTestId("application-detail-page");
    expect(screen.getByTestId("application-detail-name")).toHaveTextContent("Satyabrata Swain");
    expect(screen.getByTestId("detail-applicant")).not.toBeDisabled();

    const applicant = screen.getByTestId("detail-applicant");
    await user.clear(applicant);
    await user.type(applicant, "Satyabrata Kumar Swain");

    const notes = screen.getByTestId("detail-notes");
    await user.clear(notes);
    await user.type(notes, "Updated assessment notes for draft save.");

    await user.click(screen.getByTestId("save-draft"));

    expect(await screen.findByText(`Draft ${DRAFT_REF} saved`)).toBeInTheDocument();
    expect(screen.getByTestId("detail-applicant")).toHaveValue("Satyabrata Kumar Swain");
    expect(screen.getByTestId("detail-notes")).toHaveValue(
      "Updated assessment notes for draft save.",
    );
  });

  it("recalculates FOIR live when amount or income changes", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: `/applications/${DRAFT_REF}` });
    await screen.findByTestId("live-credit-foir");

    const initial = calculateFoir({
      amount: 1_200_000,
      monthlyNetIncome: 110_000,
      existingMonthlyEmis: 0,
      tenureMonths: 36,
    });
    expect(screen.getByTestId("live-credit-foir")).toHaveTextContent(
      `805 CIBIL • ${Math.round(initial.foirPct)}% FOIR`,
    );
    expect(screen.getByTestId("application-tenor-metric")).toHaveTextContent(/36 mo/);
    expect(screen.getByTestId("detail-gross-income")).toHaveValue(140_000);
    expect(screen.getByTestId("detail-income")).toHaveValue(110_000);

    const income = screen.getByTestId("detail-income");
    await user.clear(income);
    await user.type(income, "90000");

    const updated = calculateFoir({
      amount: 1_200_000,
      monthlyNetIncome: 90_000,
      existingMonthlyEmis: 0,
      tenureMonths: 36,
    });
    await waitFor(() => {
      expect(screen.getByTestId("live-credit-foir")).toHaveTextContent(
        `805 CIBIL • ${Math.round(updated.foirPct)}% FOIR`,
      );
    });
    expect(Math.round(updated.foirPct)).not.toBe(Math.round(initial.foirPct));
  });

  it("discards a draft with DRAFT_DISCARDED audit and keeps the row", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: `/applications/${DRAFT_REF}` });
    await screen.findByTestId("discard-draft");

    await user.click(screen.getByTestId("discard-draft"));

    expect(await screen.findByText(`Draft ${DRAFT_REF} discarded`)).toBeInTheDocument();
    expect(
      within(screen.getByTestId("application-detail-page")).getByText("Discarded"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("discard-draft")).not.toBeInTheDocument();
    expect(screen.queryByTestId("save-draft")).not.toBeInTheDocument();

    const ledger = screen.getByTestId("application-audit-ledger");
    expect(within(ledger).getByText(/Draft · Discarded/i)).toBeInTheDocument();
  });

  it("shows an immutable-history hint for completed applications", async () => {
    renderWithProviders(<App />, { route: `/applications/${SUBMITTED_REF}` });
    const hint = await screen.findByTestId("immutable-history-hint");
    expect(hint).toHaveAttribute(
      "title",
      "Regulatory compliance requires an immutable audit history; completed applications cannot be deleted.",
    );
    expect(screen.queryByTestId("discard-draft")).not.toBeInTheDocument();
  });

  it("submits a draft to the reviewer and transitions status to Submitted", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: `/applications/${DRAFT_REF}` });
    await screen.findByTestId("submit-to-reviewer");

    await user.click(screen.getByTestId("submit-to-reviewer"));

    expect(await screen.findByText(`Submitted ${DRAFT_REF} to reviewer`)).toBeInTheDocument();
    expect(screen.getByText("Submitted")).toBeInTheDocument();
    expect(screen.getByTestId("detail-applicant")).toBeDisabled();
    expect(screen.getByTestId("readonly-helper")).toBeInTheDocument();
  });

  it("requires a reviewer note of at least 10 characters before decide actions enable", async () => {
    const user = userEvent.setup();
    setMockRole("reviewer");
    renderWithProviders(<App />, { route: `/applications/${SUBMITTED_REF}` });

    await screen.findByTestId("detail-reviewer-note");
    expect(screen.getByTestId("approve-loan")).toBeDisabled();
    expect(screen.getByTestId("reject-application")).toBeDisabled();
    expect(screen.getByTestId("reviewer-note-hint")).toBeInTheDocument();

    await user.type(screen.getByTestId("detail-reviewer-note"), "Too short");
    expect(screen.getByTestId("approve-loan")).toBeDisabled();

    await user.clear(screen.getByTestId("detail-reviewer-note"));
    await user.type(
      screen.getByTestId("detail-reviewer-note"),
      "Within policy after income verification.",
    );

    expect(screen.getByTestId("approve-loan")).not.toBeDisabled();
    expect(screen.getByTestId("reject-application")).not.toBeDisabled();
  });

  it("approves and rejects submitted applications with a valid reviewer note", async () => {
    const user = userEvent.setup();
    setMockRole("reviewer");
    renderWithProviders(<App />, { route: `/applications/${SUBMITTED_REF}` });
    await screen.findByTestId("approve-loan");

    await user.type(
      screen.getByTestId("detail-reviewer-note"),
      "FOIR within threshold; income credits verified.",
    );
    await user.click(screen.getByTestId("approve-loan"));

    expect(await screen.findByText(`Approved ${SUBMITTED_REF}`)).toBeInTheDocument();
    expect(within(screen.getByTestId("application-detail-page")).getByText("Approved")).toBeInTheDocument();
  });

  it("rejects with a valid note from the reviewer workspace entry point", async () => {
    const user = userEvent.setup();
    setMockRole("reviewer");
    // Re-seed so the submitted app is available again after the previous approval test's store mutation.
    await resetMockStore();
    setMockRole("reviewer");

    renderWithProviders(<App />, { route: "/reviewer" });
    await screen.findByTestId("reviewer-underwriting-queue");
    await user.click(screen.getByRole("link", { name: "Review" }));

    await screen.findByTestId("application-detail-page");
    expect(screen.getByTestId("application-detail-ref")).toHaveTextContent(SUBMITTED_REF);

    await user.type(
      screen.getByTestId("detail-reviewer-note"),
      "Existing obligations push FOIR above policy.",
    );
    await user.click(screen.getByTestId("reject-application"));

    expect(await screen.findByText(`Rejected ${SUBMITTED_REF}`)).toBeInTheDocument();
    expect(within(screen.getByTestId("application-detail-page")).getByText("Rejected")).toBeInTheDocument();
  });

  it("renders application-scoped audit events with actor and hash", async () => {
    renderWithProviders(<App />, { route: `/applications/${DRAFT_REF}` });
    const ledger = await screen.findByTestId("application-audit-ledger");

    expect(within(ledger).getByRole("heading", { name: "Application State History" })).toBeInTheDocument();
    expect(within(ledger).getByText(/Application · Created/i)).toBeInTheDocument();
    expect(within(ledger).getByText(/Rakesh Panda \(Loan Officer\)/)).toBeInTheDocument();

    const hash = within(ledger).getByTitle(/^[0-9a-f]{64}$/i);
    expect(hash).toHaveTextContent(/^[0-9a-f]{64}$/i);
  });
});

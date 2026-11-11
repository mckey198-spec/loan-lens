import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { makeUser, renderWithProviders } from "./testUtils";

const draftApp = {
  id: "app-1",
  reference_code: "LL-001",
  status: "draft",
  created_by_user_id: "loan_officer-id",
  assigned_reviewer_user_id: null,
  applicant_type: "salaried",
  requested_amount: "500000.00",
  currency: "INR",
  tenure_months: 36,
  loan_purpose: "personal",
  loan_purpose_other: null,
  applicable_policy_version_id: null,
  withdraw_reason: null,
  submitted_at: null,
  archived_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  applicant: {
    id: "applicant-1",
    application_id: "app-1",
    full_name: "Ada Officer",
    date_of_birth: "1990-01-15",
    pan: "ABCDE1234F",
    current_address: "12 Example Road",
    employer_or_business_name: "Example Co",
    time_in_job_or_business_months: 24,
    gross_monthly_income: "100000.00",
    net_monthly_income: "80000.00",
    other_monthly_income: null,
    other_monthly_income_evidenced: false,
    monthly_household_expenses: null,
    total_existing_monthly_emis: "5000.00",
    outstanding_existing_loan_amount: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("officer intake", () => {
  it("disables submit until a document exists, then submits", async () => {
    const user = userEvent.setup();
    let documents: unknown[] = [];
    let submitted = false;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = (init?.method ?? "GET").toUpperCase();

        if (url.includes("/api/v1/auth/me")) {
          return new Response(JSON.stringify(makeUser("loan_officer")), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.endsWith("/api/v1/applications/app-1") && method === "GET") {
          return new Response(JSON.stringify(draftApp), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.endsWith("/documents") && method === "GET") {
          return new Response(JSON.stringify(documents), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.endsWith("/documents") && method === "POST") {
          const doc = {
            id: "doc-1",
            application_id: "app-1",
            content_sha256: "abc",
            document_type: "pan_card",
            status: "uploaded",
            original_filename: "pan.pdf",
            mime_type: "application/pdf",
            byte_size: 1024,
            is_alternative_evidence: false,
            uploaded_by_user_id: "loan_officer-id",
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          };
          documents = [doc];
          return new Response(JSON.stringify(doc), {
            status: 201,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.endsWith("/api/v1/applications/app-1") && method === "PATCH") {
          return new Response(JSON.stringify(draftApp), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.endsWith("/submit") && method === "POST") {
          submitted = true;
          return new Response(
            JSON.stringify({ ...draftApp, status: "submitted" }),
            {
              status: 200,
              headers: { "Content-Type": "application/json" },
            },
          );
        }
        return new Response(
          JSON.stringify({ code: "NOT_MOCKED", message: url }),
          { status: 500, headers: { "Content-Type": "application/json" } },
        );
      }),
    );

    sessionStorage.setItem("loanlens_csrf_token", "test-csrf");

    renderWithProviders(<App />, {
      route: "/officer/applications/app-1",
    });

    await waitFor(() => {
      expect(screen.getByTestId("officer-intake-form")).toBeInTheDocument();
    });

    const submitBtn = screen.getByTestId("submit-application-btn");
    expect(submitBtn).toBeDisabled();

    const file = new File(["%PDF"], "pan.pdf", { type: "application/pdf" });
    await user.upload(screen.getByTestId("document-file-input"), file);

    await waitFor(() => {
      expect(screen.getByText("pan.pdf")).toBeInTheDocument();
      expect(submitBtn).not.toBeDisabled();
    });

    await user.click(submitBtn);

    await waitFor(() => {
      expect(submitted).toBe(true);
      expect(
        screen.getByText(/Application submitted for processing/i),
      ).toBeInTheDocument();
    });
  });

  it("does not render claim or decide actions for officers", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/v1/auth/me")) {
          return new Response(JSON.stringify(makeUser("loan_officer")), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.includes("/applications/app-1/assessment/export")) {
          return new Response(new Blob(["pdf"]), { status: 200 });
        }
        if (url.includes("/applications/app-1")) {
          if (url.includes("/assessments")) {
            return new Response(JSON.stringify({ items: [], total: 0 }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          }
          if (
            url.includes("/calculations") ||
            url.includes("/verification") ||
            url.includes("/observations")
          ) {
            return new Response(
              JSON.stringify({ code: "NOT_FOUND", message: "missing" }),
              { status: 404, headers: { "Content-Type": "application/json" } },
            );
          }
          return new Response(
            JSON.stringify({ ...draftApp, status: "review_completed" }),
            {
              status: 200,
              headers: { "Content-Type": "application/json" },
            },
          );
        }
        return new Response("{}", { status: 404 });
      }),
    );

    renderWithProviders(<App />, {
      route: "/officer/applications/app-1/assessment",
    });

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Assessment (read-only)" }),
      ).toBeInTheDocument();
    });
    expect(screen.getByTestId("download-pdf-btn")).toBeInTheDocument();
    expect(screen.getByTestId("br12-advisory-banner")).toHaveTextContent(
      "ADVISORY ONLY — NOT A CREDIT DECISION",
    );
    expect(
      screen.queryByRole("button", { name: /Claim Application/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("submit-final-decision-btn"),
    ).not.toBeInTheDocument();
  });
});

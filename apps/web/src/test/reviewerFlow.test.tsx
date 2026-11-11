import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { makeUser, renderWithProviders } from "./testUtils";

const reviewApp = {
  id: "app-2",
  reference_code: "LL-002",
  status: "awaiting_human_review",
  created_by_user_id: "loan_officer-id",
  assigned_reviewer_user_id: null,
  applicant_type: "salaried",
  requested_amount: "400000.00",
  currency: "INR",
  tenure_months: 24,
  loan_purpose: "personal",
  loan_purpose_other: null,
  applicable_policy_version_id: "policy-1",
  withdraw_reason: null,
  submitted_at: "2026-01-02T00:00:00Z",
  archived_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
  applicant: {
    id: "applicant-2",
    application_id: "app-2",
    full_name: "Rita Review",
    date_of_birth: "1988-05-20",
    pan: "XYZAB9876C",
    current_address: "99 Review Lane",
    employer_or_business_name: "Review Corp",
    time_in_job_or_business_months: 36,
    gross_monthly_income: "90000.00",
    net_monthly_income: "70000.00",
    other_monthly_income: null,
    other_monthly_income_evidenced: false,
    monthly_household_expenses: null,
    total_existing_monthly_emis: "2000.00",
    outstanding_existing_loan_amount: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
};

const assessment = {
  id: "assess-1",
  application_id: "app-2",
  processing_run_id: "run-1",
  input_snapshot_id: "snap-1",
  applicable_policy_version_id: "policy-1",
  assigned_reviewer_id: "reviewer-id",
  recommendation: null,
  reviewer_notes: null,
  decided_at: null,
  created_at: "2026-01-02T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};

const finding = {
  id: "finding-1",
  rule_code: "BR-02",
  rule_name: "PAN match",
  status: "flagged",
  severity: "medium",
  message: "PAN mismatch across sources",
  diff_payload: {},
  provenance_sources: [],
  created_at: "2026-01-02T00:00:00Z",
};

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("reviewer claim, disposition, and decision", () => {
  it("claims an application, records disposition, and submits decision", async () => {
    const user = userEvent.setup();
    let appStatus = "awaiting_human_review";
    let currentAssessment = { ...assessment, assigned_reviewer_id: null as string | null };
    let dispositionCount = 0;
    let decided = false;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = (init?.method ?? "GET").toUpperCase();

        if (url.includes("/api/v1/auth/me")) {
          return json(makeUser("reviewer"));
        }
        if (url.includes("/api/v1/applications?") || url.endsWith("/api/v1/applications")) {
          const status = new URL(url, "http://local").searchParams.get("status");
          const items =
            status === appStatus || (!status && appStatus)
              ? [{ ...reviewApp, status: appStatus }]
              : status === "in_review" && appStatus === "in_review"
                ? [{ ...reviewApp, status: "in_review" }]
                : [];
          if (status === "awaiting_human_review") {
            return json({
              items:
                appStatus === "awaiting_human_review"
                  ? [{ ...reviewApp, status: appStatus }]
                  : [],
              offset: 0,
              limit: 100,
              total: appStatus === "awaiting_human_review" ? 1 : 0,
            });
          }
          if (status === "in_review") {
            return json({
              items:
                appStatus === "in_review"
                  ? [{ ...reviewApp, status: "in_review" }]
                  : [],
              offset: 0,
              limit: 100,
              total: appStatus === "in_review" ? 1 : 0,
            });
          }
          return json({ items, offset: 0, limit: 100, total: items.length });
        }
        if (url.endsWith("/claim") && method === "POST") {
          appStatus = "in_review";
          currentAssessment = {
            ...assessment,
            assigned_reviewer_id: "reviewer-id",
          };
          return json(currentAssessment, 201);
        }
        if (url.includes("/applications/app-2") && method === "GET") {
          if (url.includes("/assessments")) {
            return json({
              items: currentAssessment.assigned_reviewer_id
                ? [currentAssessment]
                : [],
              total: currentAssessment.assigned_reviewer_id ? 1 : 0,
            });
          }
          if (url.includes("/documents")) {
            return json([]);
          }
          if (url.includes("/verification")) {
            return json({
              id: "ver-1",
              application_id: "app-2",
              processing_run_id: "run-1",
              total_rules_evaluated: 11,
              passed_count: 10,
              flagged_count: 1,
              failed_count: 0,
              created_at: "2026-01-02T00:00:00Z",
              findings: [finding],
            });
          }
          if (url.includes("/calculations")) {
            return json({
              id: "calc-1",
              application_id: "app-2",
              processing_run_id: "run-1",
              input_snapshot_id: "snap-1",
              proposed_emi: "15000.00",
              total_obligations: "17000.00",
              foir_pct: "24.00",
              foir_threshold_pct: "50.00",
              foir_passed: true,
              net_disposable_income: "53000.00",
              max_qualifying_loan_amount: "900000.00",
              calculation_details: {},
              created_at: "2026-01-02T00:00:00Z",
            });
          }
          if (url.includes("/observations")) {
            return json({
              id: "obs-1",
              application_id: "app-2",
              processing_run_id: "run-1",
              model_id: "synthetic-model",
              observation_text: "Income pattern looks consistent.",
              risk_factors: ["Thin credit file"],
              strengths: ["Stable tenure"],
              cited_chunk_ids: [],
              is_ai_generated: true,
              created_at: "2026-01-02T00:00:00Z",
            });
          }
          if (url.includes("/policy-citations")) {
            return json({
              application_id: "app-2",
              policy_version_id: "policy-1",
              citations: {
                foir: [
                  {
                    chunk_id: "chunk-1",
                    section_label: "FOIR limits",
                    content_text: "FOIR shall not exceed configured threshold.",
                    rank_score: 0.91,
                  },
                ],
              },
            });
          }
          return json({ ...reviewApp, status: appStatus });
        }
        if (url.includes("/dispositions") && method === "POST") {
          dispositionCount += 1;
          return json({ ok: true }, 201);
        }
        if (url.includes("/decide") && method === "POST") {
          decided = true;
          currentAssessment = {
            ...currentAssessment,
            recommendation: "approve",
            reviewer_notes: "Affordability and identity checks are acceptable",
            decided_at: "2026-01-03T00:00:00Z",
          };
          appStatus = "review_completed";
          return json(currentAssessment);
        }
        return json({ code: "NOT_MOCKED", message: url }, 500);
      }),
    );

    sessionStorage.setItem("loanlens_csrf_token", "test-csrf");

    renderWithProviders(<App />, {
      route: "/reviewer/queue",
    });

    await waitFor(() => {
      expect(screen.getByTestId("review-queue")).toBeInTheDocument();
    });

    expect(
      screen.queryByRole("button", { name: "New application" }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByTestId("claim-btn-app-2"));

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Underwriting dashboard" }),
      ).toBeInTheDocument();
    });

    expect(screen.getByTestId("br12-advisory-banner")).toBeInTheDocument();
    expect(screen.getByTestId("is-ai-generated-indicator")).toHaveTextContent(
      "is_ai_generated: true",
    );
    expect(screen.getByTestId("financial-ratios-card")).toHaveTextContent("EMI");

    const decisionBtn = screen.getByTestId("submit-final-decision-btn");
    expect(decisionBtn).toBeDisabled();

    await user.click(screen.getByTestId("add-disposition-BR-02"));
    const justification = screen.getByTestId("disposition-justification");
    await user.type(justification, "short");
    expect(screen.getByTestId("disposition-submit-btn")).toBeDisabled();
    await user.clear(justification);
    await user.type(
      justification,
      "Reviewed PAN evidence; accepted residual mismatch risk",
    );
    await user.click(screen.getByTestId("disposition-submit-btn"));

    await waitFor(() => {
      expect(dispositionCount).toBe(1);
      expect(screen.getByText(/Disposition recorded/i)).toBeInTheDocument();
    });

    await user.type(
      screen.getByTestId("decision-notes"),
      "Affordability and identity checks are acceptable",
    );
    expect(decisionBtn).not.toBeDisabled();
    await user.click(decisionBtn);

    await waitFor(() => {
      expect(decided).toBe(true);
      expect(screen.getByText(/Final decision submitted/i)).toBeInTheDocument();
    });
  });
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

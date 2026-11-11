import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { makeUser, renderWithProviders } from "./testUtils";

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("role-based route protection", () => {
  it("redirects unauthenticated users to login", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            code: "AUTH_UNAUTHORIZED",
            message: "Not authenticated",
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    renderWithProviders(<App />, { route: "/officer/applications" });
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "LoanLens" }),
      ).toBeInTheDocument();
    });
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });

  it("redirects reviewer away from /officer/*", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/v1/auth/me")) {
          return new Response(JSON.stringify(makeUser("reviewer")), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.includes("/api/v1/applications")) {
          return new Response(
            JSON.stringify({ items: [], offset: 0, limit: 50, total: 0 }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response("{}", { status: 404 });
      }),
    );

    const { container } = renderWithProviders(<App />, {
      route: "/officer/applications",
    });

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Reviewer workspace" }),
      ).toBeInTheDocument();
    });
    expect(
      container.querySelector('[data-testid="officer-application-list"]'),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "New application" }),
    ).not.toBeInTheDocument();
  });

  it("redirects loan officer away from /reviewer/*", async () => {
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
        if (url.includes("/api/v1/applications")) {
          return new Response(
            JSON.stringify({ items: [], offset: 0, limit: 50, total: 0 }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response("{}", { status: 404 });
      }),
    );

    renderWithProviders(<App />, { route: "/reviewer/queue" });

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Loan Officer workspace" }),
      ).toBeInTheDocument();
    });
    expect(screen.queryByTestId("review-queue")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Claim Application/i }),
    ).not.toBeInTheDocument();
  });
});

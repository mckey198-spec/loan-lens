import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { MODE_STORAGE_KEY } from "../services/apiClient";
import { resetMockStore, setMockRole } from "../services/mockData";
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

function summaryCards() {
  return within(screen.getByTestId("reviewer-summary-cards"));
}

function queueTable() {
  return within(screen.getByTestId("reviewer-underwriting-queue"));
}

beforeEach(async () => {
  localStorage.clear();
  localStorage.setItem(MODE_STORAGE_KEY, "demo");
  await resetMockStore();
  setMockRole("reviewer");
  stubUnauthenticatedApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("reviewer workspace", () => {
  it("calculates and displays summary card counts from seeded demo applications", async () => {
    renderWithProviders(<App />, { route: "/reviewer" });
    await screen.findByTestId("reviewer-underwriting-queue");

    const cards = summaryCards();
    expect(cards.getByText("Awaiting Decision")).toBeInTheDocument();
    expect(cards.getByText("Submitted by Loan Officers")).toBeInTheDocument();
    expect(cards.getByText("Approved Today")).toBeInTheDocument();
    expect(cards.getByText("Audit hash verified")).toBeInTheDocument();
    expect(cards.getByText("Returned / Rejected")).toBeInTheDocument();
    expect(cards.getByText("Policy threshold check")).toBeInTheDocument();

    // Seeded demo: 1 Submitted, 1 Approved, 1 Rejected
    expect(cards.getAllByText("1 applications")).toHaveLength(3);
    expect(screen.getByTestId("pending-review-badge")).toHaveTextContent("Pending review: 1");
  });

  it("lists applications and shows Review for submitted ones", async () => {
    renderWithProviders(<App />, { route: "/reviewer" });
    await screen.findByTestId("reviewer-underwriting-queue");

    const table = queueTable();
    expect(table.getByText("Pratibha Rani Pattanayak")).toBeInTheDocument();
    expect(table.getByText("Biswajit Nayak")).toBeInTheDocument();
    expect(table.getByText("Kalyani Devi Behera")).toBeInTheDocument();
    expect(table.getByText("Satyabrata Swain")).toBeInTheDocument();

    const reviewLink = table.getByRole("link", { name: "Review" });
    expect(reviewLink).toHaveAttribute(
      "href",
      "/applications/LL-9A71C04E2B58",
    );

    const openLinks = table.getAllByRole("link", { name: "Open" });
    expect(openLinks).toHaveLength(3);
  });

  it("navigates to the unified application detail route when Review is clicked", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/reviewer" });
    await screen.findByTestId("reviewer-underwriting-queue");

    await user.click(queueTable().getByRole("link", { name: "Review" }));

    expect(screen.queryByTestId("reviewer-workspace")).not.toBeInTheDocument();
    expect(await screen.findByTestId("application-detail-page")).toBeInTheDocument();
    expect(screen.getByTestId("application-detail-ref")).toHaveTextContent("LL-9A71C04E2B58");
    expect(screen.getByTestId("application-detail-name")).toHaveTextContent("Biswajit Nayak");
  });
});


import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { MODE_STORAGE_KEY } from "../services/apiClient";
import { resetMockStore } from "../services/mockData";
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

describe("officer workspace", () => {
  it("renders all four seeded applications in demo mode", async () => {
    renderWithProviders(<App />, { route: "/officer/applications" });
    await screen.findByTestId("officer-application-list");

    const table = applicationTable();
    expect(await table.findByText("Pratibha Rani Pattanayak")).toBeInTheDocument();
    expect(table.getByText("Biswajit Nayak")).toBeInTheDocument();
    expect(table.getByText("Kalyani Devi Behera")).toBeInTheDocument();
    expect(table.getByText("Satyabrata Swain")).toBeInTheDocument();
    expect(table.getAllByRole("row")).toHaveLength(5);
  });

  it("shows dynamic counts on status filter pills", async () => {
    renderWithProviders(<App />, { route: "/officer/applications" });
    await screen.findByTestId("officer-application-list");

    expect(screen.getByRole("button", { name: "All (4)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Draft (1)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submitted (1)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approved (1)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rejected (1)" })).toBeInTheDocument();
  });

  it("filters rows when a status pill is selected", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/officer/applications" });
    await screen.findByTestId("officer-application-list");

    await user.click(screen.getByRole("button", { name: "Draft (1)" }));

    const table = applicationTable();
    expect(table.getAllByRole("row")).toHaveLength(2);
    expect(table.getByText("Satyabrata Swain")).toBeInTheDocument();
    expect(table.queryByText("Pratibha Rani Pattanayak")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Approved (1)" }));

    expect(table.getAllByRole("row")).toHaveLength(2);
    expect(table.getByText("Pratibha Rani Pattanayak")).toBeInTheDocument();
    expect(table.queryByText("Satyabrata Swain")).not.toBeInTheDocument();
  });

  it("filters by applicant name and reference ID", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/officer/applications" });
    await screen.findByTestId("officer-application-list");

    const search = screen.getByTestId("officer-application-search");

    await user.type(search, "Pratibha");

    const table = applicationTable();
    expect(table.getAllByRole("row")).toHaveLength(2);
    expect(table.getByText("Pratibha Rani Pattanayak")).toBeInTheDocument();

    await user.clear(search);
    await user.type(search, "LL-5284DB1327C6");

    expect(table.getAllByRole("row")).toHaveLength(2);
    expect(table.getByText("LL-5284DB1327C6")).toBeInTheDocument();
  });
});

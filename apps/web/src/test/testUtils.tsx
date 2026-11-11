import { render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import type { UserProfile, UserRole } from "../api/types";
import { AuthProvider } from "../auth/AuthProvider";

export function makeUser(role: UserRole): UserProfile {
  return {
    id: `${role}-id`,
    email: `${role}@example.com`,
    full_name: `Test ${role}`,
    role,
    is_active: true,
  };
}

export function renderWithProviders(
  ui: ReactElement,
  { route = "/" }: { route?: string } = {},
) {
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <MemoryRouter initialEntries={[route]}>
        <AuthProvider>{children}</AuthProvider>
      </MemoryRouter>
    );
  }

  return render(ui, { wrapper: Wrapper });
}

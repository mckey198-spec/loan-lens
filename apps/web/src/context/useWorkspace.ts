import { createContext, useContext } from "react";

import type { DataMode } from "../services/apiClient";
import type { SessionUser, UserRole } from "../types/domain";

export type Workspace = "officer" | "reviewer" | "admin";

export const WORKSPACE_HOME: Readonly<Record<Workspace, string>> = {
  officer: "/officer/applications",
  reviewer: "/reviewer",
  admin: "/admin",
};

export const ROLE_LABELS: Readonly<Record<UserRole, string>> = {
  loan_officer: "Loan Officer",
  reviewer: "Reviewer",
  admin: "Admin",
};

const PRIMARY_WORKSPACE: Readonly<Record<UserRole, Workspace>> = {
  loan_officer: "officer",
  reviewer: "reviewer",
  admin: "admin",
};

export function primaryWorkspaceFor(role: UserRole): Workspace {
  return PRIMARY_WORKSPACE[role];
}

export function homePathFor(role: UserRole): string {
  return WORKSPACE_HOME[PRIMARY_WORKSPACE[role]];
}

export function workspaceForPath(pathname: string): Workspace | null {
  if (pathname.startsWith("/officer")) {
    return "officer";
  }
  if (pathname.startsWith("/reviewer")) {
    return "reviewer";
  }
  if (pathname.startsWith("/admin")) {
    return "admin";
  }
  return null;
}

export interface WorkspaceContextValue {
  activeWorkspace: Workspace;
  dataMode: DataMode;
  /** Demo: the simulated persona. Live: the `/api/v1/auth/me` session, or null. */
  currentUser: SessionUser | null;
  loading: boolean;
  /** Increments whenever mode, persona, or demo data changes so views can remount. */
  revision: number;
  switchRole: (role: UserRole) => void;
  toggleDataMode: (mode: DataMode) => void;
  openWorkspace: (workspace: Workspace) => void;
  resetDemoData: () => Promise<void>;
  /** Bump `revision` so workspace views remount and refetch. */
  refreshData: () => void;
}

export const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) {
    throw new Error("useWorkspace must be used within WorkspaceProvider");
  }
  return ctx;
}

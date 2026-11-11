import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import type { UserRole } from "../api/types";
import { homePathFor, useWorkspace } from "../context/useWorkspace";

interface RequireRoleProps {
  roles: UserRole[];
  children: ReactNode;
}

/**
 * Client-side route guard. Authorization remains on the API (AGENT_RULES §8).
 */
export function RequireRole({ roles, children }: RequireRoleProps) {
  const { currentUser, loading } = useWorkspace();
  const location = useLocation();

  if (loading) {
    return <div className="page-loading">Checking session…</div>;
  }

  if (!currentUser) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  if (!roles.includes(currentUser.role)) {
    return <Navigate to={homePathFor(currentUser.role)} replace />;
  }

  return <>{children}</>;
}

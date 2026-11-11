import { useCallback, useMemo, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthProvider";
import { getDataMode, setDataMode, type DataMode } from "../services/apiClient";
import { getMockSession, resetMockStore, setMockRole } from "../services/mockData";
import type { SessionUser, UserRole } from "../types/domain";
import {
  WORKSPACE_HOME,
  WorkspaceContext,
  homePathFor,
  primaryWorkspaceFor,
  workspaceForPath,
  type Workspace,
  type WorkspaceContextValue,
} from "./useWorkspace";

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { user: liveProfile, loading: liveLoading, refresh } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [dataMode, setDataModeState] = useState<DataMode>(getDataMode);
  const [demoUser, setDemoUser] = useState<SessionUser>(getMockSession);
  const [revision, setRevision] = useState(0);

  const liveUser = useMemo<SessionUser | null>(
    () =>
      liveProfile
        ? {
            id: liveProfile.id,
            name: liveProfile.full_name,
            email: liveProfile.email,
            role: liveProfile.role,
          }
        : null,
    [liveProfile],
  );

  const currentUser = dataMode === "demo" ? demoUser : liveUser;
  const loading = dataMode === "live" && liveLoading;
  const activeWorkspace: Workspace =
    workspaceForPath(pathname) ??
    (currentUser ? primaryWorkspaceFor(currentUser.role) : "officer");

  const switchRole = useCallback(
    (role: UserRole) => {
      if (dataMode !== "demo") {
        return;
      }
      setDemoUser(setMockRole(role));
      setRevision((r) => r + 1);
      navigate(homePathFor(role));
    },
    [dataMode, navigate],
  );

  const toggleDataMode = useCallback(
    (mode: DataMode) => {
      if (mode === dataMode) {
        return;
      }
      setDataMode(mode);
      setDataModeState(mode);
      if (mode === "demo") {
        setDemoUser(getMockSession());
      } else {
        void refresh();
      }
      setRevision((r) => r + 1);
    },
    [dataMode, refresh],
  );

  const openWorkspace = useCallback(
    (workspace: Workspace) => {
      navigate(WORKSPACE_HOME[workspace]);
    },
    [navigate],
  );

  const resetDemoData = useCallback(async () => {
    if (dataMode !== "demo") {
      return;
    }
    const role = demoUser.role;
    await resetMockStore();
    // resetMockStore() restores the default persona; keep the simulated one.
    setDemoUser(setMockRole(role));
    setRevision((r) => r + 1);
  }, [dataMode, demoUser.role]);

  const refreshData = useCallback(() => {
    setRevision((r) => r + 1);
  }, []);

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      activeWorkspace,
      dataMode,
      currentUser,
      loading,
      revision,
      switchRole,
      toggleDataMode,
      openWorkspace,
      resetDemoData,
      refreshData,
    }),
    [
      activeWorkspace,
      dataMode,
      currentUser,
      loading,
      revision,
      switchRole,
      toggleDataMode,
      openWorkspace,
      resetDemoData,
      refreshData,
    ],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useAuth } from "../../auth/AuthProvider";
import {
  ROLE_LABELS,
  WORKSPACE_HOME,
  useWorkspace,
  type Workspace,
} from "../../context/useWorkspace";
import type { DataMode } from "../../services/apiClient";
import type { UserRole } from "../../types/domain";
import { DemoAccountModal } from "../modals/DemoAccountModal";

const WORKSPACE_LINKS: ReadonlyArray<{
  workspace: Workspace;
  label: string;
  roles: readonly UserRole[];
}> = [
  { workspace: "officer", label: "Officer", roles: ["loan_officer", "admin"] },
  { workspace: "reviewer", label: "Reviewer", roles: ["reviewer", "admin"] },
  { workspace: "admin", label: "Admin", roles: ["admin"] },
];

const DATA_MODES: ReadonlyArray<{ mode: DataMode; label: string }> = [
  { mode: "live", label: "Live API" },
  { mode: "demo", label: "Demo Showcase" },
];

const SIMULATED_ROLES: readonly UserRole[] = ["loan_officer", "reviewer", "admin"];

export function TopNavBar() {
  const { logout } = useAuth();
  const { activeWorkspace, dataMode, currentUser, switchRole, toggleDataMode } = useWorkspace();
  const navigate = useNavigate();
  const [accountModalOpen, setAccountModalOpen] = useState(false);

  const visibleLinks = WORKSPACE_LINKS.filter(
    (link) => currentUser !== null && link.roles.includes(currentUser.role),
  );

  async function handleLogout(): Promise<void> {
    if (dataMode === "demo") {
      setAccountModalOpen(true);
      return;
    }
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <header className="top-nav">
      <Link to={WORKSPACE_HOME[activeWorkspace]} className="brand">
        LoanLens
      </Link>

      <nav className="top-nav-links" aria-label="Workspaces">
        {visibleLinks.map(({ workspace, label }) => {
          const active = workspace === activeWorkspace;
          return (
            <Link
              key={workspace}
              to={WORKSPACE_HOME[workspace]}
              className={active ? "top-nav-link top-nav-link--active" : "top-nav-link"}
              aria-current={active ? "page" : undefined}
            >
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="top-nav-controls">
        <div className="pill" role="group" aria-label="Data mode">
          {DATA_MODES.map(({ mode, label }) => (
            <button
              key={mode}
              type="button"
              className="pill-option"
              aria-pressed={dataMode === mode}
              onClick={() => toggleDataMode(mode)}
            >
              {label}
            </button>
          ))}
        </div>

        {dataMode === "demo" && (
          <div className="pill pill--simulator" role="group" aria-label="Role simulator">
            {SIMULATED_ROLES.map((role) => (
              <button
                key={role}
                type="button"
                className="pill-option"
                aria-pressed={currentUser?.role === role}
                onClick={() => switchRole(role)}
              >
                {ROLE_LABELS[role]}
              </button>
            ))}
          </div>
        )}

        {currentUser && (
          <span className="user-badge" data-testid="user-badge">
            <span className="user-badge-name">{currentUser.name}</span>{" "}
            <span className="muted">({ROLE_LABELS[currentUser.role]})</span>
          </span>
        )}

        {currentUser && (
          <button type="button" className="btn btn-ghost" onClick={() => void handleLogout()}>
            Log out
          </button>
        )}
      </div>

      {accountModalOpen && currentUser && (
        <DemoAccountModal
          activeRole={currentUser.role}
          onSelect={(role) => {
            setAccountModalOpen(false);
            switchRole(role);
          }}
          onClose={() => setAccountModalOpen(false)}
        />
      )}
    </header>
  );
}

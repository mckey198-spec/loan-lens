import { useEffect, useState } from "react";

import { useWorkspace, type Workspace } from "../../context/useWorkspace";
import type { Application } from "../../types/domain";
import { ArchitectureSpecModal } from "../modals/ArchitectureSpecModal";
import { NewApplicationModal } from "../modals/NewApplicationModal";

const TOAST_DURATION_MS = 3500;

const WORKSPACE_EXPLAINERS: Readonly<Record<Workspace, string>> = {
  officer:
    "Loan Officer Workspace: Create new loan applications, edit drafts, and submit applications to the Reviewer queue.",
  reviewer:
    "Reviewer Workspace: Evaluate submitted applications, verify risk rules & debt ratios, and issue final credit decisions.",
  admin:
    "Admin Workspace: Manage operator roles, verify the SHA-256 audit ledger, and monitor system health.",
};

interface Toast {
  tone: "success" | "error";
  message: string;
}

function canCreateApplication(role: string | undefined): boolean {
  return role === "loan_officer" || role === "admin";
}

export function RoleContextBar() {
  const { activeWorkspace, dataMode, currentUser, resetDemoData, refreshData } = useWorkspace();
  const [specOpen, setSpecOpen] = useState(false);
  const [newAppOpen, setNewAppOpen] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [toast, setToast] = useState<Toast | null>(null);

  useEffect(() => {
    if (!toast) {
      return;
    }
    const timer = window.setTimeout(() => setToast(null), TOAST_DURATION_MS);
    return () => window.clearTimeout(timer);
  }, [toast]);

  async function handleReset(): Promise<void> {
    setResetting(true);
    try {
      await resetDemoData();
      setToast({ tone: "success", message: "Demo data reset to the seeded showcase dataset." });
    } catch (err) {
      setToast({
        tone: "error",
        message: err instanceof Error ? err.message : "Demo data reset failed.",
      });
    } finally {
      setResetting(false);
    }
  }

  function handleCreated(application: Application): void {
    setToast({ tone: "success", message: `Created draft ${application.ref}` });
    refreshData();
  }

  return (
    <div className="role-context-bar">
      <p className="role-context-text">
        <span
          className={dataMode === "demo" ? "status-dot status-dot--pulse" : "status-dot"}
          aria-hidden="true"
        />
        {WORKSPACE_EXPLAINERS[activeWorkspace]}
      </p>

      <div className="role-context-actions">
        {canCreateApplication(currentUser?.role) && (
          <button
            type="button"
            className="context-action"
            onClick={() => setNewAppOpen(true)}
          >
            + New Application
          </button>
        )}
        {dataMode === "demo" && (
          <button
            type="button"
            className="context-action"
            disabled={resetting}
            onClick={() => void handleReset()}
          >
            {resetting ? "Resetting…" : "Reset Demo Data"}
          </button>
        )}
        <button type="button" className="context-action" onClick={() => setSpecOpen(true)}>
          Architecture Spec
        </button>
      </div>

      {toast && (
        <div className={`toast toast--${toast.tone}`} role="status">
          {toast.message}
        </div>
      )}

      {specOpen && <ArchitectureSpecModal onClose={() => setSpecOpen(false)} />}
      {newAppOpen && (
        <NewApplicationModal
          onClose={() => setNewAppOpen(false)}
          onCreated={handleCreated}
        />
      )}
    </div>
  );
}

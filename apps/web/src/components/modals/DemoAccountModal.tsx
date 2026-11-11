import { ROLE_LABELS } from "../../context/useWorkspace";
import { ROLE_PROFILES } from "../../services/mockData";
import type { UserRole } from "../../types/domain";
import { ModalShell } from "./ModalShell";

const ROLE_ORDER: readonly UserRole[] = ["loan_officer", "reviewer", "admin"];

interface DemoAccountModalProps {
  activeRole: UserRole;
  onSelect: (role: UserRole) => void;
  onClose: () => void;
}

export function DemoAccountModal({ activeRole, onSelect, onClose }: DemoAccountModalProps) {
  return (
    <ModalShell
      title="Switch demo account"
      subtitle="Demo Showcase has no real session. Pick the operator to continue as."
      onClose={onClose}
    >
      <ul className="account-list">
        {ROLE_ORDER.map((role) => {
          const profile = ROLE_PROFILES[role];
          return (
            <li key={role}>
              <button
                type="button"
                className="account-option"
                aria-pressed={role === activeRole}
                onClick={() => onSelect(role)}
              >
                <span className="account-name">{profile.name}</span>
                <span className="muted">
                  {ROLE_LABELS[role]} · {profile.email}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </ModalShell>
  );
}

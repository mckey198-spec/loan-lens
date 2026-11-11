import type { ApplicationStatus } from "../types/domain";

export function StatusBadge({ status }: { status: ApplicationStatus }) {
  return (
    <span className={`status-badge status-badge--${status}`}>{status}</span>
  );
}

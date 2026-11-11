import type { ApplicationStatus, DocumentStatus, RuleStatus } from "../api/types";

const APPLICATION_LABELS: Record<ApplicationStatus, string> = {
  draft: "Draft",
  submitted: "Submitted",
  processing: "Processing",
  processing_failed: "Processing failed",
  awaiting_human_review: "Awaiting review",
  in_review: "In review",
  clarification_required: "Clarification required",
  escalated: "Escalated",
  review_completed: "Review completed",
  archived: "Archived",
};

export function StatusTag({ status }: { status: ApplicationStatus }) {
  return (
    <span className={`status-tag status-tag--${status}`}>
      {APPLICATION_LABELS[status]}
    </span>
  );
}

export function DocumentStatusTag({ status }: { status: DocumentStatus }) {
  const quarantineHint =
    status === "uploaded"
      ? "post-quarantine (scan clean)"
      : status === "failed"
        ? "processing failed"
        : null;
  return (
    <span
      className={`status-tag status-tag--doc-${status}`}
      title={quarantineHint ?? undefined}
    >
      {status.replaceAll("_", " ")}
      {status === "uploaded" ? " · clean" : ""}
    </span>
  );
}

export function RuleStatusBadge({ status }: { status: RuleStatus }) {
  return (
    <span className={`status-tag status-tag--rule-${status}`}>{status}</span>
  );
}

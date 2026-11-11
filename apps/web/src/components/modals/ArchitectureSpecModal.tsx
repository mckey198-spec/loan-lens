import { ModalShell } from "./ModalShell";

interface Highlight {
  title: string;
  summary: string;
  points: string[];
}

const HIGHLIGHTS: readonly Highlight[] = [
  {
    title: "Strict Role-Based State Machine (RBAC Maker-Checker)",
    summary:
      "Loan Officers make, Reviewers check, Admins govern. No single role can both originate and decide an application.",
    points: [
      "Officers create, edit, and submit drafts; only Reviewers record Approved or Rejected outcomes.",
      "Every transition is validated against the current status, so out-of-order moves fail with a structured 409 error.",
      "Authorization is enforced by the API and re-checked in the database for review writes; hiding controls in the UI is never the gate.",
    ],
  },
  {
    title: "Tamper-Evident SHA-256 Audit Ledger",
    summary:
      "Each audit event stores the SHA-256 hash of its content together with the previous event's hash, forming a single chain from a genesis hash.",
    points: [
      "Appends are serialized behind one chain lock, so concurrent writers cannot fork the ledger.",
      "Verification replays the chain from genesis and reports the first event whose link or content hash does not match.",
      "Demo mode runs the same chain client-side with Web Crypto, including a tamper simulation for showcase purposes.",
    ],
  },
  {
    title: "Deterministic Underwriting Engine & FOIR",
    summary:
      "EMI, FOIR, and the BR-01 to BR-11 verification rules are pure, deterministic functions. No LLM computes a number or decides a gate.",
    points: [
      "Proposed EMI uses the standard amortizing formula; FOIR is total monthly obligations divided by net monthly income.",
      "The FOIR ceiling comes from versioned policy configuration, and inputs, formula identity, and rule version are persisted for replay.",
      "Model output is limited to labelled advisory observations and never overwrites a calculator result.",
    ],
  },
];

interface ArchitectureSpecModalProps {
  onClose: () => void;
}

export function ArchitectureSpecModal({ onClose }: ArchitectureSpecModalProps) {
  return (
    <ModalShell
      title="Architecture Spec"
      subtitle="Three engineering guarantees behind LoanLens."
      wide
      onClose={onClose}
    >
      <ol className="spec-list" data-testid="architecture-spec">
        {HIGHLIGHTS.map((highlight, index) => (
          <li key={highlight.title} className="spec-item">
            <span className="spec-index" aria-hidden="true">
              {index + 1}
            </span>
            <div>
              <h3 className="spec-title">{highlight.title}</h3>
              <p className="spec-summary">{highlight.summary}</p>
              <ul className="spec-points">
                {highlight.points.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ul>
            </div>
          </li>
        ))}
      </ol>
    </ModalShell>
  );
}

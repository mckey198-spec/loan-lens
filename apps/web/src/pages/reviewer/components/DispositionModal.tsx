import { useState, type FormEvent } from "react";

import type { FindingDispositionAction } from "../../../api/types";

const ACTIONS: FindingDispositionAction[] = [
  "accepted_risk",
  "overridden",
  "confirmed_issue",
  "false_positive",
];

interface DispositionModalProps {
  findingId: string;
  ruleCode: string;
  onClose: () => void;
  onSubmit: (payload: {
    finding_id: string;
    action: FindingDispositionAction;
    justification: string;
  }) => Promise<void>;
}

export function DispositionModal({
  findingId,
  ruleCode,
  onClose,
  onSubmit,
}: DispositionModalProps) {
  const [action, setAction] =
    useState<FindingDispositionAction>("accepted_risk");
  const [justification, setJustification] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = justification.trim().length >= 10 && !busy;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSubmit({
        finding_id: findingId,
        action,
        justification: justification.trim(),
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Disposition failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <form
        className="modal"
        data-testid="disposition-modal"
        onSubmit={(e) => void handleSubmit(e)}
      >
        <h3>Add disposition — {ruleCode}</h3>
        <label>
          Action
          <select
            value={action}
            onChange={(e) =>
              setAction(e.target.value as FindingDispositionAction)
            }
          >
            {ACTIONS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label>
          Justification (min 10 characters)
          <textarea
            data-testid="disposition-justification"
            value={justification}
            onChange={(e) => setJustification(e.target.value)}
            rows={4}
            required
          />
        </label>
        <p className="muted">{justification.trim().length}/10</p>
        {error && <p className="error-text">{error}</p>}
        <div className="action-bar">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            data-testid="disposition-submit-btn"
            disabled={!canSubmit}
          >
            {busy ? "Saving…" : "Save disposition"}
          </button>
        </div>
      </form>
    </div>
  );
}

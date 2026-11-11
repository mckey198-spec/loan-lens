import { useState } from "react";

import type { AssessmentRecommendation } from "../../../api/types";

const RECOMMENDATIONS: AssessmentRecommendation[] = [
  "approve",
  "reject",
  "refer",
  "exception_approved",
];

interface DecisionFooterProps {
  disabled?: boolean;
  onDecide: (payload: {
    recommendation: AssessmentRecommendation;
    reviewer_notes: string;
  }) => Promise<void>;
}

export function DecisionFooter({ disabled, onDecide }: DecisionFooterProps) {
  const [recommendation, setRecommendation] =
    useState<AssessmentRecommendation>("approve");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = notes.trim().length >= 10 && !busy && !disabled;

  async function handleSubmit() {
    if (!canSubmit) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onDecide({
        recommendation,
        reviewer_notes: notes.trim(),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Decision failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <footer className="decision-footer" data-testid="decision-footer">
      <h3>Final decision</h3>
      <div className="form-row">
        <label>
          Recommendation
          <select
            value={recommendation}
            disabled={disabled || busy}
            onChange={(e) =>
              setRecommendation(e.target.value as AssessmentRecommendation)
            }
          >
            {RECOMMENDATIONS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="grow">
          Notes (min 10 characters)
          <textarea
            data-testid="decision-notes"
            value={notes}
            disabled={disabled || busy}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
          />
        </label>
      </div>
      <p className="muted">{notes.trim().length}/10</p>
      {error && <p className="error-text">{error}</p>}
      <button
        type="button"
        className="btn btn-primary"
        data-testid="submit-final-decision-btn"
        disabled={!canSubmit}
        onClick={() => void handleSubmit()}
      >
        {busy ? "Submitting…" : "Submit Final Decision"}
      </button>
    </footer>
  );
}

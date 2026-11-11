import { useState } from "react";

import type {
  FindingDispositionAction,
  VerificationFinding,
} from "../../../api/types";
import { RuleStatusBadge } from "../../../components/StatusTag";
import { DispositionModal } from "./DispositionModal";

interface RulesMatrixProps {
  findings: VerificationFinding[];
  canDispose: boolean;
  onDispose: (payload: {
    finding_id: string;
    action: FindingDispositionAction;
    justification: string;
  }) => Promise<void>;
}

export function RulesMatrix({
  findings,
  canDispose,
  onDispose,
}: RulesMatrixProps) {
  const [activeFinding, setActiveFinding] =
    useState<VerificationFinding | null>(null);

  return (
    <section className="panel" data-testid="rules-matrix">
      <h3>Deterministic rules matrix (BR-01..11)</h3>
      <table className="data-table">
        <thead>
          <tr>
            <th>Rule</th>
            <th>Status</th>
            <th>Severity</th>
            <th>Message</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {findings.map((finding) => (
            <tr key={finding.id}>
              <td>
                <strong>{finding.rule_code}</strong>
                <div className="muted">{finding.rule_name}</div>
              </td>
              <td>
                <RuleStatusBadge status={finding.status} />
              </td>
              <td>{finding.severity}</td>
              <td>{finding.message}</td>
              <td>
                {canDispose && finding.status !== "passed" && (
                  <button
                    type="button"
                    className="btn btn-secondary"
                    data-testid={`add-disposition-${finding.rule_code}`}
                    onClick={() => setActiveFinding(finding)}
                  >
                    Add Disposition
                  </button>
                )}
              </td>
            </tr>
          ))}
          {findings.length === 0 && (
            <tr>
              <td colSpan={5} className="muted">
                No verification findings.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {activeFinding && (
        <DispositionModal
          findingId={activeFinding.id}
          ruleCode={activeFinding.rule_code}
          onClose={() => setActiveFinding(null)}
          onSubmit={onDispose}
        />
      )}
    </section>
  );
}

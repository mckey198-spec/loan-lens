/** BR-12 advisory banner — AI observations are never credit decisions. */
export function AdvisoryBanner() {
  return (
    <div
      className="advisory-banner"
      role="status"
      data-testid="br12-advisory-banner"
    >
      ADVISORY ONLY — NOT A CREDIT DECISION
    </div>
  );
}

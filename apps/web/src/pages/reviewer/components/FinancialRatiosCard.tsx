import type { FinancialCalculation } from "../../../api/types";

export function FinancialRatiosCard({
  calculation,
}: {
  calculation: FinancialCalculation | null;
}) {
  return (
    <section className="panel" data-testid="financial-ratios-card">
      <h3>Financial ratios</h3>
      {!calculation ? (
        <p className="muted">No financial calculation available.</p>
      ) : (
        <dl className="metric-grid">
          <div>
            <dt>EMI</dt>
            <dd>{calculation.proposed_emi}</dd>
          </div>
          <div>
            <dt>FOIR %</dt>
            <dd>
              {calculation.foir_pct}
              <span className="muted">
                {" "}
                / {calculation.foir_threshold_pct}
                {calculation.foir_passed ? " · pass" : " · fail"}
              </span>
            </dd>
          </div>
          <div>
            <dt>NDI</dt>
            <dd>{calculation.net_disposable_income}</dd>
          </div>
          <div>
            <dt>Qualifying loan</dt>
            <dd>{calculation.max_qualifying_loan_amount}</dd>
          </div>
        </dl>
      )}
    </section>
  );
}

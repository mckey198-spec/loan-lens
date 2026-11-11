import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getApplication } from "../../api/applications";
import { listAssessments } from "../../api/assessments";
import {
  downloadAssessmentPdf,
  getLatestCalculation,
  getLatestObservation,
  getLatestVerification,
} from "../../api/reviewData";
import type {
  Application,
  Assessment,
  FinancialCalculation,
  VerificationRunSummary,
} from "../../api/types";
import { AdvisoryBanner } from "../../components/AdvisoryBanner";
import { StatusTag } from "../../components/StatusTag";

export function AssessmentViewPage() {
  const { applicationId = "" } = useParams();
  const [application, setApplication] = useState<Application | null>(null);
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [calculation, setCalculation] = useState<FinancialCalculation | null>(
    null,
  );
  const [verification, setVerification] =
    useState<VerificationRunSummary | null>(null);
  const [observationText, setObservationText] = useState<string | null>(null);
  const [isAiGenerated, setIsAiGenerated] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [app, assessments, calc, ver, obs] = await Promise.all([
          getApplication(applicationId),
          listAssessments(applicationId),
          getLatestCalculation(applicationId),
          getLatestVerification(applicationId),
          getLatestObservation(applicationId),
        ]);
        if (cancelled) {
          return;
        }
        setApplication(app);
        setAssessment(assessments.items[0] ?? null);
        setCalculation(calc);
        setVerification(ver);
        setObservationText(obs?.observation_text ?? null);
        setIsAiGenerated(Boolean(obs?.is_ai_generated));
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [applicationId]);

  async function handleDownload() {
    setDownloading(true);
    setError(null);
    try {
      const blob = await downloadAssessmentPdf(applicationId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `loanlens_assessment_${applicationId}.pdf`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "PDF export failed");
    } finally {
      setDownloading(false);
    }
  }

  if (!application) {
    return (
      <div className="page">
        {error ? <p className="error-text">{error}</p> : <p>Loading…</p>}
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Assessment (read-only)</h1>
          <p className="muted">
            {application.reference_code}{" "}
            <StatusTag status={application.status} />
          </p>
        </div>
        <div className="action-bar">
          <Link to={`/officer/applications/${applicationId}`}>Intake</Link>
          <button
            type="button"
            className="btn btn-primary"
            data-testid="download-pdf-btn"
            disabled={downloading}
            onClick={() => void handleDownload()}
          >
            {downloading ? "Preparing…" : "Download PDF Export"}
          </button>
        </div>
      </div>

      {error && <p className="error-text">{error}</p>}

      <section className="panel">
        <h3>Decision</h3>
        {assessment?.recommendation ? (
          <p>
            <strong>{assessment.recommendation}</strong>
            {assessment.reviewer_notes && (
              <> — {assessment.reviewer_notes}</>
            )}
          </p>
        ) : (
          <p className="muted">No final decision recorded yet.</p>
        )}
      </section>

      <section className="panel">
        <h3>Financial ratios</h3>
        {calculation ? (
          <dl className="metric-grid">
            <div>
              <dt>EMI</dt>
              <dd>{calculation.proposed_emi}</dd>
            </div>
            <div>
              <dt>FOIR %</dt>
              <dd>{calculation.foir_pct}</dd>
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
        ) : (
          <p className="muted">No calculation available.</p>
        )}
      </section>

      <section className="panel">
        <h3>Rules (BR-01..11)</h3>
        {verification ? (
          <ul>
            {verification.findings.map((f) => (
              <li key={f.id}>
                {f.rule_code}: {f.status} — {f.message}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No verification run.</p>
        )}
      </section>

      <section className="panel">
        <h3>AI observations</h3>
        <AdvisoryBanner />
        {observationText ? (
          <>
            <p data-testid="is-ai-generated-indicator">
              is_ai_generated: {String(isAiGenerated)}
            </p>
            <p>{observationText}</p>
          </>
        ) : (
          <p className="muted">No AI observation.</p>
        )}
      </section>

      {/* Maker-checker: officers never see Claim or Submit Final Decision */}
    </div>
  );
}

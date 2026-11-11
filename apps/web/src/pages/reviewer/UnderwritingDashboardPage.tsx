import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getApplication } from "../../api/applications";
import {
  decideAssessment,
  listAssessments,
  recordDisposition,
} from "../../api/assessments";
import { listDocuments } from "../../api/documents";
import {
  getLatestCalculation,
  getLatestObservation,
  getLatestVerification,
  getPolicyCitations,
} from "../../api/reviewData";
import type {
  AiObservation,
  Application,
  Assessment,
  AssessmentRecommendation,
  DocumentMeta,
  FinancialCalculation,
  FindingDispositionAction,
  PolicyCitationsResponse,
  VerificationRunSummary,
} from "../../api/types";
import { StatusTag } from "../../components/StatusTag";
import { useAuth } from "../../auth/AuthProvider";
import { AiObservationsPanel } from "./components/AiObservationsPanel";
import { DecisionFooter } from "./components/DecisionFooter";
import { DocumentFieldsPanel } from "./components/DocumentFieldsPanel";
import { FinancialRatiosCard } from "./components/FinancialRatiosCard";
import { PolicyCitationsDrawer } from "./components/PolicyCitationsDrawer";
import { RulesMatrix } from "./components/RulesMatrix";

export function UnderwritingDashboardPage() {
  const { applicationId = "" } = useParams();
  const { hasRole, user } = useAuth();
  const canReview = hasRole("reviewer", "admin");

  const [application, setApplication] = useState<Application | null>(null);
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [documents, setDocuments] = useState<DocumentMeta[]>([]);
  const [verification, setVerification] =
    useState<VerificationRunSummary | null>(null);
  const [calculation, setCalculation] = useState<FinancialCalculation | null>(
    null,
  );
  const [observation, setObservation] = useState<AiObservation | null>(null);
  const [citations, setCitations] = useState<PolicyCitationsResponse | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    const [app, assessments, docs, ver, calc, obs, cites] = await Promise.all([
      getApplication(applicationId),
      listAssessments(applicationId),
      listDocuments(applicationId),
      getLatestVerification(applicationId),
      getLatestCalculation(applicationId),
      getLatestObservation(applicationId),
      getPolicyCitations(applicationId),
    ]);
    setApplication(app);
    setAssessment(assessments.items[0] ?? null);
    setDocuments(docs);
    setVerification(ver);
    setCalculation(calc);
    setObservation(obs);
    setCitations(cites);
  }, [applicationId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      setError(null);
      try {
        await reload();
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reload]);

  const decided = Boolean(assessment?.decided_at);
  const showDecisionActions =
    canReview &&
    !decided &&
    assessment !== null &&
    (application?.status === "in_review" ||
      application?.status === "escalated");

  async function handleDispose(payload: {
    finding_id: string;
    action: FindingDispositionAction;
    justification: string;
  }) {
    if (!assessment) {
      throw new Error("No assessment claimed for this application.");
    }
    await recordDisposition(applicationId, assessment.id, payload);
    setMessage("Disposition recorded.");
    await reload();
  }

  async function handleDecide(payload: {
    recommendation: AssessmentRecommendation;
    reviewer_notes: string;
  }) {
    if (!assessment) {
      throw new Error("No assessment claimed for this application.");
    }
    const updated = await decideAssessment(
      applicationId,
      assessment.id,
      payload,
    );
    setAssessment(updated);
    setMessage("Final decision submitted.");
    await reload();
  }

  if (loading || !application) {
    return (
      <div className="page">
        {error ? <p className="error-text">{error}</p> : <p>Loading workspace…</p>}
      </div>
    );
  }

  return (
    <div className="page underwriting-page">
      <div className="page-header">
        <div>
          <h1>Underwriting dashboard</h1>
          <p className="muted">
            {application.reference_code} · {application.applicant.full_name}{" "}
            <StatusTag status={application.status} />
          </p>
          {assessment?.assigned_reviewer_id && (
            <p className="muted">
              Assigned reviewer: {assessment.assigned_reviewer_id}
              {user?.id === assessment.assigned_reviewer_id ? " (you)" : ""}
            </p>
          )}
        </div>
        <Link to="/reviewer/queue">Back to queue</Link>
      </div>

      {error && <p className="error-text">{error}</p>}
      {message && <p className="success-text">{message}</p>}

      <div className="split-view">
        <DocumentFieldsPanel
          application={application}
          documents={documents}
          findings={verification?.findings ?? []}
        />
        <div className="split-right">
          <FinancialRatiosCard calculation={calculation} />
          <RulesMatrix
            findings={verification?.findings ?? []}
            canDispose={showDecisionActions}
            onDispose={handleDispose}
          />
          <AiObservationsPanel observation={observation} />
          <PolicyCitationsDrawer citations={citations} />
          {showDecisionActions && assessment && (
            <DecisionFooter onDecide={handleDecide} />
          )}
          {decided && (
            <section className="panel">
              <h3>Decision recorded</h3>
              <p>
                <strong>{assessment?.recommendation}</strong> —{" "}
                {assessment?.reviewer_notes}
              </p>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

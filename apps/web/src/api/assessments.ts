import { apiFetch } from "./client";
import type {
  Assessment,
  AssessmentListResponse,
  AssessmentRecommendation,
  FindingDispositionAction,
} from "./types";

export async function claimApplication(
  applicationId: string,
): Promise<Assessment> {
  return apiFetch<Assessment>(`/api/v1/applications/${applicationId}/claim`, {
    method: "POST",
  });
}

export async function listAssessments(
  applicationId: string,
): Promise<AssessmentListResponse> {
  return apiFetch<AssessmentListResponse>(
    `/api/v1/applications/${applicationId}/assessments`,
  );
}

export async function recordDisposition(
  applicationId: string,
  assessmentId: string,
  payload: {
    finding_id: string;
    action: FindingDispositionAction;
    justification: string;
  },
): Promise<unknown> {
  return apiFetch(
    `/api/v1/applications/${applicationId}/assessments/${assessmentId}/dispositions`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export async function decideAssessment(
  applicationId: string,
  assessmentId: string,
  payload: {
    recommendation: AssessmentRecommendation;
    reviewer_notes: string;
  },
): Promise<Assessment> {
  return apiFetch<Assessment>(
    `/api/v1/applications/${applicationId}/assessments/${assessmentId}/decide`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

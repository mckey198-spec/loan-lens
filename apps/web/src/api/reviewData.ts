import { apiFetch, apiFetchBlob } from "./client";
import type {
  AiObservation,
  FinancialCalculation,
  PolicyCitationsResponse,
  VerificationRunSummary,
} from "./types";
import { ApiError } from "./types";

export async function getLatestVerification(
  applicationId: string,
): Promise<VerificationRunSummary | null> {
  try {
    return await apiFetch<VerificationRunSummary>(
      `/api/v1/applications/${applicationId}/verification`,
    );
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

export async function getLatestCalculation(
  applicationId: string,
): Promise<FinancialCalculation | null> {
  try {
    return await apiFetch<FinancialCalculation>(
      `/api/v1/applications/${applicationId}/calculations`,
    );
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

export async function getLatestObservation(
  applicationId: string,
): Promise<AiObservation | null> {
  try {
    return await apiFetch<AiObservation>(
      `/api/v1/applications/${applicationId}/observations`,
    );
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

export async function getPolicyCitations(
  applicationId: string,
): Promise<PolicyCitationsResponse | null> {
  try {
    return await apiFetch<PolicyCitationsResponse>(
      `/api/v1/applications/${applicationId}/policy-citations`,
    );
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

export async function downloadAssessmentPdf(
  applicationId: string,
): Promise<Blob> {
  return apiFetchBlob(
    `/api/v1/applications/${applicationId}/assessment/export`,
  );
}

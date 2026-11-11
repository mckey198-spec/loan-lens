import { apiFetch } from "./client";
import type {
  Application,
  ApplicationListResponse,
  ApplicationStatus,
  CreateApplicationPayload,
} from "./types";

export async function listApplications(params?: {
  offset?: number;
  limit?: number;
  status?: ApplicationStatus;
}): Promise<ApplicationListResponse> {
  const query = new URLSearchParams();
  if (params?.offset !== undefined) {
    query.set("offset", String(params.offset));
  }
  if (params?.limit !== undefined) {
    query.set("limit", String(params.limit));
  }
  if (params?.status) {
    query.set("status", params.status);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return apiFetch<ApplicationListResponse>(`/api/v1/applications${suffix}`);
}

export async function getApplication(
  applicationId: string,
): Promise<Application> {
  return apiFetch<Application>(`/api/v1/applications/${applicationId}`);
}

export async function createApplication(
  payload: CreateApplicationPayload,
): Promise<Application> {
  return apiFetch<Application>("/api/v1/applications", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateApplication(
  applicationId: string,
  payload: Partial<CreateApplicationPayload>,
): Promise<Application> {
  return apiFetch<Application>(`/api/v1/applications/${applicationId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function submitApplication(
  applicationId: string,
): Promise<Application> {
  return apiFetch<Application>(
    `/api/v1/applications/${applicationId}/submit`,
    { method: "POST" },
  );
}

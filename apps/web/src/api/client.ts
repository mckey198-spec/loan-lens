import { ApiError, type ApiErrorBody } from "./types";

const CSRF_STORAGE_KEY = "loanlens_csrf_token";

export function getCsrfToken(): string | null {
  return sessionStorage.getItem(CSRF_STORAGE_KEY);
}

export function setCsrfToken(token: string | null): void {
  if (token === null || token === "") {
    sessionStorage.removeItem(CSRF_STORAGE_KEY);
    return;
  }
  sessionStorage.setItem(CSRF_STORAGE_KEY, token);
}

function isMutatingMethod(method: string): boolean {
  return !["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase());
}

async function parseErrorBody(response: Response): Promise<ApiErrorBody | null> {
  try {
    return (await response.json()) as ApiErrorBody;
  } catch {
    return null;
  }
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);

  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }

  if (isMutatingMethod(method)) {
    const csrf = getCsrfToken();
    if (csrf) {
      headers.set("X-CSRF-Token", csrf);
    }
    if (
      init.body !== undefined &&
      !(init.body instanceof FormData) &&
      !headers.has("Content-Type")
    ) {
      headers.set("Content-Type", "application/json");
    }
  }

  const response = await fetch(path, {
    ...init,
    method,
    headers,
    credentials: "include",
  });

  if (response.status === 204) {
    return undefined as T;
  }

  if (!response.ok) {
    const body = await parseErrorBody(response);
    throw new ApiError(response.status, body, response.statusText);
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return (await response.json()) as T;
  }
  return undefined as T;
}

export async function apiFetchBlob(path: string): Promise<Blob> {
  const response = await fetch(path, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/pdf" },
  });
  if (!response.ok) {
    const body = await parseErrorBody(response);
    throw new ApiError(response.status, body, response.statusText);
  }
  return response.blob();
}

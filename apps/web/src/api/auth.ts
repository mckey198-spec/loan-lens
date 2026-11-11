import { apiFetch, setCsrfToken } from "./client";
import type { LoginResponse, UserProfile } from "./types";

export async function login(
  email: string,
  password: string,
): Promise<LoginResponse> {
  const result = await apiFetch<LoginResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setCsrfToken(result.csrf_token);
  return result;
}

export async function logout(): Promise<void> {
  try {
    await apiFetch<void>("/api/v1/auth/logout", { method: "POST" });
  } finally {
    setCsrfToken(null);
  }
}

export async function fetchMe(): Promise<UserProfile> {
  return apiFetch<UserProfile>("/api/v1/auth/me");
}

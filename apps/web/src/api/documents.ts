import { apiFetch } from "./client";
import type { DocumentMeta, DocumentType } from "./types";

export async function listDocuments(
  applicationId: string,
): Promise<DocumentMeta[]> {
  return apiFetch<DocumentMeta[]>(
    `/api/v1/applications/${applicationId}/documents`,
  );
}

export async function uploadDocument(
  applicationId: string,
  file: File,
  documentType: DocumentType,
): Promise<DocumentMeta> {
  const form = new FormData();
  form.append("file", file);
  form.append("document_type", documentType);
  return apiFetch<DocumentMeta>(
    `/api/v1/applications/${applicationId}/documents`,
    {
      method: "POST",
      body: form,
    },
  );
}

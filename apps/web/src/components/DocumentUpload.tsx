import { useState, type ChangeEvent } from "react";

import { uploadDocument } from "../api/documents";
import type { DocumentMeta, DocumentType } from "../api/types";
import { DocumentStatusTag } from "./StatusTag";

const DOCUMENT_TYPES: DocumentType[] = [
  "identity_proof",
  "pan_card",
  "address_proof",
  "salary_slip",
  "bank_statement",
  "form_16",
  "itr",
  "gst_return",
  "business_proof",
  "existing_loan_statement",
  "other",
];

interface DocumentUploadProps {
  applicationId: string;
  documents: DocumentMeta[];
  disabled?: boolean;
  onUploaded: (doc: DocumentMeta) => void;
}

export function DocumentUpload({
  applicationId,
  documents,
  disabled = false,
  onUploaded,
}: DocumentUploadProps) {
  const [documentType, setDocumentType] = useState<DocumentType>("pan_card");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const doc = await uploadDocument(applicationId, file, documentType);
      onUploaded(doc);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" data-testid="document-upload">
      <h3>Documents</h3>
      <p className="muted">
        Uploads enter quarantine, are malware-scanned, and only appear here when
        clean. Status tags reflect post-scan processing.
      </p>
      {!disabled && (
        <div className="form-row">
          <label>
            Document type
            <select
              value={documentType}
              onChange={(e) => setDocumentType(e.target.value as DocumentType)}
              disabled={busy}
            >
              {DOCUMENT_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </label>
          <label className="file-input-label">
            {busy ? "Uploading…" : "Choose file"}
            <input
              type="file"
              data-testid="document-file-input"
              disabled={busy}
              onChange={(e) => void handleFileChange(e)}
            />
          </label>
        </div>
      )}
      {error && <p className="error-text">{error}</p>}
      <ul className="doc-list">
        {documents.length === 0 && (
          <li className="muted">No documents uploaded yet.</li>
        )}
        {documents.map((doc) => (
          <li key={doc.id} className="doc-list-item">
            <div>
              <strong>{doc.original_filename}</strong>
              <div className="muted">
                {doc.document_type} · {(doc.byte_size / 1024).toFixed(1)} KB
              </div>
            </div>
            <DocumentStatusTag status={doc.status} />
          </li>
        ))}
      </ul>
    </section>
  );
}

import { useMemo, useState } from "react";

import type {
  Application,
  DocumentMeta,
  VerificationFinding,
} from "../../../api/types";

interface DocumentFieldsPanelProps {
  application: Application;
  documents: DocumentMeta[];
  findings: VerificationFinding[];
}

export function DocumentFieldsPanel({
  application,
  documents,
  findings,
}: DocumentFieldsPanelProps) {
  const [selectedDocId, setSelectedDocId] = useState<string>("declared");

  const extractedRows = useMemo(() => {
    if (selectedDocId === "declared") {
      const a = application.applicant;
      return [
        { field: "full_name", value: a.full_name, layer: "declared" },
        { field: "pan", value: maskPan(a.pan), layer: "declared" },
        { field: "date_of_birth", value: a.date_of_birth, layer: "declared" },
        {
          field: "net_monthly_income",
          value: a.net_monthly_income,
          layer: "declared",
        },
        {
          field: "gross_monthly_income",
          value: a.gross_monthly_income,
          layer: "declared",
        },
        {
          field: "employer_or_business_name",
          value: a.employer_or_business_name,
          layer: "declared",
        },
      ];
    }

    const rows: Array<{ field: string; value: string; layer: string }> = [];
    for (const finding of findings) {
      for (const source of finding.provenance_sources) {
        const docId = String(
          source.source_document_id ?? source.document_id ?? "",
        );
        if (docId && docId !== selectedDocId) {
          continue;
        }
        const field = String(
          source.field_name ?? source.field ?? finding.rule_code,
        );
        const value = String(
          source.value ?? source.normalized_value ?? finding.message,
        );
        const layer = String(source.layer ?? "extracted");
        rows.push({ field, value, layer });
      }
    }
    if (rows.length === 0) {
      const doc = documents.find((d) => d.id === selectedDocId);
      rows.push({
        field: "document_type",
        value: doc?.document_type ?? "—",
        layer: "metadata",
      });
      rows.push({
        field: "status",
        value: doc?.status ?? "—",
        layer: "metadata",
      });
    }
    return rows;
  }, [application, documents, findings, selectedDocId]);

  return (
    <aside className="panel split-left" data-testid="document-fields-panel">
      <h3>Documents & extracted fields</h3>
      <div className="doc-tabs" role="tablist">
        <button
          type="button"
          className={selectedDocId === "declared" ? "tab active" : "tab"}
          onClick={() => setSelectedDocId("declared")}
        >
          Declared
        </button>
        {documents.map((doc) => (
          <button
            key={doc.id}
            type="button"
            className={selectedDocId === doc.id ? "tab active" : "tab"}
            onClick={() => setSelectedDocId(doc.id)}
            title={doc.original_filename}
          >
            {doc.document_type}
          </button>
        ))}
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>Field</th>
            <th>Value</th>
            <th>Layer</th>
          </tr>
        </thead>
        <tbody>
          {extractedRows.map((row) => (
            <tr key={`${row.field}-${row.layer}-${row.value}`}>
              <td>{row.field}</td>
              <td>{row.value}</td>
              <td>{row.layer}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </aside>
  );
}

function maskPan(pan: string): string {
  if (pan.length < 4) {
    return "****";
  }
  return `${pan.slice(0, 2)}******${pan.slice(-2)}`;
}

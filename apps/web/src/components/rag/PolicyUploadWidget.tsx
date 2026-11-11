import { useMemo, useRef, useState, type ChangeEvent, type DragEvent } from "react";

import { AdvisoryBanner } from "../AdvisoryBanner";
import {
  formatCitationForDecisionNote,
  getActivePolicyName,
  getIndexedChunkCount,
  hasCustomPolicy,
  ingestPolicyFile,
  queryIngestedPolicy,
  synthesizeUnderwritingAdvice,
  type IngestProgressStage,
  type RetrievalResult,
} from "../../services/policyIngestionEngine";
import { formatInr } from "../../utils/currency";

const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".txt"] as const;
const ACCEPT_ATTR = ".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain";

const STAGE_LABEL: Record<Exclude<IngestProgressStage, "done" | "error">, string> = {
  extracting: "Extracting text",
  chunking: "Chunking 350-word windows",
  indexing: "Indexing embeddings…",
};

export interface PolicyUploadWidgetProps {
  foirPct: number;
  loanAmount: number;
  onInsertIntoDecisionNote: (text: string) => void;
  insertDisabled?: boolean;
}

function isAcceptedFile(file: File): boolean {
  const lower = file.name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

function progressPipelineLabel(stage: IngestProgressStage | null): string {
  if (!stage || stage === "done" || stage === "error") {
    return "";
  }
  const order: Array<Exclude<IngestProgressStage, "done" | "error">> = [
    "extracting",
    "chunking",
    "indexing",
  ];
  return order
    .map((s) => (s === stage ? STAGE_LABEL[s] : STAGE_LABEL[s].replace("…", "")))
    .join(" → ");
}

export function PolicyUploadWidget({
  foirPct,
  loanAmount,
  onInsertIntoDecisionNote,
  insertDisabled = false,
}: PolicyUploadWidgetProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [policyName, setPolicyName] = useState(getActivePolicyName);
  const [chunkCount, setChunkCount] = useState(getIndexedChunkCount);
  const [customActive, setCustomActive] = useState(hasCustomPolicy);
  const [ingestStage, setIngestStage] = useState<IngestProgressStage | null>(null);
  const [ingestError, setIngestError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<RetrievalResult[]>([]);
  const [advice, setAdvice] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);

  const busy = ingestStage === "extracting" || ingestStage === "chunking" || ingestStage === "indexing";

  const suggestionChips = useMemo(() => {
    const roundedFoir = Math.round(foirPct);
    return [
      `What is the FOIR policy for a ${roundedFoir}% case?`,
      `Is loan amount ${formatInr(loanAmount)} within sanctioned limits?`,
      "What is the policy for salary under 40k?",
      "Does freelance income count?",
    ];
  }, [foirPct, loanAmount]);

  const activeBadge = customActive
    ? `Active: ${policyName} (${chunkCount} chunks indexed into vector memory)`
    : policyName;

  function showSuccessToast(message: string): void {
    setToast(message);
    window.setTimeout(() => setToast(null), 3500);
  }

  async function processFile(file: File): Promise<void> {
    if (!isAcceptedFile(file)) {
      setIngestError("Unsupported file type. Upload a .pdf, .docx, or .txt policy document.");
      return;
    }

    setIngestError(null);
    setResults([]);
    setAdvice(null);
    setSearched(false);

    try {
      const outcome = await ingestPolicyFile(file, (stage) => setIngestStage(stage));
      setPolicyName(outcome.docName);
      setChunkCount(outcome.chunkCount);
      setCustomActive(true);
      setIngestStage(null);
      showSuccessToast(
        `New underwriting rules are now active from ${outcome.docName} (${outcome.chunkCount} chunks indexed).`,
      );
    } catch (err) {
      setIngestStage(null);
      setIngestError(err instanceof Error ? err.message : "Failed to ingest policy document.");
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>): void {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) {
      void processFile(file);
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>): void {
    event.preventDefault();
    setDragOver(false);
    const file = event.dataTransfer.files?.[0];
    if (file) {
      void processFile(file);
    }
  }

  function runSearch(rawQuery: string): void {
    const trimmed = rawQuery.trim();
    if (trimmed === "") {
      return;
    }
    setQuery(trimmed);
    const hits = queryIngestedPolicy(trimmed, 3);
    setResults(hits);
    setAdvice(synthesizeUnderwritingAdvice(trimmed, hits));
    setSearched(true);
  }

  function handleInsert(result: RetrievalResult): void {
    onInsertIntoDecisionNote(formatCitationForDecisionNote(result));
    showSuccessToast("Cited rule inserted into Decision Note.");
  }

  function handleInsertAdvice(): void {
    if (!advice) {
      return;
    }
    onInsertIntoDecisionNote(advice);
    showSuccessToast("Synthesized advice inserted into Decision Note.");
  }

  return (
    <section
      className="soft-card policy-rag-card"
      data-testid="policy-rag-card"
      aria-labelledby="policy-rag-heading"
    >
      <div className="policy-rag-topbar">
        <div>
          <h2 id="policy-rag-heading">Policy Ingestion &amp; RAG Copilot</h2>
          <p className="policy-active-badge" data-testid="policy-active-badge">
            {activeBadge}
          </p>
        </div>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          disabled={busy}
          onClick={() => fileInputRef.current?.click()}
          data-testid="policy-upload-trigger"
        >
          Upload New Policy Document (.pdf / .docx)
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPT_ATTR}
          className="sr-only"
          data-testid="policy-file-input"
          onChange={handleFileChange}
        />
      </div>

      <div
        className={`policy-dropzone${dragOver ? " policy-dropzone--active" : ""}`}
        data-testid="policy-dropzone"
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => {
          if (!busy) {
            fileInputRef.current?.click();
          }
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            fileInputRef.current?.click();
          }
        }}
        role="button"
        tabIndex={0}
        aria-label="Drop policy document or click to browse"
      >
        <p>
          Drop a <strong>.pdf</strong>, <strong>.docx</strong>, or <strong>.txt</strong> rulebook
          here, or click to browse.
        </p>
        <p className="muted">
          Text is extracted locally, chunked into 350-word windows, and indexed in session vector
          memory for citation search.
        </p>
      </div>

      {busy && (
        <p className="policy-ingest-progress" data-testid="policy-ingest-progress" role="status">
          {progressPipelineLabel(ingestStage)}
        </p>
      )}
      {ingestError && (
        <p className="error-text" role="alert" data-testid="policy-ingest-error">
          {ingestError}
        </p>
      )}

      <form
        className="policy-search-row"
        onSubmit={(e) => {
          e.preventDefault();
          runSearch(query);
        }}
      >
        <label className="policy-search-label" htmlFor="policy-rag-query">
          Ask the active policy
        </label>
        <div className="policy-search-controls">
          <input
            id="policy-rag-query"
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder='e.g. "What is the policy for salary under 40k?"'
            data-testid="policy-rag-query"
            disabled={busy}
          />
          <button
            type="submit"
            className="btn btn-primary btn-sm"
            disabled={busy || query.trim() === ""}
            data-testid="policy-rag-search"
          >
            Search
          </button>
        </div>
      </form>

      <div className="policy-chip-row" data-testid="policy-suggestion-chips">
        {suggestionChips.map((chip) => (
          <button
            key={chip}
            type="button"
            className="helper-chip policy-suggestion-chip"
            disabled={busy}
            onClick={() => runSearch(chip)}
          >
            {chip}
          </button>
        ))}
      </div>

      {searched && (
        <div className="policy-rag-results" data-testid="policy-rag-results">
          <h3>Retrieved Policy Clauses</h3>
          {results.length === 0 ? (
            <p className="muted">No matching clauses above the relevance threshold.</p>
          ) : (
            <ul className="policy-clause-list">
              {results.map((result) => (
                <li key={result.chunk.id} className="policy-clause-item">
                  <div className="policy-clause-meta">
                    <span className="policy-clause-source">{result.chunk.sourceDoc}</span>
                    <span className="muted">
                      Chunk #{result.chunk.chunkIndex} · {result.chunk.pageLabel}
                    </span>
                    <span
                      className="policy-score-badge"
                      data-testid="policy-score-badge"
                      title="Pseudo-cosine similarity"
                    >
                      {result.score.toFixed(2)}
                    </span>
                  </div>
                  <blockquote className="policy-clause-snippet">{result.chunk.content}</blockquote>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    disabled={insertDisabled}
                    onClick={() => handleInsert(result)}
                    data-testid="insert-citation-btn"
                  >
                    Insert into Decision Note
                  </button>
                </li>
              ))}
            </ul>
          )}

          <h3>Synthesized Underwriting Advice</h3>
          <AdvisoryBanner />
          <pre className="policy-advice-block" data-testid="policy-synthesized-advice">
            {advice}
          </pre>
          {advice && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={insertDisabled}
              onClick={handleInsertAdvice}
              data-testid="insert-advice-btn"
            >
              Insert into Decision Note
            </button>
          )}
        </div>
      )}

      {toast && (
        <div className="toast toast--success" role="status" data-testid="policy-ingest-toast">
          {toast}
        </div>
      )}
    </section>
  );
}

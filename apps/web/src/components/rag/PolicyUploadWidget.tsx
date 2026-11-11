import { useMemo, useRef, useState, type ChangeEvent, type DragEvent } from "react";

import { AdvisoryBanner } from "../AdvisoryBanner";
import {
  generatePolicyAnswer,
  getActivePolicyName,
  getIndexedChunkCount,
  hasCustomPolicy,
  ingestPolicyFile,
  queryIngestedPolicy,
  resolveOpenAiApiKey,
  setSessionOpenAiApiKey,
  type IngestProgressStage,
  type RetrievalResult,
} from "../../services/policyIngestionEngine";
import { formatInr } from "../../utils/currency";

const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".txt"] as const;
const ACCEPT_ATTR =
  ".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain";

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

/** Lightweight prose rendering for advisory text (paragraphs + simple bullets). */
function AdvisoryProse({ text }: { text: string }) {
  const blocks = text
    .split(/\n\s*\n/)
    .map((block) => block.trim())
    .filter((block) => block.length > 0);

  return (
    <div className="policy-advisory-prose" data-testid="policy-synthesized-advice">
      {blocks.map((block, index) => {
        const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
        const isList = lines.length > 1 && lines.every((line) => /^[-•*]/.test(line));
        if (isList) {
          return (
            <ul key={`b-${index}`}>
              {lines.map((line, lineIndex) => (
                <li key={`l-${index}-${lineIndex}`}>{line.replace(/^[-•*]\s*/, "")}</li>
              ))}
            </ul>
          );
        }
        return (
          <p key={`b-${index}`}>
            {lines.map((line, lineIndex) => (
              <span key={`s-${index}-${lineIndex}`}>
                {lineIndex > 0 ? <br /> : null}
                {line}
              </span>
            ))}
          </p>
        );
      })}
    </div>
  );
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
  const [searching, setSearching] = useState(false);

  const [keyPanelOpen, setKeyPanelOpen] = useState(false);
  const [apiKeyDraft, setApiKeyDraft] = useState(() => resolveOpenAiApiKey() ?? "");
  const [hasSessionKey, setHasSessionKey] = useState(() => Boolean(resolveOpenAiApiKey()));

  const busy =
    ingestStage === "extracting" ||
    ingestStage === "chunking" ||
    ingestStage === "indexing" ||
    searching;

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

  async function runSearch(rawQuery: string): Promise<void> {
    const trimmed = rawQuery.trim();
    if (trimmed === "") {
      return;
    }
    setQuery(trimmed);
    setSearching(true);
    try {
      const hits = queryIngestedPolicy(trimmed, 3);
      setResults(hits);
      const answer = await generatePolicyAnswer(
        trimmed,
        hits.map((hit) => hit.chunk),
      );
      setAdvice(answer);
      setSearched(true);
    } finally {
      setSearching(false);
    }
  }

  function handleInsertAdvice(): void {
    if (!advice) {
      return;
    }
    onInsertIntoDecisionNote(advice);
    showSuccessToast("Underwriting advisory inserted into Decision Note.");
  }

  function handleSaveApiKey(): void {
    setSessionOpenAiApiKey(apiKeyDraft);
    const resolved = Boolean(resolveOpenAiApiKey());
    setHasSessionKey(resolved);
    setKeyPanelOpen(false);
    showSuccessToast(
      resolved
        ? "OpenAI key saved for this session."
        : "OpenAI key cleared. Demo fallback answers will be used.",
    );
  }

  function handleClearApiKey(): void {
    setApiKeyDraft("");
    setSessionOpenAiApiKey(null);
    setHasSessionKey(Boolean(resolveOpenAiApiKey()));
    showSuccessToast("OpenAI key cleared. Demo fallback answers will be used.");
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
        <div className="policy-rag-topbar-actions">
          <button
            type="button"
            className="btn btn-ghost btn-sm policy-key-btn"
            disabled={busy}
            onClick={() => setKeyPanelOpen((open) => !open)}
            data-testid="policy-openai-key-toggle"
            aria-expanded={keyPanelOpen}
            title="Optionally supply an OpenAI API key for live completions"
          >
            <svg
              className="policy-key-icon"
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
            </svg>
            Set OpenAI Key (Optional)
            {hasSessionKey ? <span className="policy-key-dot" title="Key configured" /> : null}
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={busy}
            onClick={() => fileInputRef.current?.click()}
            data-testid="policy-upload-trigger"
          >
            Upload New Policy Document (.pdf / .docx)
          </button>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPT_ATTR}
          className="sr-only"
          data-testid="policy-file-input"
          onChange={handleFileChange}
        />
      </div>

      {keyPanelOpen && (
        <div className="policy-key-panel" data-testid="policy-openai-key-panel">
          <label htmlFor="policy-openai-key-input">
            OpenAI API key (stored in sessionStorage only)
          </label>
          <div className="policy-key-controls">
            <input
              id="policy-openai-key-input"
              type="password"
              autoComplete="off"
              value={apiKeyDraft}
              onChange={(e) => setApiKeyDraft(e.target.value)}
              placeholder="sk-…"
              data-testid="policy-openai-key-input"
            />
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={handleSaveApiKey}
              data-testid="policy-openai-key-save"
            >
              Save
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={handleClearApiKey}
              data-testid="policy-openai-key-clear"
            >
              Clear
            </button>
          </div>
          <p className="muted policy-key-hint">
            When set, answers use gpt-4o-mini. Otherwise a fluent local underwriting advisory is
            generated from retrieved policy clauses.
          </p>
        </div>
      )}

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

      {busy && ingestStage && (
        <p className="policy-ingest-progress" data-testid="policy-ingest-progress" role="status">
          {progressPipelineLabel(ingestStage)}
        </p>
      )}
      {searching && (
        <p className="policy-ingest-progress" data-testid="policy-search-progress" role="status">
          Generating underwriting advisory…
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
          void runSearch(query);
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
            onClick={() => void runSearch(chip)}
          >
            {chip}
          </button>
        ))}
      </div>

      {searched && advice && (
        <div className="policy-rag-results" data-testid="policy-rag-results">
          <div className="policy-advisory-block" data-testid="policy-advisory-block">
            <div className="policy-advisory-header">
              <h3>Underwriting Advisory &amp; Policy Guidance</h3>
              <AdvisoryBanner />
            </div>
            <AdvisoryProse text={advice} />
            <button
              type="button"
              className="btn btn-primary btn-sm policy-insert-advice-btn"
              disabled={insertDisabled}
              onClick={handleInsertAdvice}
              data-testid="insert-advice-btn"
            >
              Insert into Decision Note
            </button>
          </div>

          <details className="policy-citations-disclosure" data-testid="policy-citations-disclosure">
            <summary className="policy-citations-summary">
              Source Document Citations (Audit Ledger) — Show {results.length} Retrieved Chunk
              {results.length === 1 ? "" : "s"}
            </summary>
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
                  </li>
                ))}
              </ul>
            )}
          </details>
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

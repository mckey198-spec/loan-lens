export const SEEDED_POLICY_NAME = "Retail Lending Circular 2024 (Seeded)";

export type IngestProgressStage =
  | "extracting"
  | "chunking"
  | "indexing"
  | "done"
  | "error";

export interface IngestedChunk {
  id: string;
  sourceDoc: string;
  chunkIndex: number;
  content: string;
  wordCount: number;
  pageLabel: string;
}

export interface RetrievalResult {
  chunk: IngestedChunk;
  score: number;
}

const SEEDED_POLICY_TEXT = `
--- Page 1 ---
Retail Lending Circular 2024 — Personal Loan Underwriting Standards
Section 1. Eligibility & Income
1.1 Minimum net monthly salary for salaried applicants is INR 40,000. Applications with verified salary below INR 40,000 require senior underwriter exception and compensating factors (CIBIL ≥ 780 and FOIR ≤ 35%).
1.2 Freelance and gig income may count toward eligibility only when supported by 12 months of bank credits and Form 16 / ITR. Cap freelance contribution at 50% of total assessed income unless contracts exceed 24 months.
1.3 Variable pay (bonus, incentives) is averaged over the last 6 months and may not exceed 30% of assessed monthly income.

--- Page 2 ---
Section 2. FOIR & Obligations
2.1 Fixed Obligations to Income Ratio (FOIR) must not exceed 50% of net monthly income after including proposed EMI and existing EMIs.
2.2 When FOIR is between 45% and 50%, require enhanced documentation: latest 6 salary slips, 6 months bank statements, and employer confirmation letter.
2.3 When FOIR exceeds 50%, decline unless a co-applicant with independent income reduces blended FOIR to ≤ 50%.

--- Page 3 ---
Section 3. Loan Amount & Tenure
3.1 Maximum unsecured personal loan amount is INR 2,500,000 for CIBIL ≥ 750; INR 1,500,000 for CIBIL 700–749; and INR 750,000 for CIBIL 650–699.
3.2 Tenure may not exceed 60 months for salaried applicants or 48 months for self-employed / freelance applicants.
3.3 Loan purpose must be declared; debt consolidation requires settlement letters for all listed obligations.

--- Page 4 ---
Section 4. Credit Bureau & Exceptions
4.1 Minimum CIBIL / bureau score is 700 for standard approval. Scores 650–699 may be considered only with FOIR ≤ 40% and clean repayment history for 24 months.
4.2 Any write-off, settlement, or suit-filed account in the last 36 months is an automatic referral to credit risk.
4.3 Policy citations retrieved by advisory tools are ADVISORY ONLY and must not be treated as a credit decision. Final disposition remains with the human reviewer.
`.trim();

let inMemoryPolicyIndex: IngestedChunk[] = [];
let activePolicyName = SEEDED_POLICY_NAME;
let customPolicyActive = false;
let pdfWorkerConfigured = false;

function derivePageLabel(content: string, chunkIndex: number): string {
  const matches = [...content.matchAll(/---\s*Page\s+(\d+)\s*---/gi)];
  if (matches.length === 0) {
    return `Section / Chunk ${chunkIndex}`;
  }
  const pages = [...new Set(matches.map((m) => m[1]))];
  if (pages.length === 1) {
    return `Page ${pages[0]}`;
  }
  return `Pages ${pages[0]}–${pages[pages.length - 1]}`;
}

function seedDefaultPolicy(): void {
  inMemoryPolicyIndex = chunkText(SEEDED_POLICY_TEXT, SEEDED_POLICY_NAME);
  activePolicyName = SEEDED_POLICY_NAME;
  customPolicyActive = false;
}

seedDefaultPolicy();

async function parsePdf(file: File): Promise<string> {
  const [{ getDocument, GlobalWorkerOptions }, workerSrc] = await Promise.all([
    import("pdfjs-dist"),
    import("pdfjs-dist/build/pdf.worker.min.mjs?url").then((m) => m.default),
  ]);

  if (!pdfWorkerConfigured) {
    GlobalWorkerOptions.workerSrc = workerSrc;
    pdfWorkerConfigured = true;
  }

  const arrayBuffer = await file.arrayBuffer();
  const pdf = await getDocument({ data: new Uint8Array(arrayBuffer) }).promise;
  let fullText = "";

  for (let i = 1; i <= pdf.numPages; i++) {
    const page = await pdf.getPage(i);
    const textContent = await page.getTextContent();
    const pageText = textContent.items
      .map((item) => ("str" in item ? String(item.str) : ""))
      .join(" ");
    fullText += `\n--- Page ${i} ---\n${pageText}`;
  }

  return fullText;
}

async function parseDocx(file: File): Promise<string> {
  const mammoth = await import("mammoth");
  const arrayBuffer = await file.arrayBuffer();
  const result = await mammoth.extractRawText({ arrayBuffer });
  return result.value;
}

/** Parse text from an uploaded File (.pdf, .docx, or plain text). */
export async function parseDocument(file: File): Promise<string> {
  const extension = file.name.split(".").pop()?.toLowerCase();

  if (extension === "docx") {
    return parseDocx(file);
  }

  if (extension === "pdf") {
    return parsePdf(file);
  }

  return file.text();
}

/** Sliding-window chunking (350 words per chunk with 50-word overlap). */
export function chunkText(text: string, docName: string): IngestedChunk[] {
  const words = text.split(/\s+/).filter((w) => w.trim().length > 0);
  const chunkSize = 350;
  const overlap = 50;
  const chunks: IngestedChunk[] = [];

  let idx = 0;
  for (let i = 0; i < words.length; i += chunkSize - overlap) {
    const chunkWords = words.slice(i, i + chunkSize);
    if (chunkWords.length < 20) {
      break;
    }

    const content = chunkWords.join(" ");
    const chunkIndex = idx + 1;
    chunks.push({
      id: `chunk_${Date.now()}_${idx}`,
      sourceDoc: docName,
      chunkIndex,
      content,
      wordCount: chunkWords.length,
      pageLabel: derivePageLabel(content, chunkIndex),
    });
    idx += 1;
  }

  return chunks;
}

/** Parse, chunk, and replace the active in-session policy index. */
export async function ingestPolicyFile(
  file: File,
  onProgress?: (stage: IngestProgressStage) => void,
): Promise<{ docName: string; chunkCount: number }> {
  try {
    onProgress?.("extracting");
    const extractedText = await parseDocument(file);

    onProgress?.("chunking");
    const chunks = chunkText(extractedText, file.name);

    onProgress?.("indexing");
    inMemoryPolicyIndex = chunks;
    activePolicyName = file.name;
    customPolicyActive = true;

    onProgress?.("done");
    return {
      docName: file.name,
      chunkCount: chunks.length,
    };
  } catch (err) {
    onProgress?.("error");
    throw err;
  }
}

export function getActivePolicyName(): string {
  return activePolicyName;
}

export function hasCustomPolicy(): boolean {
  return customPolicyActive;
}

export function getIndexedChunkCount(): number {
  return inMemoryPolicyIndex.length;
}

/** Restore the seeded Retail Lending Circular corpus (tests / reset). */
export function resetToSeededPolicy(): void {
  seedDefaultPolicy();
}

/**
 * Term-frequency keyword matching over the active session index.
 * Scores are normalized into a pseudo-cosine range for display.
 */
export function queryIngestedPolicy(query: string, topK: number = 3): RetrievalResult[] {
  if (inMemoryPolicyIndex.length === 0 || query.trim() === "") {
    return [];
  }

  const queryTerms = query
    .toLowerCase()
    .split(/\W+/)
    .filter((w) => w.length > 2);

  if (queryTerms.length === 0) {
    return [];
  }

  const scored = inMemoryPolicyIndex.map((chunk) => {
    const lowerContent = chunk.content.toLowerCase();
    let score = 0;

    queryTerms.forEach((term) => {
      const occurrences = lowerContent.split(term).length - 1;
      if (occurrences > 0) {
        score += occurrences * 1.5;
      }
    });

    const normalizedScore = Math.min(0.98, 0.65 + (score / (queryTerms.length * 5)) * 0.33);

    return {
      chunk,
      score: score > 0 ? Number(normalizedScore.toFixed(2)) : 0.45,
    };
  });

  return scored
    .filter((r) => r.score > 0.45)
    .sort((a, b) => b.score - a.score)
    .slice(0, topK);
}

const OPENAI_KEY_STORAGE = "loanlens_openai_api_key";

const POLICY_ANSWER_SYSTEM_PROMPT =
  "You are an expert credit underwriting assistant for LoanLens. Answer the underwriter's question concisely using ONLY the provided policy context. Format your response clearly with a direct verdict, policy rationale, and specific clause references. Always conclude with an advisory note.";

/** Resolve optional OpenAI key from sessionStorage or Vite env (demo only). */
export function resolveOpenAiApiKey(): string | null {
  if (typeof sessionStorage !== "undefined") {
    const fromSession = sessionStorage.getItem(OPENAI_KEY_STORAGE)?.trim();
    if (fromSession) {
      return fromSession;
    }
  }
  const fromEnv =
    typeof import.meta !== "undefined"
      ? String(import.meta.env.VITE_OPENAI_API_KEY ?? "").trim()
      : "";
  return fromEnv.length > 0 ? fromEnv : null;
}

/** Persist or clear a user-supplied OpenAI key for this browser session. */
export function setSessionOpenAiApiKey(key: string | null): void {
  if (typeof sessionStorage === "undefined") {
    return;
  }
  const trimmed = key?.trim() ?? "";
  if (trimmed === "") {
    sessionStorage.removeItem(OPENAI_KEY_STORAGE);
    return;
  }
  sessionStorage.setItem(OPENAI_KEY_STORAGE, trimmed);
}

function cleanChunkText(content: string): string {
  return content.replace(/---\s*Page\s+\d+\s*---/gi, " ").replace(/\s+/g, " ").trim();
}

function extractThresholdHighlights(text: string): string[] {
  const highlights: string[] = [];
  const patterns: RegExp[] = [
    /FOIR[^.]{0,80}?(?:\d+\s*%|≤\s*\d+%|>=?\s*\d+%)/gi,
    /(?:minimum|max(?:imum)?)\s+(?:net\s+)?(?:monthly\s+)?salary[^.]{0,60}?INR\s*[\d,]+/gi,
    /INR\s*[\d,]+/gi,
    /tenure[^.]{0,80}?\d+\s*months?/gi,
    /CIBIL[^.]{0,60}?\d{3}/gi,
    /\d+\s*%/g,
  ];
  for (const pattern of patterns) {
    for (const match of text.matchAll(pattern)) {
      const value = match[0].replace(/\s+/g, " ").trim();
      if (value.length >= 3 && !highlights.some((h) => h.toLowerCase() === value.toLowerCase())) {
        highlights.push(value);
      }
      if (highlights.length >= 6) {
        return highlights;
      }
    }
  }
  return highlights;
}

function pickRelevantSentences(content: string, query: string, limit: number = 3): string[] {
  const terms = query
    .toLowerCase()
    .split(/\W+/)
    .filter((w) => w.length > 2);
  const sentences = (cleanChunkText(content).match(/[^.!;]+[.!;]?/g) ?? [])
    .map((s) => s.trim())
    .filter((s) => s.length > 20);

  const ranked = sentences
    .map((sentence) => {
      const lower = sentence.toLowerCase();
      const hits = terms.reduce((acc, term) => (lower.includes(term) ? acc + 1 : acc), 0);
      return { sentence, hits };
    })
    .filter((row) => row.hits > 0)
    .sort((a, b) => b.hits - a.hits);

  const chosen = (ranked.length > 0 ? ranked : sentences.map((sentence) => ({ sentence, hits: 0 })))
    .slice(0, limit)
    .map((row) => row.sentence.replace(/\s+/g, " ").trim());

  return chosen;
}

/**
 * Client-side demo fallback: fluent underwriting paragraph from retrieved chunks.
 * Never dumps raw chunk text as the primary answer.
 */
export function generateLocalPolicyAnswer(
  query: string,
  contextChunks: IngestedChunk[],
): string {
  const trimmedQuery = query.trim();
  if (contextChunks.length === 0) {
    return (
      `Verdict: No strongly matching policy clauses were found for “${trimmedQuery}”.\n\n` +
      "Policy rationale: Refine the question or upload a more specific policy document so the copilot can cite concrete eligibility, FOIR, salary, or tenure rules.\n\n" +
      "Advisory note: This guidance is advisory only and is not a credit decision. Final disposition remains with the human reviewer."
    );
  }

  const clauseRefs = contextChunks
    .map(
      (chunk) =>
        `${chunk.sourceDoc}, ${chunk.pageLabel}, Chunk #${chunk.chunkIndex}`,
    )
    .join("; ");

  const supportingPoints = contextChunks.flatMap((chunk) =>
    pickRelevantSentences(chunk.content, trimmedQuery, 2),
  );
  const uniquePoints = [...new Set(supportingPoints)].slice(0, 4);
  const joinedContext = contextChunks.map((c) => cleanChunkText(c.content)).join(" ");
  const thresholds = extractThresholdHighlights(joinedContext);
  const thresholdPhrase =
    thresholds.length > 0
      ? ` Key thresholds in scope include ${thresholds.slice(0, 4).join("; ")}.`
      : "";

  const lowerQuery = trimmedQuery.toLowerCase();
  let verdictFocus = "Apply the cited policy clauses before disposition.";
  if (/\bfoir\b/.test(lowerQuery)) {
    verdictFocus =
      "Assess FOIR against the policy ceiling (typically 50% of net monthly income, with tighter bands for enhanced documentation or decline).";
  } else if (/salary|40k|income/.test(lowerQuery)) {
    verdictFocus =
      "Confirm verified net monthly salary against the minimum salary gate and any exception path (for example INR 40,000 with compensating factors).";
  } else if (/freelance|gig/.test(lowerQuery)) {
    verdictFocus =
      "Treat freelance or gig income as eligible only when documentation and contribution caps in the policy are met.";
  } else if (/tenure|tenor|months/.test(lowerQuery)) {
    verdictFocus =
      "Confirm proposed tenure does not exceed the salaried (60 months) or self-employed / freelance (48 months) caps.";
  } else if (/loan amount|sanction|limit|inr/.test(lowerQuery)) {
    verdictFocus =
      "Compare the requested amount to the unsecured personal-loan caps tied to bureau score bands.";
  } else if (/cibil|bureau|score/.test(lowerQuery)) {
    verdictFocus =
      "Apply the minimum bureau score and any exception band (for example 650–699 with tighter FOIR) before recommending approval.";
  }

  const rationaleBody =
    uniquePoints.length > 0
      ? uniquePoints.map((point) => `• ${point}`).join("\n")
      : `• Relevant rules were retrieved from ${activePolicyName}; cross-check FOIR, income, tenure, and bureau requirements against the live application facts.`;

  return (
    `Verdict: Regarding “${trimmedQuery}”, ${verdictFocus}${thresholdPhrase}\n\n` +
    `Policy rationale:\n${rationaleBody}\n\n` +
    `Clause references: ${clauseRefs}.\n\n` +
    "Advisory note: This guidance is advisory only and is not a credit decision. Final disposition remains with the human reviewer."
  );
}

async function callOpenAiChatCompletion(
  apiKey: string,
  query: string,
  contextChunks: IngestedChunk[],
): Promise<string> {
  const contextBlock =
    contextChunks.length === 0
      ? "(No policy chunks retrieved.)"
      : contextChunks
          .map(
            (chunk, index) =>
              `[${index + 1}] ${chunk.sourceDoc} · ${chunk.pageLabel} · Chunk #${chunk.chunkIndex}\n${cleanChunkText(chunk.content)}`,
          )
          .join("\n\n");

  const response = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model: "gpt-4o-mini",
      temperature: 0.2,
      messages: [
        { role: "system", content: POLICY_ANSWER_SYSTEM_PROMPT },
        {
          role: "user",
          content:
            `Underwriter question:\n${query.trim()}\n\n` +
            `Policy context (use only this material):\n${contextBlock}\n\n` +
            "Respond with a direct verdict, policy rationale, specific clause references, and a closing advisory note.",
        },
      ],
    }),
  });

  if (!response.ok) {
    throw new Error(`OpenAI request failed (${response.status})`);
  }

  const payload = (await response.json()) as {
    choices?: Array<{ message?: { content?: string } }>;
  };
  const content = payload.choices?.[0]?.message?.content?.trim() ?? "";
  if (content === "") {
    throw new Error("OpenAI returned an empty completion");
  }
  return content;
}

/**
 * Generative synthesis for the underwriting advisory.
 * Uses OpenAI when a key is available; otherwise a fluent local demo answer.
 */
export async function generatePolicyAnswer(
  query: string,
  contextChunks: IngestedChunk[],
): Promise<string> {
  const apiKey = resolveOpenAiApiKey();
  if (apiKey) {
    try {
      return await callOpenAiChatCompletion(apiKey, query, contextChunks);
    } catch {
      // Fall through to deterministic local advisory — never dump raw chunks.
    }
  }
  return generateLocalPolicyAnswer(query, contextChunks);
}

/** Deterministic synthesis from retrieved clauses — never a credit decision. */
export function synthesizeUnderwritingAdvice(
  query: string,
  results: RetrievalResult[],
): string {
  return generateLocalPolicyAnswer(
    query,
    results.map((result) => result.chunk),
  );
}

function summarizeSnippet(content: string, maxChars: number = 220): string {
  const cleaned = cleanChunkText(content);
  if (cleaned.length <= maxChars) {
    return cleaned;
  }
  return `${cleaned.slice(0, maxChars).trim()}…`;
}

/** Format a citation block suitable for appending to a reviewer decision note. */
export function formatCitationForDecisionNote(result: RetrievalResult): string {
  const snippet = summarizeSnippet(result.chunk.content, 320);
  return (
    `[Policy citation — ${result.chunk.sourceDoc}, ${result.chunk.pageLabel}, ` +
    `Chunk #${result.chunk.chunkIndex}, match ${result.score.toFixed(2)}]\n${snippet}`
  );
}

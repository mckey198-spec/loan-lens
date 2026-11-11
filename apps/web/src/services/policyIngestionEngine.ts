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

/** Deterministic synthesis from retrieved clauses — never a credit decision. */
export function synthesizeUnderwritingAdvice(
  query: string,
  results: RetrievalResult[],
): string {
  if (results.length === 0) {
    return (
      `No strongly matching clauses were found for “${query.trim()}”. ` +
      "Refine the question or upload a more specific policy document. " +
      "This output is advisory only and is not a credit decision."
    );
  }

  const bullets = results
    .map(
      (r, i) =>
        `${i + 1}. [${r.chunk.sourceDoc} · ${r.chunk.pageLabel} · Chunk #${r.chunk.chunkIndex} · score ${r.score.toFixed(2)}] ` +
        summarizeSnippet(r.chunk.content),
    )
    .join("\n");

  return (
    `Synthesized policy compliance guidance for “${query.trim()}” ` +
    `(active corpus: ${activePolicyName}):\n\n${bullets}\n\n` +
    "Apply these citations alongside FOIR and bureau checks. " +
    "ADVISORY ONLY — NOT A CREDIT DECISION."
  );
}

function summarizeSnippet(content: string, maxChars: number = 220): string {
  const cleaned = content.replace(/---\s*Page\s+\d+\s*---/gi, " ").replace(/\s+/g, " ").trim();
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

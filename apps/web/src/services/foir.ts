import type { FoirResult } from "../types/domain";

/**
 * Client-side preview of `modules/calculations/engine.py`
 * (standard_amortizing_emi_v1). The backend Decimal engine remains the
 * authoritative source for underwriting decisions.
 */

export const DEFAULT_ANNUAL_RATE_PCT = 10.5;
export const DEFAULT_TENURE_MONTHS = 36;
export const DEFAULT_FOIR_THRESHOLD_PCT = 50;

const FOIR_UNDEFINABLE = 999.99;

function round2(value: number): number {
  return Math.round((value + Number.EPSILON) * 100) / 100;
}

export function computeProposedEmi(
  principal: number,
  annualRatePct: number,
  tenureMonths: number,
): number {
  if (tenureMonths <= 0) {
    throw new RangeError("tenureMonths must be positive");
  }
  if (principal < 0) {
    throw new RangeError("principal must be non-negative");
  }
  if (annualRatePct === 0) {
    return round2(principal / tenureMonths);
  }
  const monthlyRate = annualRatePct / 1200;
  const factor = (1 + monthlyRate) ** tenureMonths;
  return round2((principal * monthlyRate * factor) / (factor - 1));
}

export function computeFoirPct(
  totalObligations: number,
  monthlyNetIncome: number,
): number {
  if (monthlyNetIncome === 0) {
    return totalObligations === 0 ? 0 : FOIR_UNDEFINABLE;
  }
  return round2((totalObligations / monthlyNetIncome) * 100);
}

export interface FoirInputs {
  amount: number;
  monthlyNetIncome: number;
  existingMonthlyEmis: number;
  annualRatePct?: number;
  tenureMonths?: number;
  foirThresholdPct?: number;
}

export function calculateFoir(inputs: FoirInputs): FoirResult {
  const foirThresholdPct = inputs.foirThresholdPct ?? DEFAULT_FOIR_THRESHOLD_PCT;
  const proposedEmi = computeProposedEmi(
    inputs.amount,
    inputs.annualRatePct ?? DEFAULT_ANNUAL_RATE_PCT,
    inputs.tenureMonths ?? DEFAULT_TENURE_MONTHS,
  );
  const totalObligations = round2(inputs.existingMonthlyEmis + proposedEmi);
  const foirPct = computeFoirPct(totalObligations, inputs.monthlyNetIncome);
  return {
    proposedEmi,
    totalObligations,
    foirPct,
    foirThresholdPct,
    foirPassed: foirPct <= foirThresholdPct,
    netDisposableIncome: round2(inputs.monthlyNetIncome - totalObligations),
  };
}

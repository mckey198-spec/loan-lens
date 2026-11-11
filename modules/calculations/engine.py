"""Pure deterministic financial calculators (ADR-009; Architecture §8).

All monetary and ratio arithmetic uses ``decimal.Decimal`` with
``ROUND_HALF_UP``. No binary floating-point operations.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from modules.calculations.schemas import CalculationInputs, CalculationResult

_MONEY = Decimal("0.01")
_PCT = Decimal("0.01")
_TWELVE = Decimal("12")
_HUNDRED = Decimal("100")
_ONE = Decimal("1")
_ZERO = Decimal("0")
_ZERO_MONEY = Decimal("0.00")
_FOIR_UNDEFINABLE = Decimal("999.99")
_FORMULA_ID = "standard_amortizing_emi_v1"


def _as_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _as_pct(value: Decimal) -> Decimal:
    return value.quantize(_PCT, rounding=ROUND_HALF_UP)


def _monthly_interest_rate(annual_interest_rate_pct: Decimal) -> Decimal:
    """Monthly rate r = annual_pct / (12 × 100)."""
    return annual_interest_rate_pct / (_TWELVE * _HUNDRED)


def _amortization_factor(monthly_rate: Decimal, tenure_months: int) -> Decimal:
    return (_ONE + monthly_rate) ** tenure_months


def compute_proposed_emi(
    principal: Decimal,
    annual_interest_rate_pct: Decimal,
    tenure_months: int,
) -> Decimal:
    """
    Standard amortizing EMI.

    Zero verified rate uses principal / tenure (Architecture §8).
    """
    if tenure_months <= 0:
        raise ValueError("tenure_months must be positive")
    if principal < _ZERO:
        raise ValueError("principal must be non-negative")

    if annual_interest_rate_pct == _ZERO:
        return _as_money(principal / Decimal(tenure_months))

    monthly_rate = _monthly_interest_rate(annual_interest_rate_pct)
    factor = _amortization_factor(monthly_rate, tenure_months)
    numerator = principal * monthly_rate * factor
    denominator = factor - _ONE
    return _as_money(numerator / denominator)


def compute_max_qualifying_loan_amount(
    max_allowable_emi: Decimal,
    annual_interest_rate_pct: Decimal,
    tenure_months: int,
) -> Decimal:
    """
    Reverse amortization: principal supportable by ``max_allowable_emi``.

    When max allowable EMI is non-positive, qualifying principal is zero.
    """
    if tenure_months <= 0:
        raise ValueError("tenure_months must be positive")
    if max_allowable_emi <= _ZERO:
        return _ZERO_MONEY

    if annual_interest_rate_pct == _ZERO:
        return _as_money(max_allowable_emi * Decimal(tenure_months))

    monthly_rate = _monthly_interest_rate(annual_interest_rate_pct)
    factor = _amortization_factor(monthly_rate, tenure_months)
    numerator = max_allowable_emi * (factor - _ONE)
    denominator = monthly_rate * factor
    return _as_money(numerator / denominator)


def compute_foir_pct(
    total_obligations: Decimal,
    monthly_net_income: Decimal,
) -> Decimal:
    """
    FOIR% = (total_obligations / monthly_net_income) × 100.

    Zero income is safe: undefined FOIR when obligations > 0 uses the
    saturated sentinel ``999.99`` (NUMERIC(5,2) ceiling); both zero → 0.
    """
    if monthly_net_income == _ZERO:
        if total_obligations == _ZERO:
            return _ZERO_MONEY
        return _FOIR_UNDEFINABLE
    return _as_pct((total_obligations / monthly_net_income) * _HUNDRED)


def compute_financial_metrics(inputs: CalculationInputs) -> CalculationResult:
    """Compute EMI, FOIR, NDI, and max qualifying loan from frozen inputs."""
    monthly_rate = _monthly_interest_rate(inputs.annual_interest_rate_pct)
    proposed_emi = compute_proposed_emi(
        inputs.requested_loan_amount,
        inputs.annual_interest_rate_pct,
        inputs.tenure_months,
    )
    total_obligations = _as_money(inputs.monthly_obligations + proposed_emi)
    foir_pct = compute_foir_pct(total_obligations, inputs.monthly_net_income)
    foir_threshold_pct = _as_pct(inputs.max_allowed_foir_pct)
    foir_passed = foir_pct <= foir_threshold_pct
    net_disposable_income = _as_money(
        inputs.monthly_net_income - total_obligations
    )

    max_allowable_emi = _as_money(
        (inputs.monthly_net_income * inputs.max_allowed_foir_pct / _HUNDRED)
        - inputs.monthly_obligations
    )
    max_qualifying_loan_amount = compute_max_qualifying_loan_amount(
        max_allowable_emi,
        inputs.annual_interest_rate_pct,
        inputs.tenure_months,
    )

    details: dict[str, str | int | bool] = {
        "formula": _FORMULA_ID,
        "rounding": "ROUND_HALF_UP",
        "requested_loan_amount": str(inputs.requested_loan_amount),
        "annual_interest_rate_pct": str(inputs.annual_interest_rate_pct),
        "tenure_months": inputs.tenure_months,
        "monthly_interest_rate": str(monthly_rate),
        "monthly_net_income": str(inputs.monthly_net_income),
        "monthly_obligations": str(inputs.monthly_obligations),
        "max_allowed_foir_pct": str(inputs.max_allowed_foir_pct),
        "max_allowable_emi": str(max_allowable_emi),
        "zero_income_foir_safe": inputs.monthly_net_income == _ZERO,
    }

    return CalculationResult(
        proposed_emi=proposed_emi,
        total_obligations=total_obligations,
        foir_pct=foir_pct,
        foir_threshold_pct=foir_threshold_pct,
        foir_passed=foir_passed,
        net_disposable_income=net_disposable_income,
        max_qualifying_loan_amount=max_qualifying_loan_amount,
        calculation_details=dict(details),
    )

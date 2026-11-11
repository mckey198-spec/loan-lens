"""Extraction result schemas (Architecture §7; four value layers ADR-014 lineage)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ExtractionFieldName = Literal[
    "applicant_name",
    "pan",
    "date_of_birth",
    "employer_name",
    "gross_salary",
    "net_salary",
    "statement_start_date",
    "statement_end_date",
    "account_number",
    "ifsc",
    "average_monthly_balance",
    "total_credits",
    "total_debits",
]

BBox = tuple[Decimal, Decimal, Decimal, Decimal]

EXTRACTOR_VERSION = "1.0.0"


class ExtractedFieldResult(BaseModel):
    """
    One extracted key-value fact.

    Layer 1: ``raw_value`` preserves source text exactly.
    Layer 2: ``normalized_value`` holds typed JSON (ISO dates, numeric strings).
    """

    model_config = ConfigDict(from_attributes=True)

    field_name: ExtractionFieldName
    raw_value: str
    normalized_value: dict[str, object] | None = None
    confidence: Decimal = Field(ge=Decimal("0.0"), le=Decimal("1.0"))
    page_number: int | None = Field(default=None, ge=1)
    bbox: BBox | None = None

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: object) -> Decimal:
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    @field_validator("bbox", mode="before")
    @classmethod
    def _coerce_bbox(cls, value: object) -> BBox | None:
        if value is None:
            return None
        if isinstance(value, tuple) and len(value) == 4:
            return (
                Decimal(str(value[0])),
                Decimal(str(value[1])),
                Decimal(str(value[2])),
                Decimal(str(value[3])),
            )
        if isinstance(value, list) and len(value) == 4:
            return (
                Decimal(str(value[0])),
                Decimal(str(value[1])),
                Decimal(str(value[2])),
                Decimal(str(value[3])),
            )
        raise TypeError("bbox must be a 4-tuple of decimal-compatible values")


class SalarySlipExtraction(BaseModel):
    """Structured salary-slip summary (persisted to salary_slip_extractions)."""

    model_config = ConfigDict(from_attributes=True)

    employer_name: str
    pay_period_month: date
    basic_pay: Decimal | None = None
    allowances: Decimal | None = None
    deductions: Decimal | None = None
    net_pay: Decimal
    employee_pan: str | None = None

    @field_validator(
        "basic_pay",
        "allowances",
        "deductions",
        "net_pay",
        mode="before",
    )
    @classmethod
    def _coerce_money(cls, value: object) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))


class BankStatementMonthlySummary(BaseModel):
    """One month of bank-statement aggregates."""

    model_config = ConfigDict(from_attributes=True)

    statement_month: date
    total_credits: Decimal
    total_debits: Decimal
    closing_balance: Decimal
    identified_salary_credits: Decimal = Decimal("0.00")
    salary_credit_count: int = 0

    @field_validator(
        "total_credits",
        "total_debits",
        "closing_balance",
        "identified_salary_credits",
        mode="before",
    )
    @classmethod
    def _coerce_money(cls, value: object) -> Decimal:
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))


class BankStatementSummary(BaseModel):
    """Account-level metadata fields plus monthly summary lines."""

    model_config = ConfigDict(from_attributes=True)

    fields: list[ExtractedFieldResult] = Field(default_factory=list)
    monthly_summaries: list[BankStatementMonthlySummary] = Field(
        default_factory=list
    )


class DocumentExtractionResult(BaseModel):
    """Full parser output for one document."""

    model_config = ConfigDict(from_attributes=True)

    extractor_name: str
    extractor_version: str = EXTRACTOR_VERSION
    fields: list[ExtractedFieldResult] = Field(default_factory=list)
    salary_slip_data: SalarySlipExtraction | None = None
    bank_summaries: list[BankStatementMonthlySummary] | None = None
    raw_payload: dict[str, object] = Field(default_factory=dict)


class ExtractionError(Exception):
    """Raised when document text is unreadable or required fields cannot be parsed."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

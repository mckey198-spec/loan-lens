"""Deterministic Underwriting Assessment PDF renderer (ReportLab; Architecture §10)."""

from __future__ import annotations

import json
from decimal import Decimal
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from modules.reporting.schemas import (
    BR12_ADVISORY_BANNER,
    AssessmentReportPackage,
)


class _NumberedCanvas(canvas.Canvas):
    """Canvas that stamps ``Page X of Y`` after the document is complete."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Keep content streams uncompressed so export markers stay searchable.
        kwargs["pageCompression"] = 0
        canvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states: list[dict[str, Any]] = []

    def showPage(self) -> None:  # noqa: N802 — ReportLab API
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        page_count = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_page_number(page_count)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def _draw_page_number(self, page_count: int) -> None:
        page_number = self._pageNumber
        label = f"Page {page_number} of {page_count}"
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.black)
        self.drawCentredString(A4[0] / 2.0, 12 * mm, label)


def _fmt_decimal(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return format(value, "f")


def _fmt_bool(value: bool | None) -> str:
    if value is None:
        return "—"
    return "Yes" if value else "No"


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=14,
            spaceAfter=8,
            alignment=TA_CENTER,
        ),
        "section": ParagraphStyle(
            "SectionHeading",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11,
            spaceBefore=12,
            spaceAfter=6,
            textColor=colors.HexColor("#1a1a1a"),
        ),
        "body": ParagraphStyle(
            "BodyTextCustom",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            alignment=TA_LEFT,
        ),
        "small": ParagraphStyle(
            "SmallText",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
        ),
        "warning": ParagraphStyle(
            "AdvisoryWarning",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#7a1f1f"),
            alignment=TA_CENTER,
        ),
    }


def _kv_table(rows: list[tuple[str, str]]) -> Table:
    data = [[Paragraph(_esc(k), _styles()["small"]), Paragraph(_esc(v), _styles()["small"])]
            for k, v in rows]
    table = Table(data, colWidths=[55 * mm, 115 * mm])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f2f2")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
            ]
        )
    )
    return table


def _section_table(headers: list[str], rows: list[list[str]]) -> Table:
    styles = _styles()
    header_cells = [Paragraph(_esc(h), styles["small"]) for h in headers]
    body = [
        [Paragraph(_esc(cell), styles["small"]) for cell in row]
        for row in rows
    ]
    table = Table([header_cells, *body], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#bbbbbb")),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _advisory_banner_flowables(styles: dict[str, ParagraphStyle]) -> list[Any]:
    banner = Paragraph(_esc(BR12_ADVISORY_BANNER), styles["warning"])
    box = Table([[banner]], colWidths=[170 * mm])
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fff3cd")),
                ("BOX", (0, 0), (-1, -1), 2, colors.HexColor("#b45309")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return [box, Spacer(1, 6)]


def build_assessment_pdf(package: AssessmentReportPackage) -> bytes:
    """
    Render a deterministic assessment PDF from a frozen report package.

    Compression is disabled so fixture assertions can read markers as plain
    bytes. Generation is pure: no database writes.
    """
    buffer = BytesIO()
    styles = _styles()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=20 * mm,
        title=f"LoanLens Assessment {package.application_id}",
        author="LoanLens",
        subject=(
            f"snapshot_hash={package.snapshot.snapshot_hash}; "
            f"recommendation={package.decision.recommendation or ''}"
        ),
        keywords=BR12_ADVISORY_BANNER,
        compress=0,
        pageCompression=0,
    )

    story: list[Any] = []
    story.append(Paragraph("LoanLens Underwriting Assessment Package", styles["title"]))
    story.append(
        Paragraph(
            _esc(f"Application ID: {package.application_id}"),
            styles["body"],
        )
    )
    story.append(Spacer(1, 4))

    # Section 1 — Application Header & Frozen Snapshot Metadata
    story.append(
        Paragraph(
            "Section 1: Application Header & Frozen Snapshot Metadata",
            styles["section"],
        )
    )
    snap = package.snapshot
    story.append(
        _kv_table(
            [
                ("Reference Code", snap.application_reference_code),
                ("Application Status", snap.application_status),
                ("Snapshot Hash", snap.snapshot_hash),
                ("As-of Date", snap.as_of_date.isoformat()),
                ("Policy Version", snap.policy_version_label),
                ("Policy Version ID", str(snap.policy_version_id)),
                ("Snapshot ID", str(snap.snapshot_id)),
            ]
        )
    )

    # Section 2 — Final Assessment Decision
    story.append(
        Paragraph("Section 2: Final Assessment Decision", styles["section"])
    )
    decision = package.decision
    story.append(
        _kv_table(
            [
                ("Assessment ID", str(decision.assessment_id)),
                ("Recommendation", decision.recommendation or "—"),
                (
                    "Reviewer User",
                    decision.reviewer_full_name
                    or (
                        str(decision.reviewer_user_id)
                        if decision.reviewer_user_id
                        else "—"
                    ),
                ),
                (
                    "Timestamp",
                    decision.decided_at.isoformat() if decision.decided_at else "—",
                ),
                ("Reviewer Notes", decision.reviewer_notes or "—"),
            ]
        )
    )

    # Section 3 — Financial Calculations & Ratio Table
    story.append(
        Paragraph(
            "Section 3: Financial Calculations & Ratio Table",
            styles["section"],
        )
    )
    fin = package.financials
    story.append(
        _section_table(
            ["Metric", "Value"],
            [
                ["Gross Monthly Income", _fmt_decimal(fin.monthly_gross_income)],
                ["Net Monthly Income", _fmt_decimal(fin.monthly_net_income)],
                ["Monthly Obligations", _fmt_decimal(fin.monthly_obligations)],
                ["Proposed EMI", _fmt_decimal(fin.proposed_emi)],
                ["Total Obligations", _fmt_decimal(fin.total_obligations)],
                ["FOIR %", _fmt_decimal(fin.foir_pct)],
                ["FOIR Threshold %", _fmt_decimal(fin.foir_threshold_pct)],
                ["FOIR Passed", _fmt_bool(fin.foir_passed)],
                [
                    "Qualifying Loan Amount",
                    _fmt_decimal(fin.max_qualifying_loan_amount),
                ],
                [
                    "Net Disposable Income",
                    _fmt_decimal(fin.net_disposable_income),
                ],
            ],
        )
    )

    # Section 4 — Deterministic Rules Matrix
    story.append(
        Paragraph(
            "Section 4: Deterministic Rules Matrix (BR-01 through BR-11)",
            styles["section"],
        )
    )
    if package.findings:
        finding_rows: list[list[str]] = []
        for finding in package.findings:
            diff_text = json.dumps(finding.diff_payload_json, sort_keys=True, default=str)
            finding_rows.append(
                [
                    finding.rule_code,
                    finding.status,
                    finding.severity,
                    finding.message,
                    diff_text,
                ]
            )
        story.append(
            _section_table(
                ["Rule", "Status", "Severity", "Message", "Diff Payload"],
                finding_rows,
            )
        )
    else:
        story.append(
            Paragraph("No verification findings recorded for this run.", styles["body"])
        )

    # Section 5 — Reviewer Overrides & Dispositions
    story.append(
        Paragraph(
            "Section 5: Reviewer Overrides & Dispositions",
            styles["section"],
        )
    )
    if package.dispositions:
        story.append(
            _section_table(
                ["Rule", "Action", "Justification", "Created At"],
                [
                    [
                        d.rule_code or "—",
                        d.action,
                        d.justification,
                        d.created_at.isoformat(),
                    ]
                    for d in package.dispositions
                ],
            )
        )
    else:
        story.append(Paragraph("No finding dispositions recorded.", styles["body"]))

    story.append(Spacer(1, 4))
    story.append(Paragraph("Field Corrections", styles["body"]))
    if package.corrections:
        story.append(
            _section_table(
                ["Field", "Corrected Value", "Reason", "Created At"],
                [
                    [
                        c.field_name or "—",
                        (
                            c.corrected_value_text
                            if c.corrected_value_text is not None
                            else (
                                _fmt_decimal(c.corrected_value_numeric)
                                if c.corrected_value_numeric is not None
                                else (
                                    c.corrected_value_date.isoformat()
                                    if c.corrected_value_date is not None
                                    else "—"
                                )
                            )
                        ),
                        c.reason,
                        c.created_at.isoformat(),
                    ]
                    for c in package.corrections
                ],
            )
        )
    else:
        story.append(Paragraph("No field corrections recorded.", styles["body"]))

    # Section 6 — Lexical Policy Citations
    story.append(
        Paragraph("Section 6: Lexical Policy Citations", styles["section"])
    )
    if package.policy_citations:
        story.append(
            _section_table(
                ["Topic", "Section Label", "Quoted Clause"],
                [
                    [c.topic, c.section_label, c.content_text]
                    for c in package.policy_citations
                ],
            )
        )
    else:
        story.append(
            Paragraph(
                "No lexical policy citations retrieved for this package.",
                styles["body"],
            )
        )

    # Section 7 — Advisory AI Observations (BR-12)
    story.append(
        Paragraph("Section 7: Advisory AI Observations", styles["section"])
    )
    story.extend(_advisory_banner_flowables(styles))
    if package.ai_observations:
        for obs in package.ai_observations:
            story.append(
                _kv_table(
                    [
                        ("Model ID", obs.model_id),
                        ("Is AI Generated", "true" if obs.is_ai_generated else "false"),
                        ("Created At", obs.created_at.isoformat()),
                        ("Observation", obs.observation_text),
                        (
                            "Risk Factors",
                            "; ".join(obs.risk_factors) if obs.risk_factors else "—",
                        ),
                        (
                            "Strengths",
                            "; ".join(obs.strengths) if obs.strengths else "—",
                        ),
                    ]
                )
            )
            story.append(Spacer(1, 4))
    else:
        story.append(
            Paragraph(
                "No labelled AI observations were recorded for this run.",
                styles["body"],
            )
        )

    doc.build(story, canvasmaker=_NumberedCanvas)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    if not pdf_bytes.startswith(b"%PDF-"):
        raise RuntimeError("ReportLab produced non-PDF output")
    return pdf_bytes

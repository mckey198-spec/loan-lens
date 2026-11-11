"""Assessment PDF reporting (Architecture §10; M13)."""

from modules.reporting.schemas import BR12_ADVISORY_BANNER, AssessmentReportPackage
from modules.reporting.service import generate_assessment_report

__all__ = [
    "BR12_ADVISORY_BANNER",
    "AssessmentReportPackage",
    "generate_assessment_report",
]

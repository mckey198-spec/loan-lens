"""Re-export of the governance audit chain verifier (Database Design §4.8)."""

from modules.governance.verifier import (
    GENESIS_EVENT_HASH,
    AuditVerificationReport,
    verify_audit_log_integrity,
)

__all__ = [
    "GENESIS_EVENT_HASH",
    "AuditVerificationReport",
    "verify_audit_log_integrity",
]

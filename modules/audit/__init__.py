"""Audit integrity helpers.

Architecture §4 places audit ownership under ``modules.governance``. This
package re-exports the verifier for the M15 deliverable import path.
"""

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

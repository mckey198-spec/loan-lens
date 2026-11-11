"""Malware scanner interface: ClamAV clamd client and configurable mock (ADR-025)."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol

# Standard EICAR test string (not malware; used by scanners and the mock).
EICAR_TEST_BYTES = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)

ScannerBackend = Literal["clamav", "mock"]


class ScanStatus(str, Enum):
    CLEAN = "clean"
    INFECTED = "infected"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ScanOutcome:
    status: ScanStatus
    scanner_name: str
    signature_version: str | None = None
    threat_name: str | None = None
    detail: str | None = None


class MalwareScanner(Protocol):
    def scan_bytes(self, data: bytes) -> ScanOutcome:
        """Scan file bytes; never raises for infected results — returns status."""


class MockMalwareScanner:
    """
    Test / fallback scanner.

    Returns infected for the EICAR test payload; clean otherwise.
    Optional force_status overrides detection for fixtures.
    """

    def __init__(
        self,
        *,
        force_status: ScanStatus | None = None,
        force_detail: str | None = None,
    ) -> None:
        self._force_status = force_status
        self._force_detail = force_detail

    def scan_bytes(self, data: bytes) -> ScanOutcome:
        if self._force_status is not None:
            if self._force_status is ScanStatus.INFECTED:
                return ScanOutcome(
                    status=ScanStatus.INFECTED,
                    scanner_name="mock",
                    threat_name="EICAR-Test-File",
                    detail=self._force_detail or "forced infected",
                )
            if self._force_status is ScanStatus.ERROR:
                return ScanOutcome(
                    status=ScanStatus.ERROR,
                    scanner_name="mock",
                    detail=self._force_detail or "forced scanner error",
                )
            return ScanOutcome(
                status=ScanStatus.CLEAN,
                scanner_name="mock",
                signature_version="mock-1",
            )

        if EICAR_TEST_BYTES in data:
            return ScanOutcome(
                status=ScanStatus.INFECTED,
                scanner_name="mock",
                threat_name="EICAR-Test-File",
                detail="EICAR test string detected",
            )
        return ScanOutcome(
            status=ScanStatus.CLEAN,
            scanner_name="mock",
            signature_version="mock-1",
        )


class ClamdScanner:
    """Lightweight clamd INSTREAM client (no pyclamd dependency)."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout_seconds: float = 30.0,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout_seconds
        self._chunk_size = chunk_size

    def ping(self) -> str | None:
        """Return clamd version string, or raise OSError if unreachable."""
        return self._ping_version()

    def scan_bytes(self, data: bytes) -> ScanOutcome:
        try:
            version = self._ping_version()
            response = self._instream(data)
        except OSError as exc:
            return ScanOutcome(
                status=ScanStatus.ERROR,
                scanner_name="clamav",
                detail=f"clamd connection failed: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 — surface as scanner error
            return ScanOutcome(
                status=ScanStatus.ERROR,
                scanner_name="clamav",
                detail=f"clamd scan failed: {exc}",
            )

        normalized = response.strip()
        if normalized.endswith("OK"):
            return ScanOutcome(
                status=ScanStatus.CLEAN,
                scanner_name="clamav",
                signature_version=version,
            )
        if "FOUND" in normalized:
            threat = normalized.removeprefix("stream:").removesuffix("FOUND").strip()
            return ScanOutcome(
                status=ScanStatus.INFECTED,
                scanner_name="clamav",
                signature_version=version,
                threat_name=threat or None,
                detail=normalized,
            )
        return ScanOutcome(
            status=ScanStatus.ERROR,
            scanner_name="clamav",
            signature_version=version,
            detail=normalized or "empty clamd response",
        )

    def _connect(self) -> socket.socket:
        sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
        sock.settimeout(self._timeout)
        return sock

    def _ping_version(self) -> str | None:
        with self._connect() as sock:
            sock.sendall(b"zVERSION\0")
            raw = sock.recv(4096)
        text = raw.decode("utf-8", errors="replace").strip().rstrip("\0")
        return text or None

    def _instream(self, data: bytes) -> str:
        with self._connect() as sock:
            sock.sendall(b"zINSTREAM\0")
            view = memoryview(data)
            offset = 0
            while offset < len(view):
                chunk = view[offset : offset + self._chunk_size]
                sock.sendall(len(chunk).to_bytes(4, "big"))
                sock.sendall(chunk)
                offset += len(chunk)
            sock.sendall((0).to_bytes(4, "big"))
            raw = sock.recv(4096)
        return raw.decode("utf-8", errors="replace").strip().rstrip("\0")


def build_malware_scanner(
    *,
    backend: ScannerBackend,
    host: str,
    port: int,
    fallback_to_mock: bool = True,
) -> MalwareScanner:
    """
    Construct a scanner for the configured backend.

    When backend is clamav and the daemon is unreachable, optionally fall back
    to the mock in non-strict environments (tests / local without Compose).
    """
    if backend == "mock":
        return MockMalwareScanner()

    clam = ClamdScanner(host, port)
    if not fallback_to_mock:
        return clam
    try:
        clam.ping()
    except OSError:
        return MockMalwareScanner()
    return clam


__all__ = [
    "EICAR_TEST_BYTES",
    "ClamdScanner",
    "MalwareScanner",
    "MockMalwareScanner",
    "ScanOutcome",
    "ScanStatus",
    "ScannerBackend",
    "build_malware_scanner",
]

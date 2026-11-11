"""Magic-byte / file-signature checks for accepted upload formats (M04-T02).

No third-party magic library: TOOLS.md lists that dependency as Conditional.
Checks are pure, deterministic byte-prefix matches for PDF, JPEG, and PNG.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

FileKind = Literal["pdf", "jpeg", "png"]

PDF_MAGIC = b"%PDF-"
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_KIND_BY_MAGIC: tuple[tuple[bytes, FileKind], ...] = (
    (PNG_MAGIC, "png"),
    (JPEG_MAGIC, "jpeg"),
    (PDF_MAGIC, "pdf"),
)

_EXT_TO_KIND: dict[str, FileKind] = {
    ".pdf": "pdf",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".png": "png",
}

_KIND_TO_MIME: dict[FileKind, str] = {
    "pdf": "application/pdf",
    "jpeg": "image/jpeg",
    "png": "image/png",
}

_ALLOWED_MIMES: frozenset[str] = frozenset(_KIND_TO_MIME.values())


def sniff_kind(data: bytes) -> FileKind | None:
    """Return the detected kind from magic bytes, or None if unrecognized."""
    for magic, kind in _KIND_BY_MAGIC:
        if data.startswith(magic):
            return kind
    return None


def extension_kind(filename: str) -> FileKind | None:
    """Map a filename extension to an accepted kind."""
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    return _EXT_TO_KIND.get(suffix)


def mime_for_kind(kind: FileKind) -> str:
    return _KIND_TO_MIME[kind]


def is_allowed_mime(mime_type: str) -> bool:
    return mime_type.lower().split(";", 1)[0].strip() in _ALLOWED_MIMES


def validate_file_signature(
    *,
    filename: str,
    declared_mime: str,
    data: bytes,
) -> tuple[FileKind, str]:
    """
    Validate extension, declared MIME, and magic bytes agree.

    Returns (kind, normalized_mime). Raises ValueError on mismatch or unknown type.
    """
    if not data:
        raise ValueError("file is empty")

    kind_from_ext = extension_kind(filename)
    if kind_from_ext is None:
        raise ValueError("file extension is not an accepted format")

    normalized_mime = declared_mime.lower().split(";", 1)[0].strip()
    expected_mime = _KIND_TO_MIME[kind_from_ext]
    if normalized_mime != expected_mime:
        raise ValueError("declared MIME type does not match file extension")

    kind_from_bytes = sniff_kind(data)
    if kind_from_bytes is None:
        raise ValueError("file signature is not a recognized PDF, JPEG, or PNG")
    if kind_from_bytes != kind_from_ext:
        raise ValueError("file signature does not match file extension")

    return kind_from_ext, expected_mime


__all__ = [
    "JPEG_MAGIC",
    "PDF_MAGIC",
    "PNG_MAGIC",
    "FileKind",
    "extension_kind",
    "is_allowed_mime",
    "mime_for_kind",
    "sniff_kind",
    "validate_file_signature",
]

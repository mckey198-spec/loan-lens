"""Document upload, metadata, and authorized content streaming (M04-T03 / M04-T04)."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from psycopg_pool import AsyncConnectionPool

from modules.identity.deps import get_pool
from modules.identity.rbac import require_role
from modules.identity.schemas import UserProfile
from modules.intake.scanner import MalwareScanner, build_malware_scanner
from modules.intake.schemas import DocumentResponse
from modules.intake.service import (
    get_document,
    list_documents,
    prepare_document_download,
    upload_document,
)
from shared.errors import AppError, request_id_from_request
from shared.settings import Settings, get_settings

router = APIRouter(
    prefix="/api/v1/applications/{application_id}/documents",
    tags=["documents"],
)

require_loan_officer_or_admin = require_role("loan_officer", "admin")
require_download_roles = require_role("loan_officer", "reviewer", "admin")

DocumentTypeForm = Literal[
    "identity_proof",
    "pan_card",
    "address_proof",
    "salary_slip",
    "bank_statement",
    "form_16",
    "itr",
    "gst_return",
    "business_proof",
    "existing_loan_statement",
    "unsupported",
    "other",
]


@lru_cache
def _cached_scanner(
    backend: str,
    host: str,
    port: int,
    fallback: bool,
) -> MalwareScanner:
    return build_malware_scanner(
        backend=backend,  # type: ignore[arg-type]
        host=host,
        port=port,
        fallback_to_mock=fallback,
    )


def get_malware_scanner(
    settings: Annotated[Settings, Depends(get_settings)],
) -> MalwareScanner:
    fallback = settings.CLAMAV_FALLBACK_TO_MOCK and settings.APP_ENV != "production"
    return _cached_scanner(
        settings.CLAMAV_BACKEND,
        settings.CLAMAV_HOST,
        settings.CLAMAV_PORT,
        fallback,
    )


@router.post("", response_model=DocumentResponse, status_code=201)
async def upload_document_endpoint(
    application_id: UUID,
    request: Request,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    actor: Annotated[UserProfile, Depends(require_loan_officer_or_admin)],
    settings: Annotated[Settings, Depends(get_settings)],
    scanner: Annotated[MalwareScanner, Depends(get_malware_scanner)],
    file: Annotated[UploadFile, File()],
    document_type: Annotated[DocumentTypeForm, Form()],
) -> DocumentResponse:
    filename = file.filename or "upload.bin"
    declared_mime = file.content_type or "application/octet-stream"
    max_bytes = settings.MAX_UPLOAD_SIZE_BYTES
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise AppError(
            code="DOCUMENT_TOO_LARGE",
            message="Upload exceeds the maximum allowed size.",
            status_code=413,
            details={
                "max_upload_size_bytes": max_bytes,
                "byte_size": len(data),
            },
        )

    return await upload_document(
        pool,
        application_id=application_id,
        actor_user_id=actor.id,
        actor_role=actor.role,
        filename=filename,
        declared_mime=declared_mime,
        data=data,
        document_type=document_type,
        scanner=scanner,
        settings=settings,
        request_id=request_id_from_request(request),
    )


@router.get("", response_model=list[DocumentResponse])
async def list_documents_endpoint(
    application_id: UUID,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _actor: Annotated[UserProfile, Depends(require_download_roles)],
) -> list[DocumentResponse]:
    return await list_documents(pool, application_id=application_id)


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document_endpoint(
    application_id: UUID,
    document_id: UUID,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    _actor: Annotated[UserProfile, Depends(require_download_roles)],
) -> DocumentResponse:
    return await get_document(
        pool,
        application_id=application_id,
        document_id=document_id,
    )


@router.get("/{document_id}/content")
async def get_document_content_endpoint(
    application_id: UUID,
    document_id: UUID,
    request: Request,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    actor: Annotated[UserProfile, Depends(require_download_roles)],
) -> FileResponse:
    content = await prepare_document_download(
        pool,
        application_id=application_id,
        document_id=document_id,
        actor_user_id=actor.id,
        request_id=request_id_from_request(request),
    )
    return FileResponse(
        path=content.path,
        media_type=content.mime_type,
        filename=content.original_filename,
        content_disposition_type="inline",
    )


__all__ = ["get_malware_scanner", "router"]

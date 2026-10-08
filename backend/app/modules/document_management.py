from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..core.database import get_engine
from ..core.security import TokenIdentity, verify_access_token


# ============================================================
# MQEIP DOCUMENT MANAGEMENT INTELLIGENCE
# DOCUMENT BLOK 1 — DATABASE & DATA MODELS
# ============================================================

router = APIRouter(
    prefix="/api/v1/documents",
    tags=["Document Management Intelligence"],
)


class DocumentCreate(BaseModel):
    document_number: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=255)
    document_type: str = Field(min_length=1, max_length=100)
    department: str = Field(min_length=1, max_length=100)
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    description: Optional[str] = None
    confidentiality: str = "internal"
    retention_years: int = Field(default=5, ge=0, le=100)


class DocumentVersionCreate(BaseModel):
    version_label: str = Field(min_length=1, max_length=50)
    file_name: str = Field(min_length=1, max_length=255)
    storage_key: str = Field(min_length=1, max_length=1000)
    mime_type: Optional[str] = None
    file_size: int = Field(ge=0)
    checksum_sha256: str = Field(min_length=64, max_length=64)
    change_summary: Optional[str] = None


class DocumentStatusUpdate(BaseModel):
    status: str
    remarks: Optional[str] = None


def serialize_document_row(row):
    result = dict(row)

    for key, value in result.items():
        if isinstance(value, UUID):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, Decimal):
            result[key] = float(value)

    return result


def init_document_tables():
    engine = get_engine()

    with engine.begin() as conn:

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mq_documents (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(100) NOT NULL,
                document_number VARCHAR(100) NOT NULL,
                title VARCHAR(255) NOT NULL,
                document_type VARCHAR(100) NOT NULL,
                department VARCHAR(100) NOT NULL,
                vessel_id VARCHAR(100),
                vessel_name VARCHAR(255),
                description TEXT,
                confidentiality VARCHAR(30)
                    NOT NULL DEFAULT 'internal',
                retention_years INTEGER NOT NULL DEFAULT 5,
                status VARCHAR(30) NOT NULL DEFAULT 'draft',
                current_version_id UUID,
                created_by VARCHAR(255) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE (tenant_id, document_number)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mq_document_versions (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(100) NOT NULL,
                document_id UUID NOT NULL
                    REFERENCES mq_documents(id),
                version_label VARCHAR(50) NOT NULL,
                file_name VARCHAR(255) NOT NULL,
                storage_key VARCHAR(1000) NOT NULL,
                mime_type VARCHAR(255),
                file_size BIGINT NOT NULL,
                checksum_sha256 VARCHAR(64) NOT NULL,
                change_summary TEXT,
                uploaded_by VARCHAR(255) NOT NULL,
                uploaded_at TIMESTAMPTZ NOT NULL,
                UNIQUE (tenant_id, document_id, version_label)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mq_document_activity (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(100) NOT NULL,
                document_id UUID NOT NULL
                    REFERENCES mq_documents(id),
                action VARCHAR(100) NOT NULL,
                remarks TEXT,
                performed_by VARCHAR(255) NOT NULL,
                performed_at TIMESTAMPTZ NOT NULL
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS
                idx_mq_documents_tenant_status
            ON mq_documents (tenant_id, status)
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS
                idx_mq_document_versions_lookup
            ON mq_document_versions
                (tenant_id, document_id, uploaded_at)
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS
                idx_mq_document_activity_lookup
            ON mq_document_activity
                (tenant_id, document_id, performed_at)
        """))

  # ============================================================
# DOCUMENT BLOK 2
# DOCUMENT REGISTRY & LIFECYCLE MANAGEMENT
# ============================================================

VALID_CONFIDENTIALITY = {
    "public", "internal", "confidential", "restricted"
}

VALID_DOCUMENT_STATUS = {
    "draft", "under_review", "approved",
    "published", "archived", "obsolete"
}

ALLOWED_DOCUMENT_TRANSITIONS = {
    "draft": {"under_review", "archived"},
    "under_review": {"draft", "approved"},
    "approved": {"published", "archived"},
    "published": {"archived", "obsolete"},
    "archived": {"draft", "obsolete"},
    "obsolete": set(),
}


@router.get(
    "",
    summary="List Registered Documents",
)
def list_documents(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_document_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM mq_documents
                WHERE tenant_id = :tenant_id
                ORDER BY created_at DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_document_row(row) for row in rows]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Register New Document",
)
def create_document(
    payload: DocumentCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_document_tables()

    confidentiality = payload.confidentiality.strip().lower()

    if confidentiality not in VALID_CONFIDENTIALITY:
        raise HTTPException(
            status_code=400,
            detail="Invalid confidentiality classification.",
        )

    document_id = uuid4()
    now = datetime.now(timezone.utc)
    engine = get_engine()

    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO mq_documents (
                        id, tenant_id, document_number,
                        title, document_type, department,
                        vessel_id, vessel_name, description,
                        confidentiality, retention_years,
                        status, created_by, created_at, updated_at
                    )
                    VALUES (
                        :id, :tenant_id, :document_number,
                        :title, :document_type, :department,
                        :vessel_id, :vessel_name, :description,
                        :confidentiality, :retention_years,
                        'draft', :created_by, :created_at, :updated_at
                    )
                """),
                {
                    "id": document_id,
                    "tenant_id": identity.tenant_id,
                    "document_number": payload.document_number,
                    "title": payload.title,
                    "document_type": payload.document_type,
                    "department": payload.department,
                    "vessel_id": payload.vessel_id,
                    "vessel_name": payload.vessel_name,
                    "description": payload.description,
                    "confidentiality": confidentiality,
                    "retention_years": payload.retention_years,
                    "created_by": identity.user_id,
                    "created_at": now,
                    "updated_at": now,
                },
            )

            conn.execute(
                text("""
                    INSERT INTO mq_document_activity (
                        id, tenant_id, document_id,
                        action, remarks, performed_by, performed_at
                    )
                    VALUES (
                        :id, :tenant_id, :document_id,
                        'document_registered', :remarks,
                        :performed_by, :performed_at
                    )
                """),
                {
                    "id": uuid4(),
                    "tenant_id": identity.tenant_id,
                    "document_id": document_id,
                    "remarks": "Document registered as draft.",
                    "performed_by": identity.user_id,
                    "performed_at": now,
                },
            )

    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Document number already exists or data conflicts.",
        ) from exc

    return {
        "id": str(document_id),
        "document_number": payload.document_number,
        "status": "draft",
        "created_at": now.isoformat(),
    }


@router.get(
    "/{document_id}",
    summary="Get Document Details",
)
def get_document(
    document_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_document_tables()
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text("""
                SELECT *
                FROM mq_documents
                WHERE id = :document_id
                  AND tenant_id = :tenant_id
            """),
            {
                "document_id": document_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Document not found.",
        )

    return serialize_document_row(row)


@router.post(
    "/{document_id}/status",
    summary="Update Document Lifecycle Status",
)
def update_document_status(
    document_id: UUID,
    payload: DocumentStatusUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_document_tables()

    new_status = payload.status.strip().lower()

    if new_status not in VALID_DOCUMENT_STATUS:
        raise HTTPException(
            status_code=400,
            detail="Invalid document status.",
        )

    engine = get_engine()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        existing = conn.execute(
            text("""
                SELECT status, current_version_id
                FROM mq_documents
                WHERE id = :document_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "document_id": document_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="Document not found.",
            )

        old_status = existing["status"]

        if new_status not in ALLOWED_DOCUMENT_TRANSITIONS.get(
            old_status, set()
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid lifecycle transition: "
                    f"{old_status} -> {new_status}"
                ),
            )

        if new_status in {"approved", "published"}:
            if existing["current_version_id"] is None:
                raise HTTPException(
                    status_code=400,
                    detail="Document version is required.",
                )

        conn.execute(
            text("""
                UPDATE mq_documents
                SET status = :status,
                    updated_at = :updated_at
                WHERE id = :document_id
                  AND tenant_id = :tenant_id
            """),
            {
                "status": new_status,
                "updated_at": now,
                "document_id": document_id,
                "tenant_id": identity.tenant_id,
            },
        )

        conn.execute(
            text("""
                INSERT INTO mq_document_activity (
                    id, tenant_id, document_id,
                    action, remarks, performed_by, performed_at
                )
                VALUES (
                    :id, :tenant_id, :document_id,
                    'status_changed', :remarks,
                    :performed_by, :performed_at
                )
            """),
            {
                "id": uuid4(),
                "tenant_id": identity.tenant_id,
                "document_id": document_id,
                "remarks": (
                    f"{old_status} -> {new_status}. "
                    f"{payload.remarks or ''}"
                ),
                "performed_by": identity.user_id,
                "performed_at": now,
            },
        )

    return {
        "id": str(document_id),
        "previous_status": old_status,
        "status": new_status,
        "updated_at": now.isoformat(),
    }

# ============================================================
# DOCUMENT BLOK 3
# VERSION CONTROL & DOCUMENT HISTORY
# ============================================================

import re


@router.post(
    "/{document_id}/versions",
    status_code=status.HTTP_201_CREATED,
    summary="Register New Document Version",
)
def create_document_version(
    document_id: UUID,
    payload: DocumentVersionCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_document_tables()

    checksum = payload.checksum_sha256.strip().lower()

    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise HTTPException(
            status_code=400,
            detail="Invalid SHA-256 checksum.",
        )

    engine = get_engine()
    version_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        document = conn.execute(
            text("""
                SELECT id, status, current_version_id
                FROM mq_documents
                WHERE id = :document_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "document_id": document_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if document is None:
            raise HTTPException(
                status_code=404,
                detail="Document not found.",
            )

        if document["status"] not in {
            "draft", "under_review"
        }:
            raise HTTPException(
                status_code=409,
                detail=(
                    "New versions require a draft or "
                    "under-review document."
                ),
            )

        duplicate = conn.execute(
            text("""
                SELECT id
                FROM mq_document_versions
                WHERE tenant_id = :tenant_id
                  AND document_id = :document_id
                  AND version_label = :version_label
            """),
            {
                "tenant_id": identity.tenant_id,
                "document_id": document_id,
                "version_label": payload.version_label,
            },
        ).first()

        if duplicate is not None:
            raise HTTPException(
                status_code=409,
                detail="Document version already exists.",
            )

        conn.execute(
            text("""
                INSERT INTO mq_document_versions (
                    id,
                    tenant_id,
                    document_id,
                    version_label,
                    file_name,
                    storage_key,
                    mime_type,
                    file_size,
                    checksum_sha256,
                    change_summary,
                    uploaded_by,
                    uploaded_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :document_id,
                    :version_label,
                    :file_name,
                    :storage_key,
                    :mime_type,
                    :file_size,
                    :checksum_sha256,
                    :change_summary,
                    :uploaded_by,
                    :uploaded_at
                )
            """),
            {
                "id": version_id,
                "tenant_id": identity.tenant_id,
                "document_id": document_id,
                "version_label": payload.version_label,
                "file_name": payload.file_name,
                "storage_key": payload.storage_key,
                "mime_type": payload.mime_type,
                "file_size": payload.file_size,
                "checksum_sha256": checksum,
                "change_summary": payload.change_summary,
                "uploaded_by": identity.user_id,
                "uploaded_at": now,
            },
        )

        conn.execute(
            text("""
                UPDATE mq_documents
                SET current_version_id = :version_id,
                    updated_at = :updated_at
                WHERE id = :document_id
                  AND tenant_id = :tenant_id
            """),
            {
                "version_id": version_id,
                "updated_at": now,
                "document_id": document_id,
                "tenant_id": identity.tenant_id,
            },
        )

        conn.execute(
            text("""
                INSERT INTO mq_document_activity (
                    id,
                    tenant_id,
                    document_id,
                    action,
                    remarks,
                    performed_by,
                    performed_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :document_id,
                    'version_registered',
                    :remarks,
                    :performed_by,
                    :performed_at
                )
            """),
            {
                "id": uuid4(),
                "tenant_id": identity.tenant_id,
                "document_id": document_id,
                "remarks": (
                    f"Version {payload.version_label} registered. "
                    f"{payload.change_summary or ''}"
                ),
                "performed_by": identity.user_id,
                "performed_at": now,
            },
        )

    return {
        "id": str(version_id),
        "document_id": str(document_id),
        "version_label": payload.version_label,
        "file_name": payload.file_name,
        "checksum_sha256": checksum,
        "registered_at": now.isoformat(),
    }


@router.get(
    "/{document_id}/versions",
    summary="List Document Version History",
)
def list_document_versions(
    document_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_document_tables()
    engine = get_engine()

    with engine.begin() as conn:
        document = conn.execute(
            text("""
                SELECT id, current_version_id
                FROM mq_documents
                WHERE id = :document_id
                  AND tenant_id = :tenant_id
            """),
            {
                "document_id": document_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if document is None:
            raise HTTPException(
                status_code=404,
                detail="Document not found.",
            )

        rows = conn.execute(
            text("""
                SELECT
                    id,
                    document_id,
                    version_label,
                    file_name,
                    mime_type,
                    file_size,
                    checksum_sha256,
                    change_summary,
                    uploaded_by,
                    uploaded_at
                FROM mq_document_versions
                WHERE tenant_id = :tenant_id
                  AND document_id = :document_id
                ORDER BY uploaded_at DESC, id DESC
            """),
            {
                "tenant_id": identity.tenant_id,
                "document_id": document_id,
            },
        ).mappings().all()

    result = []

    for row in rows:
        item = serialize_document_row(row)
        item["is_current"] = (
            row["id"] == document["current_version_id"]
        )
        result.append(item)

    return result


@router.get(
    "/{document_id}/versions/{version_id}",
    summary="Get Document Version Metadata",
)
def get_document_version(
    document_id: UUID,
    version_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_document_tables()
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text("""
                SELECT
                    v.id,
                    v.document_id,
                    v.version_label,
                    v.file_name,
                    v.mime_type,
                    v.file_size,
                    v.checksum_sha256,
                    v.change_summary,
                    v.uploaded_by,
                    v.uploaded_at,
                    d.current_version_id
                FROM mq_document_versions v
                JOIN mq_documents d
                  ON d.id = v.document_id
                 AND d.tenant_id = v.tenant_id
                WHERE v.id = :version_id
                  AND v.document_id = :document_id
                  AND v.tenant_id = :tenant_id
            """),
            {
                "version_id": version_id,
                "document_id": document_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Document version not found.",
        )

    result = serialize_document_row(row)
    result["is_current"] = (
        row["id"] == row["current_version_id"]
    )
    result.pop("current_version_id", None)

    return result


@router.get(
    "/{document_id}/history",
    summary="Get Document Activity History",
)
def get_document_history(
    document_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_document_tables()
    engine = get_engine()

    with engine.begin() as conn:
        document = conn.execute(
            text("""
                SELECT id
                FROM mq_documents
                WHERE id = :document_id
                  AND tenant_id = :tenant_id
            """),
            {
                "document_id": document_id,
                "tenant_id": identity.tenant_id,
            },
        ).first()

        if document is None:
            raise HTTPException(
                status_code=404,
                detail="Document not found.",
            )

        rows = conn.execute(
            text("""
                SELECT
                    id,
                    document_id,
                    action,
                    remarks,
                    performed_by,
                    performed_at
                FROM mq_document_activity
                WHERE tenant_id = :tenant_id
                  AND document_id = :document_id
                ORDER BY performed_at DESC, id DESC
                LIMIT 500
            """),
            {
                "tenant_id": identity.tenant_id,
                "document_id": document_id,
            },
        ).mappings().all()

    return [serialize_document_row(row) for row in rows]

# ============================================================
# DOCUMENT BLOK 4
# DASHBOARD, SEARCH & DOCUMENT INTELLIGENCE
# ============================================================

from fastapi import Query


@router.get(
    "/intelligence/search",
    summary="Search Enterprise Documents",
)
def search_documents(
    keyword: Optional[str] = Query(default=None, max_length=200),
    department: Optional[str] = None,
    document_type: Optional[str] = None,
    document_status: Optional[str] = None,
    vessel_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_document_tables()
    engine = get_engine()

    if document_status:
        document_status = document_status.strip().lower()
        if document_status not in VALID_DOCUMENT_STATUS:
            raise HTTPException(
                status_code=400,
                detail="Invalid document status.",
            )

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    id,
                    document_number,
                    title,
                    document_type,
                    department,
                    vessel_id,
                    vessel_name,
                    confidentiality,
                    retention_years,
                    status,
                    current_version_id,
                    created_by,
                    created_at,
                    updated_at
                FROM mq_documents
                WHERE tenant_id = :tenant_id
                  AND (
                    :keyword IS NULL
                    OR document_number ILIKE
                        '%' || :keyword || '%'
                    OR title ILIKE
                        '%' || :keyword || '%'
                    OR description ILIKE
                        '%' || :keyword || '%'
                  )
                  AND (
                    :department IS NULL
                    OR department = :department
                  )
                  AND (
                    :document_type IS NULL
                    OR document_type = :document_type
                  )
                  AND (
                    :document_status IS NULL
                    OR status = :document_status
                  )
                  AND (
                    :vessel_id IS NULL
                    OR vessel_id = :vessel_id
                  )
                ORDER BY updated_at DESC
                LIMIT :limit
            """),
            {
                "tenant_id": identity.tenant_id,
                "keyword": keyword,
                "department": department,
                "document_type": document_type,
                "document_status": document_status,
                "vessel_id": vessel_id,
                "limit": limit,
            },
        ).mappings().all()

    return {
        "total_returned": len(rows),
        "documents": [
            serialize_document_row(row)
            for row in rows
        ],
    }


@router.get(
    "/intelligence/dashboard",
    summary="Enterprise Document Dashboard",
)
def document_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_document_tables()
    engine = get_engine()

    with engine.begin() as conn:
        status_rows = conn.execute(
            text("""
                SELECT status, COUNT(*) AS total
                FROM mq_documents
                WHERE tenant_id = :tenant_id
                GROUP BY status
                ORDER BY status
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        department_rows = conn.execute(
            text("""
                SELECT
                    department,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (
                        WHERE status = 'published'
                    ) AS published,
                    COUNT(*) FILTER (
                        WHERE status = 'under_review'
                    ) AS under_review
                FROM mq_documents
                WHERE tenant_id = :tenant_id
                GROUP BY department
                ORDER BY total DESC, department
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        confidentiality_rows = conn.execute(
            text("""
                SELECT confidentiality, COUNT(*) AS total
                FROM mq_documents
                WHERE tenant_id = :tenant_id
                GROUP BY confidentiality
                ORDER BY confidentiality
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        version_metrics = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_versions,
                    COUNT(DISTINCT document_id)
                        AS versioned_documents
                FROM mq_document_versions
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        total_documents = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM mq_documents
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

    return {
        "total_documents": total_documents,
        "by_status": [
            serialize_document_row(row)
            for row in status_rows
        ],
        "by_department": [
            serialize_document_row(row)
            for row in department_rows
        ],
        "by_confidentiality": [
            serialize_document_row(row)
            for row in confidentiality_rows
        ],
        "version_metrics": serialize_document_row(
            version_metrics
        ),
    }


@router.get(
    "/intelligence/quality",
    summary="Document Quality and Governance Intelligence",
)
def document_quality_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_document_tables()
    engine = get_engine()

    with engine.begin() as conn:
        metrics = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_documents,
                    COUNT(*) FILTER (
                        WHERE current_version_id IS NULL
                    ) AS missing_version,
                    COUNT(*) FILTER (
                        WHERE status = 'draft'
                    ) AS draft_documents,
                    COUNT(*) FILTER (
                        WHERE status = 'under_review'
                    ) AS pending_review,
                    COUNT(*) FILTER (
                        WHERE status = 'obsolete'
                    ) AS obsolete_documents,
                    COUNT(*) FILTER (
                        WHERE status = 'published'
                          AND current_version_id IS NULL
                    ) AS published_without_version,
                    COUNT(*) FILTER (
                        WHERE confidentiality IN (
                            'confidential', 'restricted'
                        )
                    ) AS sensitive_documents
                FROM mq_documents
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        stale_reviews = conn.execute(
            text("""
                SELECT
                    id,
                    document_number,
                    title,
                    department,
                    updated_at
                FROM mq_documents
                WHERE tenant_id = :tenant_id
                  AND status = 'under_review'
                  AND updated_at <
                      CURRENT_TIMESTAMP - INTERVAL '30 days'
                ORDER BY updated_at ASC
                LIMIT 100
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "metrics": serialize_document_row(metrics),
        "reviews_older_than_30_days": [
            serialize_document_row(row)
            for row in stale_reviews
        ],
        "note": (
            "Governance indicators are based on "
            "registered document metadata."
        ),
    }


@router.get(
    "/intelligence/audit-trail",
    summary="Enterprise Document Activity Audit Trail",
)
def document_audit_trail(
    limit: int = Query(default=200, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_document_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    a.id,
                    a.document_id,
                    d.document_number,
                    d.title,
                    d.department,
                    a.action,
                    a.remarks,
                    a.performed_by,
                    a.performed_at
                FROM mq_document_activity a
                JOIN mq_documents d
                  ON d.id = a.document_id
                 AND d.tenant_id = a.tenant_id
                WHERE a.tenant_id = :tenant_id
                ORDER BY a.performed_at DESC, a.id DESC
                LIMIT :limit
            """),
            {
                "tenant_id": identity.tenant_id,
                "limit": limit,
            },
        ).mappings().all()

    return [
        serialize_document_row(row)
        for row in rows
    ]


# ============================================================
# INITIALIZE DOCUMENT MANAGEMENT DATABASE
# ============================================================

init_document_tables()

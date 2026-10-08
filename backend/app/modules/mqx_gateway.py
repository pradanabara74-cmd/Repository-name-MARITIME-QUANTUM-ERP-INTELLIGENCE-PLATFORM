# ============================================================
# MQEIP - MARITIME QUANTUM EXCHANGE (MQX)
# BLOK 1 - ENTERPRISE API & INTEGRATION GATEWAY FOUNDATION
# ============================================================

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone, timedelta
from typing import Any, Literal, Optional
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from ..core.database import get_engine
from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/mqx",
    tags=["MQX Enterprise API & Integration Gateway"],
)

UTC = timezone.utc

INTEGRATION_TYPES = (
    "erp",
    "fleet",
    "crew",
    "finance",
    "procurement",
    "pms",
    "hsse",
    "iot",
    "weather",
    "ais",
    "custom",
)

INTEGRATION_STATUSES = (
    "draft",
    "active",
    "paused",
    "disabled",
)

DELIVERY_STATUSES = (
    "pending",
    "processing",
    "delivered",
    "failed",
    "cancelled",
)


# ============================================================
# BASE MODEL
# ============================================================

class MQXBaseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )


# ============================================================
# INTEGRATION REGISTRATION MODEL
# ============================================================

class MQXIntegrationCreate(MQXBaseModel):
    integration_name: str = Field(
        min_length=3,
        max_length=150,
    )

    integration_type: Literal[
        "erp",
        "fleet",
        "crew",
        "finance",
        "procurement",
        "pms",
        "hsse",
        "iot",
        "weather",
        "ais",
        "custom",
    ]

    provider_name: str = Field(
        min_length=2,
        max_length=150,
    )

    description: Optional[str] = Field(
        default=None,
        max_length=2000,
    )

    external_system_reference: Optional[str] = Field(
        default=None,
        max_length=150,
    )

    configuration: dict[str, Any] = Field(
        default_factory=dict,
    )


class MQXIntegrationStatusUpdate(MQXBaseModel):
    integration_status: Literal[
        "draft",
        "active",
        "paused",
        "disabled",
    ]


# ============================================================
# ENDPOINT REGISTRATION MODEL
# ============================================================

class MQXEndpointCreate(MQXBaseModel):
    endpoint_name: str = Field(
        min_length=3,
        max_length=150,
    )

    endpoint_url: str = Field(
        min_length=12,
        max_length=2000,
    )

    http_method: Literal[
        "GET",
        "POST",
        "PUT",
        "PATCH",
    ] = "POST"

    event_type: str = Field(
        min_length=2,
        max_length=150,
    )

    timeout_seconds: int = Field(
        default=15,
        ge=1,
        le=60,
    )

    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
    )


# ============================================================
# INTEGRATION EVENT MODEL
# ============================================================

class MQXEventCreate(MQXBaseModel):
    integration_id: UUID

    event_type: str = Field(
        min_length=2,
        max_length=150,
    )

    source_module: str = Field(
        min_length=2,
        max_length=150,
    )

    source_record_id: Optional[str] = Field(
        default=None,
        max_length=150,
    )

    payload: dict[str, Any] = Field(
        default_factory=dict,
    )

    correlation_id: Optional[str] = Field(
        default=None,
        max_length=150,
    )


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def mqx_now():
    return datetime.now(UTC)


def mqx_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


def mqx_decode_json(value):
    if not value:
        return {}

    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def mqx_serialize_row(row):
    result = {}

    for key, value in dict(row).items():
        if isinstance(value, UUID):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
        else:
            result[key] = value

    return result


def mqx_require_admin(identity):
    role = str(
        getattr(identity, "role", "") or ""
    ).strip().lower()

    if role not in {
        "admin",
        "administrator",
        "superadmin",
        "integration_admin",
    }:
        raise HTTPException(
            status_code=403,
            detail="MQX administration permission required.",
        )


def mqx_validate_endpoint_url(url):
    parsed = urlsplit(url)

    if parsed.scheme != "https":
        raise HTTPException(
            status_code=422,
            detail="Integration endpoint must use HTTPS.",
        )

    if not parsed.hostname:
        raise HTTPException(
            status_code=422,
            detail="Integration endpoint hostname is required.",
        )

    if parsed.username or parsed.password:
        raise HTTPException(
            status_code=422,
            detail="Credentials in endpoint URLs are prohibited.",
        )

    if parsed.fragment:
        raise HTTPException(
            status_code=422,
            detail="URL fragments are not supported.",
        )

    # Network destination safety checks will be enforced
    # again before any outbound request is implemented.
    # Registration alone does not authorize network access.

    return url


# ============================================================
# AUDIT TRAIL
# ============================================================

def mqx_record_audit(
    conn,
    identity,
    entity_type,
    entity_id,
    action,
    details=None,
):
    conn.execute(
        text("""
            INSERT INTO mqx_audit_log (
                id,
                tenant_id,
                entity_type,
                entity_id,
                action,
                details,
                actor_id,
                created_at
            )
            VALUES (
                :id,
                :tenant_id,
                :entity_type,
                :entity_id,
                :action,
                :details,
                :actor_id,
                :created_at
            )
        """),
        {
            "id": uuid4(),
            "tenant_id": str(identity.tenant_id),
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "action": action,
            "details": details,
            "actor_id": str(identity.user_id),
            "created_at": mqx_now(),
        },
    )


# ============================================================
# POSTGRESQL DATABASE INITIALIZATION
# ============================================================

def init_mqx_tables():
    statements = [

        """
        CREATE TABLE IF NOT EXISTS mqx_integrations (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            integration_name VARCHAR(150) NOT NULL,
            integration_type VARCHAR(50) NOT NULL,
            provider_name VARCHAR(150) NOT NULL,
            description TEXT,
            external_system_reference VARCHAR(150),
            configuration_json TEXT NOT NULL DEFAULT '{}',
            integration_status VARCHAR(30)
                NOT NULL DEFAULT 'draft',
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS mqx_endpoints (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            integration_id UUID NOT NULL,
            endpoint_name VARCHAR(150) NOT NULL,
            endpoint_url TEXT NOT NULL,
            http_method VARCHAR(10) NOT NULL,
            event_type VARCHAR(150) NOT NULL,
            timeout_seconds INTEGER NOT NULL DEFAULT 15,
            max_retries INTEGER NOT NULL DEFAULT 3,
            endpoint_status VARCHAR(30)
                NOT NULL DEFAULT 'disabled',
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS mqx_events (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            integration_id UUID NOT NULL,
            event_type VARCHAR(150) NOT NULL,
            source_module VARCHAR(150) NOT NULL,
            source_record_id TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            correlation_id VARCHAR(150),
            event_status VARCHAR(30)
                NOT NULL DEFAULT 'pending',
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS mqx_deliveries (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            event_id UUID NOT NULL,
            endpoint_id UUID NOT NULL,
            delivery_status VARCHAR(30)
                NOT NULL DEFAULT 'pending',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_http_status INTEGER,
            last_error TEXT,
            next_retry_at TIMESTAMPTZ,
            delivered_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS mqx_audit_log (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            entity_type VARCHAR(100) NOT NULL,
            entity_id TEXT NOT NULL,
            action VARCHAR(100) NOT NULL,
            details TEXT,
            actor_id TEXT,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_mqx_integrations
        ON mqx_integrations (
            tenant_id,
            integration_status
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_mqx_endpoints
        ON mqx_endpoints (
            tenant_id,
            integration_id
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_mqx_events
        ON mqx_events (
            tenant_id,
            integration_id,
            created_at DESC
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_mqx_deliveries
        ON mqx_deliveries (
            tenant_id,
            delivery_status,
            created_at DESC
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_mqx_audit
        ON mqx_audit_log (
            tenant_id,
            created_at DESC
        )
        """,
    ]

    with get_engine().begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


# ============================================================
# END OF MQX BLOK 1
# ============================================================

# ============================================================
# MQEIP - MARITIME QUANTUM EXCHANGE
# BLOK 2 - INTEGRATION REGISTRY & CONNECTION MANAGEMENT
# ============================================================


# ============================================================
# INTEGRATION SERIALIZATION
# ============================================================

def mqx_serialize_integration(row):
    item = mqx_serialize_row(row)

    # Configuration is intentionally not exposed.
    # It may contain operational or sensitive metadata.
    item.pop("configuration_json", None)

    return item


# ============================================================
# REGISTER ENTERPRISE INTEGRATION
# ============================================================

@router.post(
    "/integrations",
    status_code=status.HTTP_201_CREATED,
    summary="Register Enterprise Integration",
)
def mqx_create_integration(
    payload: MQXIntegrationCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    tenant_id = str(identity.tenant_id)
    integration_id = uuid4()
    now = mqx_now()

    # Secret values must never be stored in configuration.
    prohibited_keys = {
        "password",
        "secret",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "private_key",
        "authorization",
        "credentials",
    }

    def check_configuration(value):
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).lower().replace("-", "_")

                if normalized in prohibited_keys:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "Secrets are not permitted in "
                            "integration configuration."
                        ),
                    )

                check_configuration(item)

        elif isinstance(value, list):
            for item in value:
                check_configuration(item)

    check_configuration(payload.configuration)

    configuration_json = mqx_json(
        payload.configuration
    )

    if len(configuration_json.encode("utf-8")) > 65536:
        raise HTTPException(
            status_code=422,
            detail="Integration configuration exceeds 64 KB.",
        )

    with get_engine().begin() as conn:

        existing = conn.execute(
            text("""
                SELECT id
                FROM mqx_integrations
                WHERE tenant_id = :tenant_id
                  AND integration_name = :integration_name
                LIMIT 1
            """),
            {
                "tenant_id": tenant_id,
                "integration_name": payload.integration_name,
            },
        ).first()

        if existing:
            raise HTTPException(
                status_code=409,
                detail="Integration name already exists.",
            )

        conn.execute(
            text("""
                INSERT INTO mqx_integrations (
                    id,
                    tenant_id,
                    integration_name,
                    integration_type,
                    provider_name,
                    description,
                    external_system_reference,
                    configuration_json,
                    integration_status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :integration_name,
                    :integration_type,
                    :provider_name,
                    :description,
                    :external_system_reference,
                    :configuration_json,
                    'draft',
                    :created_by,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "id": integration_id,
                "tenant_id": tenant_id,
                "integration_name": payload.integration_name,
                "integration_type": payload.integration_type,
                "provider_name": payload.provider_name,
                "description": payload.description,
                "external_system_reference":
                    payload.external_system_reference,
                "configuration_json": configuration_json,
                "created_by": str(identity.user_id),
                "created_at": now,
                "updated_at": now,
            },
        )

        mqx_record_audit(
            conn,
            identity,
            "integration",
            integration_id,
            "created",
            mqx_json({
                "integration_name": payload.integration_name,
                "integration_type": payload.integration_type,
                "provider_name": payload.provider_name,
            }),
        )

    return {
        "integration_id": str(integration_id),
        "integration_name": payload.integration_name,
        "integration_type": payload.integration_type,
        "integration_status": "draft",
        "created_at": now.isoformat(),
    }


# ============================================================
# LIST ENTERPRISE INTEGRATIONS
# ============================================================

@router.get(
    "/integrations",
    summary="List Enterprise Integrations",
)
def mqx_list_integrations(
    integration_type: Optional[str] = None,
    integration_status: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    with get_engine().connect() as conn:

        rows = conn.execute(
            text("""
                SELECT
                    id,
                    integration_name,
                    integration_type,
                    provider_name,
                    description,
                    external_system_reference,
                    integration_status,
                    created_by,
                    created_at,
                    updated_at
                FROM mqx_integrations
                WHERE tenant_id = :tenant_id
                  AND (
                      :integration_type IS NULL
                      OR integration_type = :integration_type
                  )
                  AND (
                      :integration_status IS NULL
                      OR integration_status = :integration_status
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
            """),
            {
                "tenant_id": str(identity.tenant_id),
                "integration_type": integration_type,
                "integration_status": integration_status,
                "limit": limit,
            },
        ).mappings().all()

    return {
        "module": "MQX Integration Registry",
        "count": len(rows),
        "integrations": [
            mqx_serialize_integration(row)
            for row in rows
        ],
    }


# ============================================================
# GET ENTERPRISE INTEGRATION DETAILS
# ============================================================

@router.get(
    "/integrations/{integration_id}",
    summary="Get Enterprise Integration Details",
)
def mqx_get_integration(
    integration_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    with get_engine().connect() as conn:

        row = conn.execute(
            text("""
                SELECT
                    id,
                    integration_name,
                    integration_type,
                    provider_name,
                    description,
                    external_system_reference,
                    integration_status,
                    created_by,
                    created_at,
                    updated_at
                FROM mqx_integrations
                WHERE id = :integration_id
                  AND tenant_id = :tenant_id
            """),
            {
                "integration_id": integration_id,
                "tenant_id": str(identity.tenant_id),
            },
        ).mappings().first()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise integration not found.",
            )

        endpoint_count = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM mqx_endpoints
                WHERE integration_id = :integration_id
                  AND tenant_id = :tenant_id
            """),
            {
                "integration_id": integration_id,
                "tenant_id": str(identity.tenant_id),
            },
        ).scalar_one()

    result = mqx_serialize_integration(row)
    result["endpoint_count"] = endpoint_count

    return result


# ============================================================
# UPDATE INTEGRATION STATUS
# ============================================================

@router.patch(
    "/integrations/{integration_id}/status",
    summary="Update Enterprise Integration Status",
)
def mqx_update_integration_status(
    integration_id: UUID,
    payload: MQXIntegrationStatusUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    tenant_id = str(identity.tenant_id)
    now = mqx_now()

    with get_engine().begin() as conn:

        existing = conn.execute(
            text("""
                SELECT
                    integration_name,
                    integration_status
                FROM mqx_integrations
                WHERE id = :integration_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "integration_id": integration_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise integration not found.",
            )

        conn.execute(
            text("""
                UPDATE mqx_integrations
                SET integration_status = :integration_status,
                    updated_at = :updated_at
                WHERE id = :integration_id
                  AND tenant_id = :tenant_id
            """),
            {
                "integration_status":
                    payload.integration_status,
                "updated_at": now,
                "integration_id": integration_id,
                "tenant_id": tenant_id,
            },
        )

        # Pausing or disabling an integration also disables
        # its endpoints to prevent future delivery scheduling.
        if payload.integration_status in {
            "paused",
            "disabled",
        }:
            conn.execute(
                text("""
                    UPDATE mqx_endpoints
                    SET endpoint_status = 'disabled',
                        updated_at = :updated_at
                    WHERE integration_id = :integration_id
                      AND tenant_id = :tenant_id
                """),
                {
                    "updated_at": now,
                    "integration_id": integration_id,
                    "tenant_id": tenant_id,
                },
            )

        mqx_record_audit(
            conn,
            identity,
            "integration",
            integration_id,
            "status_updated",
            mqx_json({
                "previous_status":
                    existing["integration_status"],
                "new_status":
                    payload.integration_status,
            }),
        )

    return {
        "integration_id": str(integration_id),
        "integration_name":
            existing["integration_name"],
        "previous_status":
            existing["integration_status"],
        "integration_status":
            payload.integration_status,
        "updated_at": now.isoformat(),
    }


# ============================================================
# ENTERPRISE INTEGRATION INTELLIGENCE
# ============================================================

@router.get(
    "/intelligence/integrations",
    summary="Enterprise Integration Intelligence",
)
def mqx_integration_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:

        integrations = conn.execute(
            text("""
                SELECT
                    integration_type,
                    integration_status,
                    COUNT(*) AS integration_count
                FROM mqx_integrations
                WHERE tenant_id = :tenant_id
                GROUP BY
                    integration_type,
                    integration_status
                ORDER BY
                    integration_type,
                    integration_status
            """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().all()

        endpoints = conn.execute(
            text("""
                SELECT
                    endpoint_status,
                    COUNT(*) AS endpoint_count
                FROM mqx_endpoints
                WHERE tenant_id = :tenant_id
                GROUP BY endpoint_status
                ORDER BY endpoint_status
            """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().all()

    integration_groups = [
        mqx_serialize_row(row)
        for row in integrations
    ]

    endpoint_groups = [
        mqx_serialize_row(row)
        for row in endpoints
    ]

    return {
        "module": "MQX Enterprise Integration Intelligence",
        "total_integrations": sum(
            item["integration_count"]
            for item in integration_groups
        ),
        "total_endpoints": sum(
            item["endpoint_count"]
            for item in endpoint_groups
        ),
        "integration_groups": integration_groups,
        "endpoint_groups": endpoint_groups,
        "generated_at": mqx_now().isoformat(),
        "note": (
            "Registered integrations do not imply "
            "verified external connectivity."
        ),
    }


# ============================================================
# END OF MQX BLOK 2
# ============================================================

# ============================================================
# MQEIP - MARITIME QUANTUM EXCHANGE
# BLOK 3 - ENDPOINT REGISTRY & SECURE WEBHOOK CONFIGURATION
# ============================================================


# ============================================================
# ENDPOINT STATUS MODEL
# ============================================================

class MQXEndpointStatusUpdate(MQXBaseModel):
    endpoint_status: Literal[
        "active",
        "disabled",
    ]


# ============================================================
# ENDPOINT SERIALIZATION
# ============================================================

def mqx_serialize_endpoint(row):
    item = mqx_serialize_row(row)

    # Do not expose the full destination URL in API responses.
    # Query parameters may contain sensitive information.
    item.pop("endpoint_url", None)

    return item


# ============================================================
# REGISTER INTEGRATION ENDPOINT
# ============================================================

@router.post(
    "/integrations/{integration_id}/endpoints",
    status_code=status.HTTP_201_CREATED,
    summary="Register MQX Integration Endpoint",
)
def mqx_create_endpoint(
    integration_id: UUID,
    payload: MQXEndpointCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    tenant_id = str(identity.tenant_id)
    endpoint_id = uuid4()
    now = mqx_now()

    endpoint_url = mqx_validate_endpoint_url(
        payload.endpoint_url
    )

    # Credentials in URL query strings are prohibited.
    # Query strings are also disallowed to avoid accidentally
    # storing access tokens in endpoint configuration.
    parsed_url = urlsplit(endpoint_url)

    if parsed_url.query:
        raise HTTPException(
            status_code=422,
            detail=(
                "Endpoint URLs must not contain "
                "query parameters."
            ),
        )

    with get_engine().begin() as conn:

        integration = conn.execute(
            text("""
                SELECT
                    id,
                    integration_name,
                    integration_status
                FROM mqx_integrations
                WHERE id = :integration_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "integration_id": integration_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if integration is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise integration not found.",
            )

        if integration["integration_status"] == "disabled":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot register an endpoint "
                    "for a disabled integration."
                ),
            )

        existing = conn.execute(
            text("""
                SELECT id
                FROM mqx_endpoints
                WHERE tenant_id = :tenant_id
                  AND integration_id = :integration_id
                  AND endpoint_name = :endpoint_name
                LIMIT 1
            """),
            {
                "tenant_id": tenant_id,
                "integration_id": integration_id,
                "endpoint_name": payload.endpoint_name,
            },
        ).first()

        if existing:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Endpoint name already exists "
                    "for this integration."
                ),
            )

        conn.execute(
            text("""
                INSERT INTO mqx_endpoints (
                    id,
                    tenant_id,
                    integration_id,
                    endpoint_name,
                    endpoint_url,
                    http_method,
                    event_type,
                    timeout_seconds,
                    max_retries,
                    endpoint_status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :integration_id,
                    :endpoint_name,
                    :endpoint_url,
                    :http_method,
                    :event_type,
                    :timeout_seconds,
                    :max_retries,
                    'disabled',
                    :created_by,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "id": endpoint_id,
                "tenant_id": tenant_id,
                "integration_id": integration_id,
                "endpoint_name": payload.endpoint_name,
                "endpoint_url": endpoint_url,
                "http_method": payload.http_method,
                "event_type": payload.event_type,
                "timeout_seconds": payload.timeout_seconds,
                "max_retries": payload.max_retries,
                "created_by": str(identity.user_id),
                "created_at": now,
                "updated_at": now,
            },
        )

        mqx_record_audit(
            conn,
            identity,
            "endpoint",
            endpoint_id,
            "created",
            mqx_json({
                "integration_id": str(integration_id),
                "endpoint_name": payload.endpoint_name,
                "event_type": payload.event_type,
                "http_method": payload.http_method,
            }),
        )

    return {
        "endpoint_id": str(endpoint_id),
        "integration_id": str(integration_id),
        "endpoint_name": payload.endpoint_name,
        "endpoint_status": "disabled",
        "created_at": now.isoformat(),
        "note": (
            "Endpoint registered but not activated. "
            "No outbound connection has been attempted."
        ),
    }


# ============================================================
# LIST INTEGRATION ENDPOINTS
# ============================================================

@router.get(
    "/integrations/{integration_id}/endpoints",
    summary="List MQX Integration Endpoints",
)
def mqx_list_endpoints(
    integration_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:

        integration = conn.execute(
            text("""
                SELECT id, integration_name
                FROM mqx_integrations
                WHERE id = :integration_id
                  AND tenant_id = :tenant_id
            """),
            {
                "integration_id": integration_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if integration is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise integration not found.",
            )

        rows = conn.execute(
            text("""
                SELECT
                    id,
                    integration_id,
                    endpoint_name,
                    http_method,
                    event_type,
                    timeout_seconds,
                    max_retries,
                    endpoint_status,
                    created_by,
                    created_at,
                    updated_at
                FROM mqx_endpoints
                WHERE tenant_id = :tenant_id
                  AND integration_id = :integration_id
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
            """),
            {
                "tenant_id": tenant_id,
                "integration_id": integration_id,
                "limit": limit,
            },
        ).mappings().all()

    return {
        "integration_id": str(integration_id),
        "integration_name": integration["integration_name"],
        "count": len(rows),
        "endpoints": [
            mqx_serialize_endpoint(row)
            for row in rows
        ],
    }


# ============================================================
# GET ENDPOINT DETAILS
# ============================================================

@router.get(
    "/endpoints/{endpoint_id}",
    summary="Get MQX Endpoint Details",
)
def mqx_get_endpoint(
    endpoint_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    with get_engine().connect() as conn:

        row = conn.execute(
            text("""
                SELECT
                    id,
                    integration_id,
                    endpoint_name,
                    http_method,
                    event_type,
                    timeout_seconds,
                    max_retries,
                    endpoint_status,
                    created_by,
                    created_at,
                    updated_at
                FROM mqx_endpoints
                WHERE id = :endpoint_id
                  AND tenant_id = :tenant_id
            """),
            {
                "endpoint_id": endpoint_id,
                "tenant_id": str(identity.tenant_id),
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="MQX endpoint not found.",
        )

    return mqx_serialize_endpoint(row)


# ============================================================
# ACTIVATE OR DISABLE ENDPOINT
# ============================================================

@router.patch(
    "/endpoints/{endpoint_id}/status",
    summary="Update MQX Endpoint Status",
)
def mqx_update_endpoint_status(
    endpoint_id: UUID,
    payload: MQXEndpointStatusUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    tenant_id = str(identity.tenant_id)
    now = mqx_now()

    with get_engine().begin() as conn:

        endpoint = conn.execute(
            text("""
                SELECT
                    e.id,
                    e.endpoint_name,
                    e.endpoint_status,
                    e.integration_id,
                    i.integration_status
                FROM mqx_endpoints e
                JOIN mqx_integrations i
                  ON i.id = e.integration_id
                 AND i.tenant_id = e.tenant_id
                WHERE e.id = :endpoint_id
                  AND e.tenant_id = :tenant_id
                FOR UPDATE OF e, i
            """),
            {
                "endpoint_id": endpoint_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if endpoint is None:
            raise HTTPException(
                status_code=404,
                detail="MQX endpoint not found.",
            )

        if (
            payload.endpoint_status == "active"
            and endpoint["integration_status"] != "active"
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "The parent integration must be active "
                    "before enabling this endpoint."
                ),
            )

        conn.execute(
            text("""
                UPDATE mqx_endpoints
                SET endpoint_status = :endpoint_status,
                    updated_at = :updated_at
                WHERE id = :endpoint_id
                  AND tenant_id = :tenant_id
            """),
            {
                "endpoint_status": payload.endpoint_status,
                "updated_at": now,
                "endpoint_id": endpoint_id,
                "tenant_id": tenant_id,
            },
        )

        mqx_record_audit(
            conn,
            identity,
            "endpoint",
            endpoint_id,
            "status_updated",
            mqx_json({
                "previous_status":
                    endpoint["endpoint_status"],
                "new_status":
                    payload.endpoint_status,
            }),
        )

    return {
        "endpoint_id": str(endpoint_id),
        "endpoint_name": endpoint["endpoint_name"],
        "previous_status": endpoint["endpoint_status"],
        "endpoint_status": payload.endpoint_status,
        "updated_at": now.isoformat(),
        "note": (
            "Endpoint status updated. "
            "Outbound HTTP delivery is not enabled "
            "by this operation."
        ),
    }


# ============================================================
# ENDPOINT SECURITY CONFIGURATION REPORT
# ============================================================

@router.get(
    "/intelligence/endpoint-security",
    summary="MQX Endpoint Security Configuration Report",
)
def mqx_endpoint_security_report(
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    with get_engine().connect() as conn:

        rows = conn.execute(
            text("""
                SELECT
                    e.id,
                    e.endpoint_name,
                    e.endpoint_url,
                    e.http_method,
                    e.endpoint_status,
                    i.integration_name,
                    i.integration_status
                FROM mqx_endpoints e
                JOIN mqx_integrations i
                  ON i.id = e.integration_id
                 AND i.tenant_id = e.tenant_id
                WHERE e.tenant_id = :tenant_id
                ORDER BY e.created_at DESC, e.id DESC
                LIMIT :limit
            """),
            {
                "tenant_id": str(identity.tenant_id),
                "limit": limit,
            },
        ).mappings().all()

    results = []

    for row in rows:
        parsed = urlsplit(row["endpoint_url"])

        checks = {
            "https_scheme": parsed.scheme == "https",
            "hostname_present": bool(parsed.hostname),
            "no_url_credentials": not (
                parsed.username or parsed.password
            ),
            "no_query_parameters": not bool(parsed.query),
            "no_url_fragment": not bool(parsed.fragment),
            "parent_integration_active": (
                row["integration_status"] == "active"
            ),
        }

        configuration_valid = all(checks.values())

        results.append({
            "endpoint_id": str(row["id"]),
            "endpoint_name": row["endpoint_name"],
            "integration_name": row["integration_name"],
            "endpoint_status": row["endpoint_status"],
            "configuration_valid": configuration_valid,
            "checks": checks,
        })

    return {
        "module": "MQX Endpoint Security Configuration",
        "count": len(results),
        "endpoints": results,
        "generated_at": mqx_now().isoformat(),
        "security_note": (
            "This report checks stored configuration only. "
            "It does not validate DNS resolution, destination "
            "IP addresses, TLS certificates, redirect targets, "
            "or actual network connectivity. "
            "Outbound requests remain disabled."
        ),
    }


# ============================================================
# END OF MQX BLOK 3
# ============================================================

# ============================================================
# MQEIP - MARITIME QUANTUM EXCHANGE
# BLOK 4 - EVENTS, DELIVERY QUEUE & RETRY MANAGEMENT
# ============================================================


# ============================================================
# DELIVERY RETRY MODEL
# ============================================================

class MQXDeliveryRetryRequest(MQXBaseModel):
    reason: str = Field(
        min_length=5,
        max_length=500,
    )


# ============================================================
# EVENT SERIALIZATION
# ============================================================

def mqx_serialize_event(row, include_payload=False):
    item = mqx_serialize_row(row)

    raw_payload = item.pop("payload_json", None)

    if include_payload:
        item["payload"] = mqx_decode_json(raw_payload)

    return item


# ============================================================
# REGISTER INTEGRATION EVENT
# ============================================================

@router.post(
    "/events",
    status_code=status.HTTP_201_CREATED,
    summary="Register MQX Integration Event",
)
def mqx_create_event(
    payload: MQXEventCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    tenant_id = str(identity.tenant_id)
    event_id = uuid4()
    now = mqx_now()

    payload_json = mqx_json(payload.payload)

    if len(payload_json.encode("utf-8")) > 262144:
        raise HTTPException(
            status_code=422,
            detail="Event payload exceeds 256 KB.",
        )

    with get_engine().begin() as conn:

        integration = conn.execute(
            text("""
                SELECT
                    id,
                    integration_name,
                    integration_status
                FROM mqx_integrations
                WHERE id = :integration_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "integration_id": payload.integration_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if integration is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise integration not found.",
            )

        if integration["integration_status"] != "active":
            raise HTTPException(
                status_code=409,
                detail="Integration must be active.",
            )

        conn.execute(
            text("""
                INSERT INTO mqx_events (
                    id,
                    tenant_id,
                    integration_id,
                    event_type,
                    source_module,
                    source_record_id,
                    payload_json,
                    correlation_id,
                    event_status,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :integration_id,
                    :event_type,
                    :source_module,
                    :source_record_id,
                    :payload_json,
                    :correlation_id,
                    'pending',
                    :created_by,
                    :created_at
                )
            """),
            {
                "id": event_id,
                "tenant_id": tenant_id,
                "integration_id": payload.integration_id,
                "event_type": payload.event_type,
                "source_module": payload.source_module,
                "source_record_id": payload.source_record_id,
                "payload_json": payload_json,
                "correlation_id": payload.correlation_id,
                "created_by": str(identity.user_id),
                "created_at": now,
            },
        )

        endpoints = conn.execute(
            text("""
                SELECT id
                FROM mqx_endpoints
                WHERE tenant_id = :tenant_id
                  AND integration_id = :integration_id
                  AND endpoint_status = 'active'
                  AND event_type = :event_type
                ORDER BY id
            """),
            {
                "tenant_id": tenant_id,
                "integration_id": payload.integration_id,
                "event_type": payload.event_type,
            },
        ).mappings().all()

        delivery_ids = []

        for endpoint in endpoints:
            delivery_id = uuid4()

            conn.execute(
                text("""
                    INSERT INTO mqx_deliveries (
                        id,
                        tenant_id,
                        event_id,
                        endpoint_id,
                        delivery_status,
                        attempt_count,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        :id,
                        :tenant_id,
                        :event_id,
                        :endpoint_id,
                        'pending',
                        0,
                        :created_at,
                        :updated_at
                    )
                """),
                {
                    "id": delivery_id,
                    "tenant_id": tenant_id,
                    "event_id": event_id,
                    "endpoint_id": endpoint["id"],
                    "created_at": now,
                    "updated_at": now,
                },
            )

            delivery_ids.append(str(delivery_id))

        mqx_record_audit(
            conn,
            identity,
            "integration_event",
            event_id,
            "created",
            mqx_json({
                "integration_id":
                    str(payload.integration_id),
                "event_type": payload.event_type,
                "delivery_count": len(delivery_ids),
            }),
        )

    return {
        "event_id": str(event_id),
        "integration_id": str(payload.integration_id),
        "event_type": payload.event_type,
        "event_status": "pending",
        "delivery_count": len(delivery_ids),
        "delivery_ids": delivery_ids,
        "created_at": now.isoformat(),
        "note": (
            "Event and delivery records are stored. "
            "No external HTTP request has been sent."
        ),
    }


# ============================================================
# LIST INTEGRATION EVENTS
# ============================================================

@router.get(
    "/events",
    summary="List MQX Integration Events",
)
def mqx_list_events(
    integration_id: Optional[UUID] = None,
    event_status: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    with get_engine().connect() as conn:

        rows = conn.execute(
            text("""
                SELECT
                    id,
                    integration_id,
                    event_type,
                    source_module,
                    source_record_id,
                    correlation_id,
                    event_status,
                    created_by,
                    created_at
                FROM mqx_events
                WHERE tenant_id = :tenant_id
                  AND (
                      :integration_id IS NULL
                      OR integration_id = :integration_id
                  )
                  AND (
                      :event_status IS NULL
                      OR event_status = :event_status
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
            """),
            {
                "tenant_id": str(identity.tenant_id),
                "integration_id": integration_id,
                "event_status": event_status,
                "limit": limit,
            },
        ).mappings().all()

    return {
        "module": "MQX Integration Events",
        "count": len(rows),
        "events": [
            mqx_serialize_event(row)
            for row in rows
        ],
    }


# ============================================================
# GET EVENT DETAILS AND DELIVERY STATUS
# ============================================================

@router.get(
    "/events/{event_id}",
    summary="Get MQX Event Details",
)
def mqx_get_event(
    event_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:

        event = conn.execute(
            text("""
                SELECT *
                FROM mqx_events
                WHERE id = :event_id
                  AND tenant_id = :tenant_id
            """),
            {
                "event_id": event_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if event is None:
            raise HTTPException(
                status_code=404,
                detail="MQX event not found.",
            )

        deliveries = conn.execute(
            text("""
                SELECT
                    d.id,
                    d.event_id,
                    d.endpoint_id,
                    e.endpoint_name,
                    d.delivery_status,
                    d.attempt_count,
                    d.last_http_status,
                    d.last_error,
                    d.next_retry_at,
                    d.delivered_at,
                    d.created_at,
                    d.updated_at
                FROM mqx_deliveries d
                JOIN mqx_endpoints e
                  ON e.id = d.endpoint_id
                 AND e.tenant_id = d.tenant_id
                WHERE d.event_id = :event_id
                  AND d.tenant_id = :tenant_id
                ORDER BY d.created_at, d.id
            """),
            {
                "event_id": event_id,
                "tenant_id": tenant_id,
            },
        ).mappings().all()

    return {
        "event": mqx_serialize_event(
            event,
            include_payload=False,
        ),
        "delivery_count": len(deliveries),
        "deliveries": [
            mqx_serialize_row(row)
            for row in deliveries
        ],
        "note": (
            "Event payload is excluded from this response "
            "to reduce unintended data disclosure."
        ),
    }


# ============================================================
# LIST DELIVERY QUEUE
# ============================================================

@router.get(
    "/deliveries",
    summary="List MQX Delivery Queue",
)
def mqx_list_deliveries(
    delivery_status: Optional[
        Literal[
            "pending",
            "processing",
            "delivered",
            "failed",
            "cancelled",
        ]
    ] = None,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    with get_engine().connect() as conn:

        rows = conn.execute(
            text("""
                SELECT
                    d.id,
                    d.event_id,
                    d.endpoint_id,
                    e.endpoint_name,
                    d.delivery_status,
                    d.attempt_count,
                    d.last_http_status,
                    d.last_error,
                    d.next_retry_at,
                    d.delivered_at,
                    d.created_at,
                    d.updated_at
                FROM mqx_deliveries d
                JOIN mqx_endpoints e
                  ON e.id = d.endpoint_id
                 AND e.tenant_id = d.tenant_id
                WHERE d.tenant_id = :tenant_id
                  AND (
                      :delivery_status IS NULL
                      OR d.delivery_status = :delivery_status
                  )
                ORDER BY d.created_at DESC, d.id DESC
                LIMIT :limit
            """),
            {
                "tenant_id": str(identity.tenant_id),
                "delivery_status": delivery_status,
                "limit": limit,
            },
        ).mappings().all()

    return {
        "module": "MQX Delivery Queue",
        "count": len(rows),
        "deliveries": [
            mqx_serialize_row(row)
            for row in rows
        ],
    }


# ============================================================
# MANUAL DELIVERY RETRY REQUEST
# ============================================================

@router.post(
    "/deliveries/{delivery_id}/retry",
    summary="Request MQX Delivery Retry",
)
def mqx_retry_delivery(
    delivery_id: UUID,
    payload: MQXDeliveryRetryRequest,
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    tenant_id = str(identity.tenant_id)
    now = mqx_now()

    with get_engine().begin() as conn:

        delivery = conn.execute(
            text("""
                SELECT
                    d.id,
                    d.event_id,
                    d.endpoint_id,
                    d.delivery_status,
                    e.endpoint_status,
                    i.integration_status
                FROM mqx_deliveries d
                JOIN mqx_endpoints e
                  ON e.id = d.endpoint_id
                 AND e.tenant_id = d.tenant_id
                JOIN mqx_integrations i
                  ON i.id = e.integration_id
                 AND i.tenant_id = e.tenant_id
                WHERE d.id = :delivery_id
                  AND d.tenant_id = :tenant_id
                FOR UPDATE OF d
            """),
            {
                "delivery_id": delivery_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if delivery is None:
            raise HTTPException(
                status_code=404,
                detail="MQX delivery not found.",
            )

        if delivery["delivery_status"] not in {
            "failed",
            "cancelled",
        }:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Only failed or cancelled deliveries "
                    "can be rescheduled."
                ),
            )

        if (
            delivery["endpoint_status"] != "active"
            or delivery["integration_status"] != "active"
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Integration and endpoint must both "
                    "be active before retry."
                ),
            )

        conn.execute(
            text("""
                UPDATE mqx_deliveries
                SET delivery_status = 'pending',
                    attempt_count = 0,
                    last_http_status = NULL,
                    last_error = NULL,
                    next_retry_at = NULL,
                    delivered_at = NULL,
                    updated_at = :updated_at
                WHERE id = :delivery_id
                  AND tenant_id = :tenant_id
            """),
            {
                "updated_at": now,
                "delivery_id": delivery_id,
                "tenant_id": tenant_id,
            },
        )

        mqx_record_audit(
            conn,
            identity,
            "delivery",
            delivery_id,
            "retry_requested",
            mqx_json({
                "previous_status":
                    delivery["delivery_status"],
                "reason": payload.reason,
            }),
        )

    return {
        "delivery_id": str(delivery_id),
        "delivery_status": "pending",
        "retry_requested_at": now.isoformat(),
        "note": (
            "Delivery has been returned to the queue. "
            "No HTTP transmission is performed here."
        ),
    }


# ============================================================
# END OF MQX BLOK 4
# ============================================================

# ============================================================
# MQEIP - MARITIME QUANTUM EXCHANGE
# BLOK 5 - ENTERPRISE DASHBOARD, AUDIT & DATABASE INIT
# ============================================================


# ============================================================
# ENTERPRISE INTEGRATION DASHBOARD
# ============================================================

@router.get(
    "/executive/dashboard",
    summary="MQX Enterprise Integration Dashboard",
)
def mqx_executive_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    tenant_id = str(identity.tenant_id)
    now = mqx_now()

    with get_engine().connect() as conn:

        integrations = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_integrations,
                    COUNT(*) FILTER (
                        WHERE integration_status = 'active'
                    ) AS active_integrations,
                    COUNT(*) FILTER (
                        WHERE integration_status = 'draft'
                    ) AS draft_integrations,
                    COUNT(*) FILTER (
                        WHERE integration_status = 'paused'
                    ) AS paused_integrations,
                    COUNT(*) FILTER (
                        WHERE integration_status = 'disabled'
                    ) AS disabled_integrations
                FROM mqx_integrations
                WHERE tenant_id = :tenant_id
            """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().one()

        endpoints = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_endpoints,
                    COUNT(*) FILTER (
                        WHERE endpoint_status = 'active'
                    ) AS active_endpoints,
                    COUNT(*) FILTER (
                        WHERE endpoint_status = 'disabled'
                    ) AS disabled_endpoints
                FROM mqx_endpoints
                WHERE tenant_id = :tenant_id
            """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().one()

        events = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_events,
                    COUNT(*) FILTER (
                        WHERE event_status = 'pending'
                    ) AS pending_events
                FROM mqx_events
                WHERE tenant_id = :tenant_id
            """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().one()

        deliveries = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_deliveries,
                    COUNT(*) FILTER (
                        WHERE delivery_status = 'pending'
                    ) AS pending_deliveries,
                    COUNT(*) FILTER (
                        WHERE delivery_status = 'processing'
                    ) AS processing_deliveries,
                    COUNT(*) FILTER (
                        WHERE delivery_status = 'delivered'
                    ) AS delivered_deliveries,
                    COUNT(*) FILTER (
                        WHERE delivery_status = 'failed'
                    ) AS failed_deliveries,
                    COUNT(*) FILTER (
                        WHERE delivery_status = 'cancelled'
                    ) AS cancelled_deliveries
                FROM mqx_deliveries
                WHERE tenant_id = :tenant_id
            """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().one()

    return {
        "platform": "MQEIP",
        "module": "MQX Enterprise Integration Gateway",
        "integrations": mqx_serialize_row(integrations),
        "endpoints": mqx_serialize_row(endpoints),
        "events": mqx_serialize_row(events),
        "deliveries": mqx_serialize_row(deliveries),
        "generated_at": now.isoformat(),
        "delivery_engine": {
            "outbound_http_enabled": False,
            "worker_connected": False,
            "automatic_retry_enabled": False,
        },
        "note": (
            "Dashboard reports stored integration records. "
            "Registered endpoints and pending deliveries "
            "do not indicate successful external transmission."
        ),
    }


# ============================================================
# DELIVERY QUEUE INTELLIGENCE
# ============================================================

@router.get(
    "/intelligence/delivery-queue",
    summary="MQX Delivery Queue Intelligence",
)
def mqx_delivery_queue_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    tenant_id = str(identity.tenant_id)
    now = mqx_now()

    with get_engine().connect() as conn:

        groups = conn.execute(
            text("""
                SELECT
                    d.delivery_status,
                    i.integration_name,
                    COUNT(*) AS delivery_count,
                    MIN(d.created_at) AS oldest_created_at,
                    MAX(d.updated_at) AS latest_updated_at
                FROM mqx_deliveries d
                JOIN mqx_endpoints e
                  ON e.id = d.endpoint_id
                 AND e.tenant_id = d.tenant_id
                JOIN mqx_integrations i
                  ON i.id = e.integration_id
                 AND i.tenant_id = e.tenant_id
                WHERE d.tenant_id = :tenant_id
                GROUP BY
                    d.delivery_status,
                    i.integration_name
                ORDER BY
                    d.delivery_status,
                    i.integration_name
            """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().all()

        pending_age = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS pending_count,
                    COUNT(*) FILTER (
                        WHERE created_at < :cutoff
                    ) AS pending_over_24_hours
                FROM mqx_deliveries
                WHERE tenant_id = :tenant_id
                  AND delivery_status = 'pending'
            """),
            {
                "tenant_id": tenant_id,
                "cutoff": now - timedelta(hours=24),
            },
        ).mappings().one()

    return {
        "module": "MQX Delivery Queue Intelligence",
        "groups": [
            mqx_serialize_row(row)
            for row in groups
        ],
        "pending_age": mqx_serialize_row(pending_age),
        "generated_at": now.isoformat(),
        "recommendation": (
            "Review pending and failed deliveries. "
            "No automatic outbound worker is configured."
        ),
    }


# ============================================================
# INTEGRATION EVENT INTELLIGENCE
# ============================================================

@router.get(
    "/intelligence/events",
    summary="MQX Integration Event Intelligence",
)
def mqx_event_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:

        rows = conn.execute(
            text("""
                SELECT
                    i.integration_name,
                    e.source_module,
                    e.event_type,
                    e.event_status,
                    COUNT(*) AS event_count
                FROM mqx_events e
                JOIN mqx_integrations i
                  ON i.id = e.integration_id
                 AND i.tenant_id = e.tenant_id
                WHERE e.tenant_id = :tenant_id
                GROUP BY
                    i.integration_name,
                    e.source_module,
                    e.event_type,
                    e.event_status
                ORDER BY
                    i.integration_name,
                    e.source_module,
                    e.event_type,
                    e.event_status
            """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().all()

    groups = [
        mqx_serialize_row(row)
        for row in rows
    ]

    return {
        "module": "MQX Integration Event Intelligence",
        "total_events": sum(
            row["event_count"]
            for row in groups
        ),
        "groups": groups,
        "generated_at": mqx_now().isoformat(),
    }


# ============================================================
# MQX AUDIT TRAIL
# ============================================================

@router.get(
    "/audit",
    summary="MQX Enterprise Audit Trail",
)
def mqx_list_audit(
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    with get_engine().connect() as conn:

        rows = conn.execute(
            text("""
                SELECT
                    id,
                    entity_type,
                    entity_id,
                    action,
                    details,
                    actor_id,
                    created_at
                FROM mqx_audit_log
                WHERE tenant_id = :tenant_id
                  AND (
                      :entity_type IS NULL
                      OR entity_type = :entity_type
                  )
                  AND (
                      :entity_id IS NULL
                      OR entity_id = :entity_id
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
            """),
            {
                "tenant_id": str(identity.tenant_id),
                "entity_type": entity_type,
                "entity_id": entity_id,
                "limit": limit,
            },
        ).mappings().all()

    return {
        "module": "MQX Enterprise Audit Trail",
        "count": len(rows),
        "records": [
            mqx_serialize_row(row)
            for row in rows
        ],
    }


# ============================================================
# MQX SYSTEM STATUS
# ============================================================

@router.get(
    "/system/status",
    summary="MQX Enterprise Gateway System Status",
)
def mqx_system_status(
    identity: TokenIdentity = Depends(verify_access_token),
):
    mqx_require_admin(identity)

    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:

        counts = conn.execute(
            text("""
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM mqx_integrations
                        WHERE tenant_id = :tenant_id
                    ) AS integration_count,
                    (
                        SELECT COUNT(*)
                        FROM mqx_endpoints
                        WHERE tenant_id = :tenant_id
                    ) AS endpoint_count,
                    (
                        SELECT COUNT(*)
                        FROM mqx_events
                        WHERE tenant_id = :tenant_id
                    ) AS event_count,
                    (
                        SELECT COUNT(*)
                        FROM mqx_deliveries
                        WHERE tenant_id = :tenant_id
                    ) AS delivery_count
            """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().one()

    return {
        "platform": "MQEIP",
        "module": "Maritime Quantum Exchange",
        "database_status": "connected",
        "registry": mqx_serialize_row(counts),
        "capabilities": {
            "integration_registry": True,
            "endpoint_registry": True,
            "event_registration": True,
            "delivery_queue": True,
            "manual_retry_queueing": True,
            "integration_dashboard": True,
            "audit_trail": True,
            "outbound_http_delivery": False,
            "automatic_delivery_worker": False,
            "external_connection_verified": False,
        },
        "generated_at": mqx_now().isoformat(),
    }


# ============================================================
# INITIALIZE MQX POSTGRESQL DATABASE TABLES
# ============================================================

init_mqx_tables()


# ============================================================
# END OF MQX BLOK 5
# ============================================================

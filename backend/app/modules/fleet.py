from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from ..core.security import TokenIdentity, verify_access_token
from ..core.database import get_database_url


router = APIRouter(
    prefix="/api/v1/fleet",
    tags=["Fleet"],
)


class VesselCreate(BaseModel):
    vessel_name: str = Field(..., min_length=2, max_length=120)
    imo_number: Optional[str] = Field(default=None, max_length=20)
    vessel_type: str = Field(..., min_length=2, max_length=80)
    flag: Optional[str] = Field(default=None, max_length=80)
    status: str = Field(default="active", max_length=30)


class VesselResponse(BaseModel):
    vessel_id: str
    vessel_name: str
    imo_number: Optional[str]
    vessel_type: str
    flag: Optional[str]
    status: str
    tenant_id: str
    created_at: str


def _engine():
    return create_engine(
        get_database_url(),
        pool_pre_ping=True,
    )


def _ensure_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS vessels (
        vessel_id VARCHAR(120) PRIMARY KEY,
        vessel_name VARCHAR(120) NOT NULL,
        imo_number VARCHAR(20),
        vessel_type VARCHAR(80) NOT NULL,
        flag VARCHAR(80),
        status VARCHAR(30) NOT NULL DEFAULT 'active',
        tenant_id VARCHAR(120) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS ix_vessels_tenant_id
    ON vessels (tenant_id);
    """

    with _engine().begin() as conn:
        conn.execute(text(sql))


@router.get("")
def list_vessels(
    identity: TokenIdentity = Depends(verify_access_token),
):
    _ensure_table()

    sql = text(
        """
        SELECT
            vessel_id,
            vessel_name,
            imo_number,
            vessel_type,
            flag,
            status,
            tenant_id,
            created_at
        FROM vessels
        WHERE tenant_id = :tenant_id
        ORDER BY created_at DESC
        """
    )

    with _engine().connect() as conn:
        rows = conn.execute(
            sql,
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    vessels = []

    for row in rows:
        item = dict(row)

        if item["created_at"] is not None:
            item["created_at"] = item["created_at"].isoformat()

        vessels.append(item)

    return {
        "platform": "MQEIP",
        "module": "Fleet Management",
        "tenant_id": identity.tenant_id,
        "total_vessels": len(vessels),
        "vessels": vessels,
    }


@router.post(
    "",
    response_model=VesselResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_vessel(
    vessel: VesselCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    _ensure_table()

    vessel_name = vessel.vessel_name.strip()

    duplicate_sql = text(
        """
        SELECT vessel_id
        FROM vessels
        WHERE tenant_id = :tenant_id
          AND LOWER(vessel_name) = LOWER(:vessel_name)
        LIMIT 1
        """
    )

    with _engine().connect() as conn:
        existing = conn.execute(
            duplicate_sql,
            {
                "tenant_id": identity.tenant_id,
                "vessel_name": vessel_name,
            },
        ).first()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Vessel already exists for this tenant.",
        )

    vessel_id = (
        f"{identity.tenant_id}-vessel-{uuid4().hex[:12]}"
    )

    created_at = datetime.now(timezone.utc)

    insert_sql = text(
        """
        INSERT INTO vessels (
            vessel_id,
            vessel_name,
            imo_number,
            vessel_type,
            flag,
            status,
            tenant_id,
            created_at
        )
        VALUES (
            :vessel_id,
            :vessel_name,
            :imo_number,
            :vessel_type,
            :flag,
            :status,
            :tenant_id,
            :created_at
        )
        """
    )

    record = {
        "vessel_id": vessel_id,
        "vessel_name": vessel_name,
        "imo_number": vessel.imo_number,
        "vessel_type": vessel.vessel_type.strip(),
        "flag": vessel.flag,
        "status": vessel.status.strip().lower(),
        "tenant_id": identity.tenant_id,
        "created_at": created_at,
    }

    with _engine().begin() as conn:
        conn.execute(insert_sql, record)

    response = dict(record)
    response["created_at"] = created_at.isoformat()

    return response


@router.get(
    "/{vessel_id}",
    response_model=VesselResponse,
)
def get_vessel(
    vessel_id: str,
    identity: TokenIdentity = Depends(verify_access_token),
):
    _ensure_table()

    sql = text(
        """
        SELECT
            vessel_id,
            vessel_name,
            imo_number,
            vessel_type,
            flag,
            status,
            tenant_id,
            created_at
        FROM vessels
        WHERE vessel_id = :vessel_id
          AND tenant_id = :tenant_id
        LIMIT 1
        """
    )

    with _engine().connect() as conn:
        row = conn.execute(
            sql,
            {
                "vessel_id": vessel_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vessel not found.",
        )

    result = dict(row)

    if result["created_at"] is not None:
        result["created_at"] = result["created_at"].isoformat()

    return result

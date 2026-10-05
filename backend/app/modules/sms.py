from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from ..core.database import get_database_url
from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/sms",
    tags=["SMS / ISM"],
)


def _engine():
    return create_engine(get_database_url(), pool_pre_ping=True)


def _init_table() -> None:
    ddl = """
    CREATE TABLE IF NOT EXISTS sms_records (
        id VARCHAR(64) PRIMARY KEY,
        tenant_id VARCHAR(120) NOT NULL,
        vessel_id VARCHAR(120),
        category VARCHAR(80) NOT NULL,
        title VARCHAR(200) NOT NULL,
        description TEXT,
        status VARCHAR(40) NOT NULL,
        responsible_person VARCHAR(150),
        due_date VARCHAR(40),
        created_at VARCHAR(50) NOT NULL,
        updated_at VARCHAR(50) NOT NULL
    )
    """
    with _engine().begin() as conn:
        conn.execute(text(ddl))


class SMSCreate(BaseModel):
    vessel_id: Optional[str] = Field(default=None, max_length=120)
    category: str = Field(..., min_length=2, max_length=80)
    title: str = Field(..., min_length=2, max_length=200)
    description: Optional[str] = None
    status: str = Field(default="open", min_length=2, max_length=40)
    responsible_person: Optional[str] = Field(default=None, max_length=150)
    due_date: Optional[str] = Field(default=None, max_length=40)


class SMSResponse(SMSCreate):
    id: str
    created_at: str
    updated_at: str


@router.get("", response_model=list[SMSResponse])
def list_sms_records(
    identity: TokenIdentity = Depends(verify_access_token),
):
    _init_table()

    sql = """
    SELECT id, vessel_id, category, title, description, status,
           responsible_person, due_date, created_at, updated_at
    FROM sms_records
    WHERE tenant_id = :tenant_id
    ORDER BY created_at DESC
    """

    with _engine().begin() as conn:
        rows = conn.execute(
            text(sql),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [dict(row) for row in rows]


@router.post(
    "",
    response_model=SMSResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_sms_record(
    payload: SMSCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    _init_table()

    record_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    sql = """
    INSERT INTO sms_records (
        id, tenant_id, vessel_id, category, title, description,
        status, responsible_person, due_date, created_at, updated_at
    )
    VALUES (
        :id, :tenant_id, :vessel_id, :category, :title, :description,
        :status, :responsible_person, :due_date, :created_at, :updated_at
    )
    """

    values = {
        "id": record_id,
        "tenant_id": identity.tenant_id,
        "vessel_id": payload.vessel_id,
        "category": payload.category,
        "title": payload.title,
        "description": payload.description,
        "status": payload.status,
        "responsible_person": payload.responsible_person,
        "due_date": payload.due_date,
        "created_at": now,
        "updated_at": now,
    }

    with _engine().begin() as conn:
        conn.execute(text(sql), values)

    return SMSResponse(
        id=record_id,
        created_at=now,
        updated_at=now,
        **payload.model_dump(),
    )


@router.delete("/{record_id}")
def delete_sms_record(
    record_id: str,
    identity: TokenIdentity = Depends(verify_access_token),
):
    _init_table()

    sql = """
    DELETE FROM sms_records
    WHERE id = :record_id
      AND tenant_id = :tenant_id
    """

    with _engine().begin() as conn:
        result = conn.execute(
            text(sql),
            {
                "record_id": record_id,
                "tenant_id": identity.tenant_id,
            },
        )

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SMS/ISM record not found.",
        )

    return {
        "status": "deleted",
        "record_id": record_id,
    }

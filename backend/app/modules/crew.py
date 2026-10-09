from datetime import date, datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from ..core.database import get_database_url
from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/crew",
    tags=["Crew & Certificate Management"],
)


def get_engine():
    return create_engine(get_database_url(), pool_pre_ping=True)


def init_crew_tables() -> None:
    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS crew_members (
                    id VARCHAR(36) PRIMARY KEY,
                    tenant_id VARCHAR(120) NOT NULL,
                    crew_name VARCHAR(160) NOT NULL,
                    employee_id VARCHAR(80),
                    rank VARCHAR(120) NOT NULL,
                    vessel_id VARCHAR(36),
                    vessel_name VARCHAR(160),
                    sign_on_date DATE,
                    sign_off_date DATE,
                    status VARCHAR(40) NOT NULL DEFAULT 'active',
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS crew_certificates (
                    id VARCHAR(36) PRIMARY KEY,
                    tenant_id VARCHAR(120) NOT NULL,
                    crew_id VARCHAR(36) NOT NULL,
                    certificate_type VARCHAR(160) NOT NULL,
                    certificate_number VARCHAR(120),
                    issuing_authority VARCHAR(160),
                    issue_date DATE,
                    expiry_date DATE,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )


class CrewCreate(BaseModel):
    crew_name: str = Field(..., min_length=2, max_length=160)
    employee_id: Optional[str] = Field(default=None, max_length=80)
    rank: str = Field(..., min_length=2, max_length=120)
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = Field(default=None, max_length=160)
    sign_on_date: Optional[date] = None
    sign_off_date: Optional[date] = None
    status: str = Field(default="active", max_length=40)


class CertificateCreate(BaseModel):
    certificate_type: str = Field(..., min_length=2, max_length=160)
    certificate_number: Optional[str] = Field(default=None, max_length=120)
    issuing_authority: Optional[str] = Field(default=None, max_length=160)
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None


def certificate_status(expiry_date: Optional[date]) -> str:
    if expiry_date is None:
        return "NO EXPIRY DATE"

    today = date.today()
    remaining_days = (expiry_date - today).days

    if remaining_days < 0:
        return "EXPIRED"
    if remaining_days <= 30:
        return "EXPIRING IN 30 DAYS"
    if remaining_days <= 60:
        return "EXPIRING IN 60 DAYS"
    if remaining_days <= 90:
        return "EXPIRING IN 90 DAYS"

    return "VALID"


@router.get("")
def list_crew(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_crew_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM crew_members
                WHERE tenant_id = :tenant_id
                ORDER BY crew_name
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [dict(row) for row in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_crew(
    payload: CrewCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_crew_tables()

    crew_id = str(uuid4())
    created_at = datetime.now(timezone.utc)
    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO crew_members (
                    id,
                    tenant_id,
                    crew_name,
                    employee_id,
                    rank,
                    vessel_id,
                    vessel_name,
                    sign_on_date,
                    sign_off_date,
                    status,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :crew_name,
                    :employee_id,
                    :rank,
                    :vessel_id,
                    :vessel_name,
                    :sign_on_date,
                    :sign_off_date,
                    :status,
                    :created_at
                )
                """
            ),
            {
                "id": crew_id,
                "tenant_id": identity.tenant_id,
                "crew_name": payload.crew_name,
                "employee_id": payload.employee_id,
                "rank": payload.rank,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "sign_on_date": payload.sign_on_date,
                "sign_off_date": payload.sign_off_date,
                "status": payload.status,
                "created_at": created_at,
            },
        )

    return {
        "id": crew_id,
        "tenant_id": identity.tenant_id,
        **payload.model_dump(),
        "created_at": created_at,
    }

@router.get("/intelligence/certificates")
def certificate_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_crew_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    c.crew_name,
                    c.employee_id,
                    c.rank,
                    c.vessel_id,
                    c.vessel_name,
                    cc.id AS certificate_id,
                    cc.certificate_type,
                    cc.certificate_number,
                    cc.issuing_authority,
                    cc.issue_date,
                    cc.expiry_date
                FROM crew_certificates cc
                JOIN crew_members c
                  ON c.id = cc.crew_id
                 AND c.tenant_id = cc.tenant_id
                WHERE cc.tenant_id = :tenant_id
                ORDER BY c.vessel_name, c.rank, c.crew_name, cc.expiry_date
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    result = []

    for row in rows:
        item = dict(row)
        item["certificate_status"] = certificate_status(item["expiry_date"])
        result.append(item)

    return {
        "tenant_id": identity.tenant_id,
        "generated_at": datetime.now(timezone.utc),
        "total_certificates": len(result),
        "certificates": result,
    }


@router.get("/{crew_id:uuid}")
def get_crew(
    crew_id: str,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_crew_tables()
    engine = get_engine()

    with engine.begin() as conn:
        crew = conn.execute(
            text(
                """
                SELECT *
                FROM crew_members
                WHERE id = :crew_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "crew_id": crew_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if crew is None:
        raise HTTPException(status_code=404, detail="Crew member not found.")

    return dict(crew)


@router.post(
    "/{crew_id}/certificates",
    status_code=status.HTTP_201_CREATED,
)
def add_certificate(
    crew_id: str,
    payload: CertificateCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_crew_tables()
    engine = get_engine()

    with engine.begin() as conn:
        crew = conn.execute(
            text(
                """
                SELECT id
                FROM crew_members
                WHERE id = :crew_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "crew_id": crew_id,
                "tenant_id": identity.tenant_id,
            },
        ).first()

        if crew is None:
            raise HTTPException(
                status_code=404,
                detail="Crew member not found.",
            )

        certificate_id = str(uuid4())
        created_at = datetime.now(timezone.utc)

        conn.execute(
            text(
                """
                INSERT INTO crew_certificates (
                    id,
                    tenant_id,
                    crew_id,
                    certificate_type,
                    certificate_number,
                    issuing_authority,
                    issue_date,
                    expiry_date,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :crew_id,
                    :certificate_type,
                    :certificate_number,
                    :issuing_authority,
                    :issue_date,
                    :expiry_date,
                    :created_at
                )
                """
            ),
            {
                "id": certificate_id,
                "tenant_id": identity.tenant_id,
                "crew_id": crew_id,
                "certificate_type": payload.certificate_type,
                "certificate_number": payload.certificate_number,
                "issuing_authority": payload.issuing_authority,
                "issue_date": payload.issue_date,
                "expiry_date": payload.expiry_date,
                "created_at": created_at,
            },
        )

    return {
        "id": certificate_id,
        "crew_id": crew_id,
        **payload.model_dump(),
        "certificate_status": certificate_status(payload.expiry_date),
        "created_at": created_at,
    }


@router.get("/{crew_id}/certificates")
def list_crew_certificates(
    crew_id: str,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_crew_tables()
    engine = get_engine()

    with engine.begin() as conn:
        crew = conn.execute(
            text(
                """
                SELECT id
                FROM crew_members
                WHERE id = :crew_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "crew_id": crew_id,
                "tenant_id": identity.tenant_id,
            },
        ).first()

        if crew is None:
            raise HTTPException(status_code=404, detail="Crew member not found.")

        rows = conn.execute(
            text(
                """
                SELECT *
                FROM crew_certificates
                WHERE crew_id = :crew_id
                  AND tenant_id = :tenant_id
                ORDER BY expiry_date NULLS LAST, certificate_type
                """
            ),
            {
                "crew_id": crew_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().all()

    certificates = []

    for row in rows:
        item = dict(row)
        item["certificate_status"] = certificate_status(item["expiry_date"])
        certificates.append(item)

    return certificates




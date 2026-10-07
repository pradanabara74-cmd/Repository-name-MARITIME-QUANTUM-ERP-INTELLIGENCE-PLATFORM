from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..core.database import get_engine
from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/technical",
    tags=["Technical & Engineering Intelligence"],
)


# ============================================================
# REQUEST MODELS
# ============================================================

class DefectCreate(BaseModel):
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    equipment: str = Field(min_length=1, max_length=255)
    defect_title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1)
    severity: str = Field(default="medium", max_length=50)
    reported_date: date
    responsible_person: Optional[str] = Field(
        default=None,
        max_length=255,
    )
    target_date: Optional[date] = None
    operational_impact: Optional[str] = None


class DefectStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=50)
    action_taken: Optional[str] = None
    closure_evidence: Optional[str] = None


class DryDockCreate(BaseModel):
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    project_name: str = Field(min_length=1, max_length=255)
    shipyard: Optional[str] = Field(default=None, max_length=255)
    planned_start: date
    planned_end: date
    budget: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(default="USD", max_length=10)
    scope_of_work: Optional[str] = None
    project_manager: Optional[str] = Field(
        default=None,
        max_length=255,
    )


class CertificateCreate(BaseModel):
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    certificate_name: str = Field(min_length=1, max_length=255)
    certificate_number: Optional[str] = Field(
        default=None,
        max_length=255,
    )
    issuing_authority: Optional[str] = Field(
        default=None,
        max_length=255,
    )
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    document_reference: Optional[str] = None


class BunkerRecordCreate(BaseModel):
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    record_date: date
    fuel_type: str = Field(min_length=1, max_length=100)
    opening_quantity: Decimal = Field(ge=0)
    received_quantity: Decimal = Field(default=Decimal("0"), ge=0)
    consumed_quantity: Decimal = Field(default=Decimal("0"), ge=0)
    closing_quantity: Decimal = Field(ge=0)
    unit: str = Field(default="MT", max_length=20)
    main_engine_hours: Optional[Decimal] = Field(default=None, ge=0)
    main_engine_rpm: Optional[Decimal] = Field(default=None, ge=0)
    distance_nm: Optional[Decimal] = Field(default=None, ge=0)
    remarks: Optional[str] = None


# ============================================================
# SERIALIZATION
# ============================================================

def serialize_row(row):
    if row is None:
        return None

    data = dict(row)

    for key, value in data.items():
        if isinstance(value, UUID):
            data[key] = str(value)
        elif isinstance(value, Decimal):
            data[key] = float(value)
        elif isinstance(value, (datetime, date)):
            data[key] = value.isoformat()

    return data


# ============================================================
# DATABASE FOUNDATION
# ============================================================

def init_technical_tables():
    engine = get_engine()

    with engine.begin() as conn:

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS technical_defects (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    equipment VARCHAR(255) NOT NULL,
                    defect_title VARCHAR(255) NOT NULL,
                    description TEXT NOT NULL,
                    severity VARCHAR(50) NOT NULL,
                    reported_date DATE NOT NULL,
                    responsible_person VARCHAR(255),
                    target_date DATE,
                    operational_impact TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'open',
                    action_taken TEXT,
                    closure_evidence TEXT,
                    closed_at TIMESTAMPTZ,
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS technical_drydock_projects (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    project_name VARCHAR(255) NOT NULL,
                    shipyard VARCHAR(255),
                    planned_start DATE NOT NULL,
                    planned_end DATE NOT NULL,
                    budget NUMERIC(18,2) NOT NULL DEFAULT 0,
                    actual_cost NUMERIC(18,2) NOT NULL DEFAULT 0,
                    currency VARCHAR(10) NOT NULL,
                    scope_of_work TEXT,
                    project_manager VARCHAR(255),
                    progress_percent NUMERIC(5,2) NOT NULL DEFAULT 0,
                    status VARCHAR(50) NOT NULL DEFAULT 'planned',
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS technical_certificates (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    certificate_name VARCHAR(255) NOT NULL,
                    certificate_number VARCHAR(255),
                    issuing_authority VARCHAR(255),
                    issue_date DATE,
                    expiry_date DATE,
                    document_reference TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'valid',
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS technical_bunker_records (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    record_date DATE NOT NULL,
                    fuel_type VARCHAR(100) NOT NULL,
                    opening_quantity NUMERIC(18,3) NOT NULL,
                    received_quantity NUMERIC(18,3) NOT NULL DEFAULT 0,
                    consumed_quantity NUMERIC(18,3) NOT NULL DEFAULT 0,
                    closing_quantity NUMERIC(18,3) NOT NULL,
                    unit VARCHAR(20) NOT NULL,
                    main_engine_hours NUMERIC(18,2),
                    main_engine_rpm NUMERIC(18,2),
                    distance_nm NUMERIC(18,2),
                    remarks TEXT,
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_technical_defects
                ON technical_defects
                (tenant_id, vessel_id, status, severity)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_technical_drydock
                ON technical_drydock_projects
                (tenant_id, vessel_id, status)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_technical_cert_expiry
                ON technical_certificates
                (tenant_id, vessel_id, expiry_date)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_technical_bunker
                ON technical_bunker_records
                (tenant_id, vessel_id, record_date)
                """
            )
        )

  # ============================================================
# DEFECT MANAGEMENT INTELLIGENCE
# ============================================================

@router.get(
    "/defects",
    summary="List Vessel Technical Defects",
)
def list_defects(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM technical_defects
                WHERE tenant_id = :tenant_id
                ORDER BY
                    CASE severity
                        WHEN 'critical' THEN 1
                        WHEN 'high' THEN 2
                        WHEN 'medium' THEN 3
                        ELSE 4
                    END,
                    reported_date DESC,
                    created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/defects",
    status_code=status.HTTP_201_CREATED,
    summary="Report Vessel Technical Defect",
)
def create_defect(
    payload: DefectCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()

    severity = payload.severity.strip().lower()

    if severity not in {
        "low",
        "medium",
        "high",
        "critical",
    }:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Severity must be low, medium, high, or critical.",
        )

    engine = get_engine()
    defect_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO technical_defects (
                    id,
                    tenant_id,
                    vessel_id,
                    vessel_name,
                    equipment,
                    defect_title,
                    description,
                    severity,
                    reported_date,
                    responsible_person,
                    target_date,
                    operational_impact,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :vessel_id,
                    :vessel_name,
                    :equipment,
                    :defect_title,
                    :description,
                    :severity,
                    :reported_date,
                    :responsible_person,
                    :target_date,
                    :operational_impact,
                    'open',
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": defect_id,
                "tenant_id": identity.tenant_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "equipment": payload.equipment,
                "defect_title": payload.defect_title,
                "description": payload.description,
                "severity": severity,
                "reported_date": payload.reported_date,
                "responsible_person":
                    payload.responsible_person,
                "target_date": payload.target_date,
                "operational_impact":
                    payload.operational_impact,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(defect_id),
        "defect_title": payload.defect_title,
        "severity": severity,
        "status": "open",
    }


@router.get(
    "/defects/{defect_id}",
    summary="Get Technical Defect",
)
def get_defect(
    defect_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT *
                FROM technical_defects
                WHERE id = :defect_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "defect_id": defect_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Technical defect not found.",
        )

    return serialize_row(row)


@router.post(
    "/defects/{defect_id}/status",
    summary="Update Technical Defect Status",
)
def update_defect_status(
    defect_id: UUID,
    payload: DefectStatusUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()

    new_status = payload.status.strip().lower()

    if new_status not in {
        "open",
        "in_progress",
        "deferred",
        "closed",
    }:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Status must be open, in_progress, "
                "deferred, or closed."
            ),
        )

    if new_status == "closed" and not payload.closure_evidence:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Closure evidence is required.",
        )

    engine = get_engine()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT id
                FROM technical_defects
                WHERE id = :defect_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {
                "defect_id": defect_id,
                "tenant_id": identity.tenant_id,
            },
        ).first()

        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Technical defect not found.",
            )

        conn.execute(
            text(
                """
                UPDATE technical_defects
                SET status = :status,
                    action_taken = :action_taken,
                    closure_evidence = :closure_evidence,
                    closed_at = :closed_at,
                    updated_at = :updated_at
                WHERE id = :defect_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "status": new_status,
                "action_taken": payload.action_taken,
                "closure_evidence": payload.closure_evidence,
                "closed_at": (
                    now if new_status == "closed" else None
                ),
                "updated_at": now,
                "defect_id": defect_id,
                "tenant_id": identity.tenant_id,
            },
        )

    return {
        "id": str(defect_id),
        "status": new_status,
        "updated_at": now.isoformat(),
    }

# ============================================================
# DRY DOCK & REPAIR INTELLIGENCE
# ============================================================

@router.get(
    "/drydock-projects",
    summary="List Dry Dock and Repair Projects",
)
def list_drydock_projects(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM technical_drydock_projects
                WHERE tenant_id = :tenant_id
                ORDER BY planned_start DESC, created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/drydock-projects",
    status_code=status.HTTP_201_CREATED,
    summary="Create Dry Dock or Repair Project",
)
def create_drydock_project(
    payload: DryDockCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()

    if payload.planned_end < payload.planned_start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Planned end date cannot be before planned start date.",
        )

    engine = get_engine()
    project_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO technical_drydock_projects (
                    id,
                    tenant_id,
                    vessel_id,
                    vessel_name,
                    project_name,
                    shipyard,
                    planned_start,
                    planned_end,
                    budget,
                    actual_cost,
                    currency,
                    scope_of_work,
                    project_manager,
                    progress_percent,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :vessel_id,
                    :vessel_name,
                    :project_name,
                    :shipyard,
                    :planned_start,
                    :planned_end,
                    :budget,
                    0,
                    :currency,
                    :scope_of_work,
                    :project_manager,
                    0,
                    'planned',
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": project_id,
                "tenant_id": identity.tenant_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "project_name": payload.project_name,
                "shipyard": payload.shipyard,
                "planned_start": payload.planned_start,
                "planned_end": payload.planned_end,
                "budget": payload.budget,
                "currency": payload.currency.upper(),
                "scope_of_work": payload.scope_of_work,
                "project_manager": payload.project_manager,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(project_id),
        "project_name": payload.project_name,
        "budget": float(payload.budget),
        "currency": payload.currency.upper(),
        "progress_percent": 0,
        "status": "planned",
    }


@router.get(
    "/drydock-projects/{project_id}",
    summary="Get Dry Dock or Repair Project",
)
def get_drydock_project(
    project_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT *
                FROM technical_drydock_projects
                WHERE id = :project_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "project_id": project_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dry dock project not found.",
        )

    return serialize_row(row)


@router.get(
    "/drydock/intelligence",
    summary="Dry Dock Cost and Schedule Intelligence",
)
def drydock_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    id,
                    vessel_id,
                    vessel_name,
                    project_name,
                    shipyard,
                    planned_start,
                    planned_end,
                    budget,
                    actual_cost,
                    currency,
                    progress_percent,
                    status,
                    CASE
                        WHEN budget > 0
                        THEN ROUND(
                            (actual_cost / budget) * 100,
                            2
                        )
                        ELSE 0
                    END AS budget_utilization_percent,
                    CASE
                        WHEN status NOT IN ('completed', 'cancelled')
                             AND planned_end < CURRENT_DATE
                        THEN TRUE
                        ELSE FALSE
                    END AS schedule_overdue
                FROM technical_drydock_projects
                WHERE tenant_id = :tenant_id
                ORDER BY planned_start DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "total_projects": len(rows),
        "projects": [
            serialize_row(row)
            for row in rows
        ],
    }

# ============================================================
# VESSEL CERTIFICATE INTELLIGENCE
# ============================================================

@router.get(
    "/certificates",
    summary="List Vessel Certificates",
)
def list_certificates(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *,
                    CASE
                        WHEN expiry_date IS NULL THEN 'NO_EXPIRY'
                        WHEN expiry_date < CURRENT_DATE THEN 'EXPIRED'
                        WHEN expiry_date <= CURRENT_DATE + 30
                            THEN 'EXPIRING_30_DAYS'
                        WHEN expiry_date <= CURRENT_DATE + 90
                            THEN 'EXPIRING_90_DAYS'
                        ELSE 'VALID'
                    END AS expiry_intelligence
                FROM technical_certificates
                WHERE tenant_id = :tenant_id
                ORDER BY expiry_date ASC NULLS LAST,
                         created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/certificates",
    status_code=status.HTTP_201_CREATED,
    summary="Create Vessel Certificate",
)
def create_certificate(
    payload: CertificateCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()

    if (
        payload.issue_date
        and payload.expiry_date
        and payload.expiry_date < payload.issue_date
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Expiry date cannot be before issue date.",
        )

    certificate_status = "valid"

    if (
        payload.expiry_date
        and payload.expiry_date < date.today()
    ):
        certificate_status = "expired"

    engine = get_engine()
    certificate_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO technical_certificates (
                    id,
                    tenant_id,
                    vessel_id,
                    vessel_name,
                    certificate_name,
                    certificate_number,
                    issuing_authority,
                    issue_date,
                    expiry_date,
                    document_reference,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :vessel_id,
                    :vessel_name,
                    :certificate_name,
                    :certificate_number,
                    :issuing_authority,
                    :issue_date,
                    :expiry_date,
                    :document_reference,
                    :status,
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": certificate_id,
                "tenant_id": identity.tenant_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "certificate_name": payload.certificate_name,
                "certificate_number":
                    payload.certificate_number,
                "issuing_authority":
                    payload.issuing_authority,
                "issue_date": payload.issue_date,
                "expiry_date": payload.expiry_date,
                "document_reference":
                    payload.document_reference,
                "status": certificate_status,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(certificate_id),
        "certificate_name": payload.certificate_name,
        "expiry_date": (
            payload.expiry_date.isoformat()
            if payload.expiry_date
            else None
        ),
        "status": certificate_status,
    }


@router.get(
    "/certificates/intelligence/expiry",
    summary="Certificate Expiry Intelligence",
)
def certificate_expiry_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()
    engine = get_engine()

    with engine.begin() as conn:
        metrics = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_certificates,

                    COUNT(*) FILTER (
                        WHERE expiry_date < CURRENT_DATE
                    ) AS expired,

                    COUNT(*) FILTER (
                        WHERE expiry_date >= CURRENT_DATE
                          AND expiry_date <= CURRENT_DATE + 30
                    ) AS expiring_30_days,

                    COUNT(*) FILTER (
                        WHERE expiry_date > CURRENT_DATE + 30
                          AND expiry_date <= CURRENT_DATE + 90
                    ) AS expiring_90_days

                FROM technical_certificates
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        priority = conn.execute(
            text(
                """
                SELECT *
                FROM technical_certificates
                WHERE tenant_id = :tenant_id
                  AND expiry_date IS NOT NULL
                  AND expiry_date <= CURRENT_DATE + 90
                ORDER BY expiry_date ASC
                LIMIT 100
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "total_certificates": int(
            metrics["total_certificates"]
        ),
        "expired": int(metrics["expired"]),
        "expiring_30_days": int(
            metrics["expiring_30_days"]
        ),
        "expiring_90_days": int(
            metrics["expiring_90_days"]
        ),
        "priority_certificates": [
            serialize_row(row)
            for row in priority
        ],
    }

# ============================================================
# FUEL / BUNKER / ENERGY INTELLIGENCE
# ============================================================

@router.get(
    "/bunker-records",
    summary="List Vessel Fuel and Bunker Records",
)
def list_bunker_records(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM technical_bunker_records
                WHERE tenant_id = :tenant_id
                ORDER BY record_date DESC, created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/bunker-records",
    status_code=status.HTTP_201_CREATED,
    summary="Create Vessel Fuel and Bunker Record",
)
def create_bunker_record(
    payload: BunkerRecordCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()

    calculated_closing = (
        payload.opening_quantity
        + payload.received_quantity
        - payload.consumed_quantity
    )

    tolerance = Decimal("0.01")

    if abs(calculated_closing - payload.closing_quantity) > tolerance:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Bunker balance mismatch. Closing quantity must equal "
                "opening + received - consumed."
            ),
        )

    engine = get_engine()
    record_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO technical_bunker_records (
                    id,
                    tenant_id,
                    vessel_id,
                    vessel_name,
                    record_date,
                    fuel_type,
                    opening_quantity,
                    received_quantity,
                    consumed_quantity,
                    closing_quantity,
                    unit,
                    main_engine_hours,
                    main_engine_rpm,
                    distance_nm,
                    remarks,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :vessel_id,
                    :vessel_name,
                    :record_date,
                    :fuel_type,
                    :opening_quantity,
                    :received_quantity,
                    :consumed_quantity,
                    :closing_quantity,
                    :unit,
                    :main_engine_hours,
                    :main_engine_rpm,
                    :distance_nm,
                    :remarks,
                    :created_by,
                    :created_at
                )
                """
            ),
            {
                "id": record_id,
                "tenant_id": identity.tenant_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "record_date": payload.record_date,
                "fuel_type": payload.fuel_type.upper(),
                "opening_quantity": payload.opening_quantity,
                "received_quantity": payload.received_quantity,
                "consumed_quantity": payload.consumed_quantity,
                "closing_quantity": payload.closing_quantity,
                "unit": payload.unit.upper(),
                "main_engine_hours": payload.main_engine_hours,
                "main_engine_rpm": payload.main_engine_rpm,
                "distance_nm": payload.distance_nm,
                "remarks": payload.remarks,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

    return {
        "id": str(record_id),
        "fuel_type": payload.fuel_type.upper(),
        "consumed_quantity": float(payload.consumed_quantity),
        "closing_quantity": float(payload.closing_quantity),
        "unit": payload.unit.upper(),
    }


@router.get(
    "/fuel/intelligence",
    summary="Fleet Fuel Efficiency Intelligence",
)
def fuel_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()
    engine = get_engine()

    with engine.begin() as conn:
        fleet = conn.execute(
            text(
                """
                SELECT
                    vessel_id,
                    vessel_name,
                    fuel_type,
                    unit,
                    COUNT(*) AS records,
                    SUM(consumed_quantity) AS total_consumption,
                    SUM(distance_nm) AS total_distance_nm,
                    SUM(main_engine_hours) AS total_engine_hours,
                    AVG(main_engine_rpm) AS average_rpm,
                    CASE
                        WHEN SUM(distance_nm) > 0
                        THEN ROUND(
                            SUM(consumed_quantity)
                            / SUM(distance_nm),
                            4
                        )
                        ELSE NULL
                    END AS consumption_per_nm,
                    CASE
                        WHEN SUM(main_engine_hours) > 0
                        THEN ROUND(
                            SUM(consumed_quantity)
                            / SUM(main_engine_hours),
                            4
                        )
                        ELSE NULL
                    END AS consumption_per_engine_hour
                FROM technical_bunker_records
                WHERE tenant_id = :tenant_id
                GROUP BY
                    vessel_id,
                    vessel_name,
                    fuel_type,
                    unit
                ORDER BY total_consumption DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vessel_fuel_performance": [
            serialize_row(row)
            for row in fleet
        ],
    }

# ============================================================
# TECHNICAL EXECUTIVE DASHBOARD
# ============================================================

@router.get(
    "/dashboard",
    summary="Technical Executive Intelligence Dashboard",
)
def technical_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()
    engine = get_engine()

    with engine.begin() as conn:
        metrics = conn.execute(
            text(
                """
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM technical_defects
                        WHERE tenant_id = :tenant_id
                          AND status <> 'closed'
                    ) AS open_defects,

                    (
                        SELECT COUNT(*)
                        FROM technical_defects
                        WHERE tenant_id = :tenant_id
                          AND status <> 'closed'
                          AND severity IN ('high', 'critical')
                    ) AS critical_defects,

                    (
                        SELECT COUNT(*)
                        FROM technical_defects
                        WHERE tenant_id = :tenant_id
                          AND status <> 'closed'
                          AND target_date IS NOT NULL
                          AND target_date < CURRENT_DATE
                    ) AS overdue_defects,

                    (
                        SELECT COUNT(*)
                        FROM technical_drydock_projects
                        WHERE tenant_id = :tenant_id
                          AND status NOT IN ('completed', 'cancelled')
                    ) AS active_drydock_projects,

                    (
                        SELECT COUNT(*)
                        FROM technical_certificates
                        WHERE tenant_id = :tenant_id
                          AND expiry_date IS NOT NULL
                          AND expiry_date < CURRENT_DATE
                    ) AS expired_certificates,

                    (
                        SELECT COUNT(*)
                        FROM technical_certificates
                        WHERE tenant_id = :tenant_id
                          AND expiry_date >= CURRENT_DATE
                          AND expiry_date <= CURRENT_DATE + 30
                    ) AS certificates_expiring_30_days
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

    risk_score = (
        int(metrics["critical_defects"]) * 5
        + int(metrics["overdue_defects"]) * 3
        + int(metrics["expired_certificates"]) * 5
        + int(metrics["certificates_expiring_30_days"]) * 2
    )

    if risk_score >= 40:
        risk_level = "CRITICAL"
    elif risk_score >= 20:
        risk_level = "HIGH"
    elif risk_score >= 5:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "technical_risk_score": risk_score,
        "technical_risk_level": risk_level,
        "indicators": {
            "open_defects": int(metrics["open_defects"]),
            "high_critical_defects": int(
                metrics["critical_defects"]
            ),
            "overdue_defects": int(
                metrics["overdue_defects"]
            ),
            "active_drydock_projects": int(
                metrics["active_drydock_projects"]
            ),
            "expired_certificates": int(
                metrics["expired_certificates"]
            ),
            "certificates_expiring_30_days": int(
                metrics["certificates_expiring_30_days"]
            ),
        },
    }


# ============================================================
# RELIABILITY & TECHNICAL RISK INTELLIGENCE
# ============================================================

@router.get(
    "/risk-intelligence",
    summary="Fleet Technical Risk Intelligence",
)
def technical_risk_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    vessel_id,
                    vessel_name,
                    COUNT(*) FILTER (
                        WHERE status <> 'closed'
                    ) AS open_defects,
                    COUNT(*) FILTER (
                        WHERE status <> 'closed'
                          AND severity IN ('high', 'critical')
                    ) AS high_critical_defects,
                    COUNT(*) FILTER (
                        WHERE status <> 'closed'
                          AND target_date IS NOT NULL
                          AND target_date < CURRENT_DATE
                    ) AS overdue_defects
                FROM technical_defects
                WHERE tenant_id = :tenant_id
                GROUP BY vessel_id, vessel_name
                ORDER BY high_critical_defects DESC,
                         overdue_defects DESC,
                         open_defects DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    vessels = []

    for row in rows:
        score = (
            int(row["high_critical_defects"]) * 5
            + int(row["overdue_defects"]) * 3
            + int(row["open_defects"])
        )

        if score >= 25:
            level = "CRITICAL"
        elif score >= 15:
            level = "HIGH"
        elif score >= 5:
            level = "MEDIUM"
        else:
            level = "LOW"

        item = serialize_row(row)
        item["technical_risk_score"] = score
        item["technical_risk_level"] = level
        vessels.append(item)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vessel_risk_intelligence": vessels,
    }


# ============================================================
# TECHNICAL AUDIT TRAIL
# ============================================================

@router.get(
    "/audit-trail",
    summary="Technical Engineering Audit Trail",
)
def technical_audit_trail(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_technical_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM (
                    SELECT
                        'defect_reported' AS event_type,
                        id AS entity_id,
                        defect_title AS reference,
                        severity AS description,
                        created_by,
                        created_at
                    FROM technical_defects
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'drydock_project_created',
                        id,
                        project_name,
                        status,
                        created_by,
                        created_at
                    FROM technical_drydock_projects
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'certificate_recorded',
                        id,
                        certificate_name,
                        status,
                        created_by,
                        created_at
                    FROM technical_certificates
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'bunker_recorded',
                        id,
                        fuel_type,
                        CAST(consumed_quantity AS VARCHAR),
                        created_by,
                        created_at
                    FROM technical_bunker_records
                    WHERE tenant_id = :tenant_id
                ) AS events
                ORDER BY created_at DESC
                LIMIT 500
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# Initialize Technical & Engineering schema when module is loaded.
init_technical_tables()

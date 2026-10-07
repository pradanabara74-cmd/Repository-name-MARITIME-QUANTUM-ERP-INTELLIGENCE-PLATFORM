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
    prefix="/api/v1/hsse",
    tags=["HSSE HSEQ & Compliance Intelligence"],
)


# ============================================================
# REQUEST MODELS
# ============================================================

class IncidentCreate(BaseModel):
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    incident_type: str = Field(min_length=1, max_length=100)
    severity: str = Field(min_length=1, max_length=50)
    incident_date: datetime
    location: Optional[str] = Field(default=None, max_length=255)
    description: str = Field(min_length=1)
    immediate_action: Optional[str] = None
    reported_by: Optional[str] = Field(default=None, max_length=255)


class RiskAssessmentCreate(BaseModel):
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    activity: str = Field(min_length=1, max_length=255)
    hazard: str = Field(min_length=1)
    likelihood: int = Field(ge=1, le=5)
    consequence: int = Field(ge=1, le=5)
    control_measures: Optional[str] = None
    responsible_person: Optional[str] = Field(
        default=None,
        max_length=255,
    )
    review_date: Optional[date] = None


class InspectionCreate(BaseModel):
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    inspection_type: str = Field(min_length=1, max_length=150)
    inspection_date: date
    inspector: str = Field(min_length=1, max_length=255)
    location: Optional[str] = Field(default=None, max_length=255)
    findings_count: int = Field(default=0, ge=0)
    observations: Optional[str] = None


class CAPACreate(BaseModel):
    source_type: str = Field(min_length=1, max_length=100)
    source_id: Optional[str] = Field(default=None, max_length=255)
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    finding: str = Field(min_length=1)
    root_cause: Optional[str] = None
    corrective_action: str = Field(min_length=1)
    preventive_action: Optional[str] = None
    responsible_person: str = Field(min_length=1, max_length=255)
    target_date: date
    priority: str = Field(default="medium", max_length=50)


class CAPAStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=50)
    closure_evidence: Optional[str] = None


class ComplianceCreate(BaseModel):
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    framework: str = Field(min_length=1, max_length=100)
    requirement: str = Field(min_length=1)
    compliance_status: str = Field(
        default="compliant",
        max_length=50,
    )
    evidence_reference: Optional[str] = None
    review_date: Optional[date] = None
    responsible_person: Optional[str] = Field(
        default=None,
        max_length=255,
    )


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

def init_hsse_tables():
    engine = get_engine()

    with engine.begin() as conn:

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS hsse_incidents (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    incident_type VARCHAR(100) NOT NULL,
                    severity VARCHAR(50) NOT NULL,
                    incident_date TIMESTAMPTZ NOT NULL,
                    location VARCHAR(255),
                    description TEXT NOT NULL,
                    immediate_action TEXT,
                    reported_by VARCHAR(255),
                    status VARCHAR(50) NOT NULL DEFAULT 'open',
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
                CREATE TABLE IF NOT EXISTS hsse_risk_assessments (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    activity VARCHAR(255) NOT NULL,
                    hazard TEXT NOT NULL,
                    likelihood INTEGER NOT NULL,
                    consequence INTEGER NOT NULL,
                    risk_score INTEGER NOT NULL,
                    risk_level VARCHAR(50) NOT NULL,
                    control_measures TEXT,
                    responsible_person VARCHAR(255),
                    review_date DATE,
                    status VARCHAR(50) NOT NULL DEFAULT 'active',
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
                CREATE TABLE IF NOT EXISTS hsse_inspections (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    inspection_type VARCHAR(150) NOT NULL,
                    inspection_date DATE NOT NULL,
                    inspector VARCHAR(255) NOT NULL,
                    location VARCHAR(255),
                    findings_count INTEGER NOT NULL DEFAULT 0,
                    observations TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'completed',
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS hsse_capa (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    source_type VARCHAR(100) NOT NULL,
                    source_id VARCHAR(255),
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    finding TEXT NOT NULL,
                    root_cause TEXT,
                    corrective_action TEXT NOT NULL,
                    preventive_action TEXT,
                    responsible_person VARCHAR(255) NOT NULL,
                    target_date DATE NOT NULL,
                    priority VARCHAR(50) NOT NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'open',
                    closure_evidence TEXT,
                    closed_by VARCHAR(255),
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
                CREATE TABLE IF NOT EXISTS hsse_compliance (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    framework VARCHAR(100) NOT NULL,
                    requirement TEXT NOT NULL,
                    compliance_status VARCHAR(50) NOT NULL,
                    evidence_reference TEXT,
                    review_date DATE,
                    responsible_person VARCHAR(255),
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
                CREATE INDEX IF NOT EXISTS idx_hsse_incident_status
                ON hsse_incidents (tenant_id, status, severity)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_hsse_risk_level
                ON hsse_risk_assessments
                (tenant_id, risk_level, status)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_hsse_capa_status
                ON hsse_capa
                (tenant_id, status, target_date)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_hsse_compliance
                ON hsse_compliance
                (tenant_id, framework, compliance_status)
                """
            )
        )

  # ============================================================
# INCIDENT / NEAR MISS MANAGEMENT
# ============================================================

@router.get(
    "/incidents",
    summary="List HSSE Incidents and Near Misses",
)
def list_incidents(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hsse_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM hsse_incidents
                WHERE tenant_id = :tenant_id
                ORDER BY incident_date DESC, created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/incidents",
    status_code=status.HTTP_201_CREATED,
    summary="Report HSSE Incident or Near Miss",
)
def create_incident(
    payload: IncidentCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hsse_tables()

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
    incident_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO hsse_incidents (
                    id,
                    tenant_id,
                    vessel_id,
                    vessel_name,
                    incident_type,
                    severity,
                    incident_date,
                    location,
                    description,
                    immediate_action,
                    reported_by,
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
                    :incident_type,
                    :severity,
                    :incident_date,
                    :location,
                    :description,
                    :immediate_action,
                    :reported_by,
                    'open',
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": incident_id,
                "tenant_id": identity.tenant_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "incident_type": payload.incident_type,
                "severity": severity,
                "incident_date": payload.incident_date,
                "location": payload.location,
                "description": payload.description,
                "immediate_action": payload.immediate_action,
                "reported_by": payload.reported_by,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(incident_id),
        "incident_type": payload.incident_type,
        "severity": severity,
        "status": "open",
        "incident_date": payload.incident_date.isoformat(),
    }


@router.get(
    "/incidents/{incident_id}",
    summary="Get HSSE Incident",
)
def get_incident(
    incident_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hsse_tables()
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT *
                FROM hsse_incidents
                WHERE id = :incident_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "incident_id": incident_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="HSSE incident not found.",
        )

    return serialize_row(row)


@router.get(
    "/incidents/intelligence/summary",
    summary="Incident and Near-Miss Intelligence",
)
def incident_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hsse_tables()
    engine = get_engine()

    with engine.begin() as conn:
        metrics = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_incidents,
                    COUNT(*) FILTER (
                        WHERE status = 'open'
                    ) AS open_incidents,
                    COUNT(*) FILTER (
                        WHERE severity = 'critical'
                    ) AS critical_incidents,
                    COUNT(*) FILTER (
                        WHERE severity = 'high'
                    ) AS high_incidents
                FROM hsse_incidents
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        types = conn.execute(
            text(
                """
                SELECT
                    incident_type,
                    COUNT(*) AS incident_count
                FROM hsse_incidents
                WHERE tenant_id = :tenant_id
                GROUP BY incident_type
                ORDER BY incident_count DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "total_incidents": int(metrics["total_incidents"]),
        "open_incidents": int(metrics["open_incidents"]),
        "critical_incidents": int(
            metrics["critical_incidents"]
        ),
        "high_incidents": int(metrics["high_incidents"]),
        "incident_type_distribution": [
            serialize_row(row)
            for row in types
        ],
    }

# ============================================================
# RISK ASSESSMENT / RISK MATRIX INTELLIGENCE
# ============================================================

def calculate_risk_level(score: int) -> str:
    if score >= 20:
        return "CRITICAL"
    if score >= 12:
        return "HIGH"
    if score >= 5:
        return "MEDIUM"
    return "LOW"


@router.get(
    "/risk-assessments",
    summary="List HSSE Risk Assessments",
)
def list_risk_assessments(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hsse_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM hsse_risk_assessments
                WHERE tenant_id = :tenant_id
                ORDER BY risk_score DESC, created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/risk-assessments",
    status_code=status.HTTP_201_CREATED,
    summary="Create HSSE Risk Assessment",
)
def create_risk_assessment(
    payload: RiskAssessmentCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hsse_tables()

    risk_score = payload.likelihood * payload.consequence
    risk_level = calculate_risk_level(risk_score)

    engine = get_engine()
    risk_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO hsse_risk_assessments (
                    id,
                    tenant_id,
                    vessel_id,
                    vessel_name,
                    activity,
                    hazard,
                    likelihood,
                    consequence,
                    risk_score,
                    risk_level,
                    control_measures,
                    responsible_person,
                    review_date,
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
                    :activity,
                    :hazard,
                    :likelihood,
                    :consequence,
                    :risk_score,
                    :risk_level,
                    :control_measures,
                    :responsible_person,
                    :review_date,
                    'active',
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": risk_id,
                "tenant_id": identity.tenant_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "activity": payload.activity,
                "hazard": payload.hazard,
                "likelihood": payload.likelihood,
                "consequence": payload.consequence,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "control_measures": payload.control_measures,
                "responsible_person":
                    payload.responsible_person,
                "review_date": payload.review_date,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(risk_id),
        "activity": payload.activity,
        "likelihood": payload.likelihood,
        "consequence": payload.consequence,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "status": "active",
    }


@router.get(
    "/risk-assessments/intelligence/matrix",
    summary="HSSE Risk Matrix Intelligence",
)
def risk_matrix_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hsse_tables()
    engine = get_engine()

    with engine.begin() as conn:
        levels = conn.execute(
            text(
                """
                SELECT
                    risk_level,
                    COUNT(*) AS risk_count
                FROM hsse_risk_assessments
                WHERE tenant_id = :tenant_id
                  AND status = 'active'
                GROUP BY risk_level
                ORDER BY risk_count DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        critical_risks = conn.execute(
            text(
                """
                SELECT *
                FROM hsse_risk_assessments
                WHERE tenant_id = :tenant_id
                  AND status = 'active'
                  AND risk_level IN ('CRITICAL', 'HIGH')
                ORDER BY risk_score DESC, created_at DESC
                LIMIT 50
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    total_active = sum(
        int(row["risk_count"])
        for row in levels
    )

    return {
        "total_active_risks": total_active,
        "risk_distribution": [
            serialize_row(row)
            for row in levels
        ],
        "priority_risks": [
            serialize_row(row)
            for row in critical_risks
        ],
    }

# ============================================================
# HSSE INSPECTION MANAGEMENT
# ============================================================

@router.get(
    "/inspections",
    summary="List HSSE Inspections",
)
def list_inspections(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hsse_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM hsse_inspections
                WHERE tenant_id = :tenant_id
                ORDER BY inspection_date DESC, created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/inspections",
    status_code=status.HTTP_201_CREATED,
    summary="Create HSSE Inspection",
)
def create_inspection(
    payload: InspectionCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hsse_tables()
    engine = get_engine()
    inspection_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO hsse_inspections (
                    id,
                    tenant_id,
                    vessel_id,
                    vessel_name,
                    inspection_type,
                    inspection_date,
                    inspector,
                    location,
                    findings_count,
                    observations,
                    status,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :vessel_id,
                    :vessel_name,
                    :inspection_type,
                    :inspection_date,
                    :inspector,
                    :location,
                    :findings_count,
                    :observations,
                    'completed',
                    :created_by,
                    :created_at
                )
                """
            ),
            {
                "id": inspection_id,
                "tenant_id": identity.tenant_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "inspection_type": payload.inspection_type,
                "inspection_date": payload.inspection_date,
                "inspector": payload.inspector,
                "location": payload.location,
                "findings_count": payload.findings_count,
                "observations": payload.observations,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

    return {
        "id": str(inspection_id),
        "inspection_type": payload.inspection_type,
        "inspection_date": payload.inspection_date.isoformat(),
        "findings_count": payload.findings_count,
        "status": "completed",
    }


# ============================================================
# CAPA — CORRECTIVE / PREVENTIVE ACTION
# ============================================================

@router.get(
    "/capa",
    summary="List Corrective and Preventive Actions",
)
def list_capa(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hsse_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM hsse_capa
                WHERE tenant_id = :tenant_id
                ORDER BY
                    CASE priority
                        WHEN 'critical' THEN 1
                        WHEN 'high' THEN 2
                        WHEN 'medium' THEN 3
                        ELSE 4
                    END,
                    target_date ASC,
                    created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/capa",
    status_code=status.HTTP_201_CREATED,
    summary="Create Corrective or Preventive Action",
)
def create_capa(
    payload: CAPACreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hsse_tables()

    priority = payload.priority.strip().lower()

    if priority not in {
        "low",
        "medium",
        "high",
        "critical",
    }:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Priority must be low, medium, high, or critical.",
        )

    engine = get_engine()
    capa_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO hsse_capa (
                    id,
                    tenant_id,
                    source_type,
                    source_id,
                    vessel_id,
                    vessel_name,
                    finding,
                    root_cause,
                    corrective_action,
                    preventive_action,
                    responsible_person,
                    target_date,
                    priority,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :source_type,
                    :source_id,
                    :vessel_id,
                    :vessel_name,
                    :finding,
                    :root_cause,
                    :corrective_action,
                    :preventive_action,
                    :responsible_person,
                    :target_date,
                    :priority,
                    'open',
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": capa_id,
                "tenant_id": identity.tenant_id,
                "source_type": payload.source_type,
                "source_id": payload.source_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "finding": payload.finding,
                "root_cause": payload.root_cause,
                "corrective_action": payload.corrective_action,
                "preventive_action": payload.preventive_action,
                "responsible_person": payload.responsible_person,
                "target_date": payload.target_date,
                "priority": priority,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(capa_id),
        "priority": priority,
        "target_date": payload.target_date.isoformat(),
        "status": "open",
    }


@router.post(
    "/capa/{capa_id}/status",
    summary="Update CAPA Status",
)
def update_capa_status(
    capa_id: UUID,
    payload: CAPAStatusUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hsse_tables()

    new_status = payload.status.strip().lower()

    if new_status not in {
        "open",
        "in_progress",
        "closed",
    }:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status must be open, in_progress, or closed.",
        )

    if new_status == "closed" and not payload.closure_evidence:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Closure evidence is required to close CAPA.",
        )

    engine = get_engine()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT id
                FROM hsse_capa
                WHERE id = :capa_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {
                "capa_id": capa_id,
                "tenant_id": identity.tenant_id,
            },
        ).first()

        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="CAPA not found.",
            )

        conn.execute(
            text(
                """
                UPDATE hsse_capa
                SET status = :status,
                    closure_evidence = :closure_evidence,
                    closed_by = :closed_by,
                    closed_at = :closed_at,
                    updated_at = :updated_at
                WHERE id = :capa_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "status": new_status,
                "closure_evidence": payload.closure_evidence,
                "closed_by": (
                    identity.user_id
                    if new_status == "closed"
                    else None
                ),
                "closed_at": (
                    now
                    if new_status == "closed"
                    else None
                ),
                "updated_at": now,
                "capa_id": capa_id,
                "tenant_id": identity.tenant_id,
            },
        )

    return {
        "id": str(capa_id),
        "status": new_status,
        "updated_at": now.isoformat(),
    }

# ============================================================
# ISM / ISPS / HSEQ COMPLIANCE
# ============================================================

@router.get(
    "/compliance",
    summary="List HSSE Compliance Records",
)
def list_compliance(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hsse_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM hsse_compliance
                WHERE tenant_id = :tenant_id
                ORDER BY framework, review_date ASC NULLS LAST
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/compliance",
    status_code=status.HTTP_201_CREATED,
    summary="Create HSSE Compliance Record",
)
def create_compliance(
    payload: ComplianceCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hsse_tables()

    compliance_status = payload.compliance_status.strip().lower()

    if compliance_status not in {
        "compliant",
        "observation",
        "non_conformity",
        "critical_non_conformity",
    }:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid compliance status.",
        )

    engine = get_engine()
    compliance_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO hsse_compliance (
                    id,
                    tenant_id,
                    vessel_id,
                    vessel_name,
                    framework,
                    requirement,
                    compliance_status,
                    evidence_reference,
                    review_date,
                    responsible_person,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :vessel_id,
                    :vessel_name,
                    :framework,
                    :requirement,
                    :compliance_status,
                    :evidence_reference,
                    :review_date,
                    :responsible_person,
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": compliance_id,
                "tenant_id": identity.tenant_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "framework": payload.framework.upper(),
                "requirement": payload.requirement,
                "compliance_status": compliance_status,
                "evidence_reference": payload.evidence_reference,
                "review_date": payload.review_date,
                "responsible_person":
                    payload.responsible_person,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(compliance_id),
        "framework": payload.framework.upper(),
        "compliance_status": compliance_status,
    }


# ============================================================
# HSSE EXECUTIVE DASHBOARD
# ============================================================

@router.get(
    "/dashboard",
    summary="HSSE Executive Intelligence Dashboard",
)
def hsse_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hsse_tables()
    engine = get_engine()

    with engine.begin() as conn:
        metrics = conn.execute(
            text(
                """
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM hsse_incidents
                        WHERE tenant_id = :tenant_id
                          AND status = 'open'
                    ) AS open_incidents,

                    (
                        SELECT COUNT(*)
                        FROM hsse_risk_assessments
                        WHERE tenant_id = :tenant_id
                          AND status = 'active'
                          AND risk_level IN (
                              'HIGH',
                              'CRITICAL'
                          )
                    ) AS high_risks,

                    (
                        SELECT COUNT(*)
                        FROM hsse_capa
                        WHERE tenant_id = :tenant_id
                          AND status <> 'closed'
                    ) AS open_capa,

                    (
                        SELECT COUNT(*)
                        FROM hsse_capa
                        WHERE tenant_id = :tenant_id
                          AND status <> 'closed'
                          AND target_date < CURRENT_DATE
                    ) AS overdue_capa,

                    (
                        SELECT COUNT(*)
                        FROM hsse_compliance
                        WHERE tenant_id = :tenant_id
                          AND compliance_status IN (
                              'non_conformity',
                              'critical_non_conformity'
                          )
                    ) AS non_conformities
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

    risk_score = (
        int(metrics["open_incidents"]) * 2
        + int(metrics["high_risks"]) * 4
        + int(metrics["open_capa"]) * 2
        + int(metrics["overdue_capa"]) * 5
        + int(metrics["non_conformities"]) * 4
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
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "enterprise_hsse_risk_level": risk_level,
        "enterprise_hsse_risk_score": risk_score,
        "indicators": {
            "open_incidents": int(
                metrics["open_incidents"]
            ),
            "high_and_critical_risks": int(
                metrics["high_risks"]
            ),
            "open_capa": int(
                metrics["open_capa"]
            ),
            "overdue_capa": int(
                metrics["overdue_capa"]
            ),
            "non_conformities": int(
                metrics["non_conformities"]
            ),
        },
    }


# ============================================================
# HSSE AUDIT TRAIL
# ============================================================

@router.get(
    "/audit-trail",
    summary="HSSE Audit Trail",
)
def hsse_audit_trail(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hsse_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM (
                    SELECT
                        'incident_reported' AS event_type,
                        id AS entity_id,
                        incident_type AS reference,
                        severity AS description,
                        created_by,
                        created_at
                    FROM hsse_incidents
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'risk_assessment_created',
                        id,
                        activity,
                        risk_level,
                        created_by,
                        created_at
                    FROM hsse_risk_assessments
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'inspection_completed',
                        id,
                        inspection_type,
                        inspector,
                        created_by,
                        created_at
                    FROM hsse_inspections
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'capa_created',
                        id,
                        source_type,
                        status,
                        created_by,
                        created_at
                    FROM hsse_capa
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'compliance_recorded',
                        id,
                        framework,
                        compliance_status,
                        created_by,
                        created_at
                    FROM hsse_compliance
                    WHERE tenant_id = :tenant_id
                ) AS events
                ORDER BY created_at DESC
                LIMIT 500
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# Initialize HSSE schema when module is loaded.
init_hsse_tables()

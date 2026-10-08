# ============================================================
# MQEIP - AUDIT & INTERNAL CONTROL INTELLIGENCE
# BLOK 1: FOUNDATION, MODELS & DATABASE
# ============================================================

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..core.database import get_engine
from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/audit",
    tags=["Audit & Internal Control Intelligence"],
)


# ============================================================
# DATA MODELS
# ============================================================

class AuditPlanCreate(BaseModel):
    audit_number: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=255)
    audit_type: str = Field(min_length=1, max_length=100)
    department: Optional[str] = None
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    scope: Optional[str] = None
    objectives: Optional[str] = None
    lead_auditor: Optional[str] = None
    planned_start: date
    planned_end: date


class AuditStatusUpdate(BaseModel):
    status: str
    remarks: Optional[str] = None


class ControlCreate(BaseModel):
    control_number: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=255)
    department: Optional[str] = None
    description: Optional[str] = None
    control_owner: Optional[str] = None
    frequency: str = "monthly"
    risk_category: str = "operational"


class ControlTestCreate(BaseModel):
    control_id: UUID
    audit_id: Optional[UUID] = None
    test_date: date
    result: str
    observations: Optional[str] = None
    evidence_reference: Optional[str] = None
    tested_by: Optional[str] = None


class AuditFindingCreate(BaseModel):
    audit_id: UUID
    finding_number: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    severity: str = "medium"
    responsible_person: Optional[str] = None
    target_date: Optional[date] = None


class AuditFindingUpdate(BaseModel):
    status: str
    corrective_action: Optional[str] = None
    closure_evidence: Optional[str] = None


# ============================================================
# SERIALIZATION
# ============================================================

def serialize_row(row):
    result = {}

    for key, value in dict(row).items():
        if isinstance(value, UUID):
            result[key] = str(value)
        elif isinstance(value, Decimal):
            result[key] = float(value)
        elif isinstance(value, (date, datetime)):
            result[key] = value.isoformat()
        else:
            result[key] = value

    return result


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_audit_tables():
    engine = get_engine()

    with engine.begin() as conn:

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_plans (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                audit_number VARCHAR(100) NOT NULL,
                title VARCHAR(255) NOT NULL,
                audit_type VARCHAR(100) NOT NULL,
                department VARCHAR(255),
                vessel_id VARCHAR(255),
                vessel_name VARCHAR(255),
                scope TEXT,
                objectives TEXT,
                lead_auditor VARCHAR(255),
                planned_start DATE NOT NULL,
                planned_end DATE NOT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'planned',
                remarks TEXT,
                created_by VARCHAR(255) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE (tenant_id, audit_number)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_controls (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                control_number VARCHAR(100) NOT NULL,
                title VARCHAR(255) NOT NULL,
                department VARCHAR(255),
                description TEXT,
                control_owner VARCHAR(255),
                frequency VARCHAR(50),
                risk_category VARCHAR(100),
                status VARCHAR(30) NOT NULL DEFAULT 'active',
                created_by VARCHAR(255) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE (tenant_id, control_number)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_control_tests (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                control_id UUID NOT NULL,
                audit_id UUID,
                test_date DATE NOT NULL,
                result VARCHAR(30) NOT NULL,
                observations TEXT,
                evidence_reference TEXT,
                tested_by VARCHAR(255),
                created_by VARCHAR(255) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_findings (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                audit_id UUID NOT NULL,
                finding_number VARCHAR(100) NOT NULL,
                title VARCHAR(255) NOT NULL,
                description TEXT,
                severity VARCHAR(30) NOT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'open',
                responsible_person VARCHAR(255),
                target_date DATE,
                corrective_action TEXT,
                closure_evidence TEXT,
                closed_at TIMESTAMPTZ,
                created_by VARCHAR(255) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE (tenant_id, finding_number)
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_audit_plan_status
            ON audit_plans (tenant_id, status)
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_audit_controls
            ON audit_controls (tenant_id, status)
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_audit_control_tests
            ON audit_control_tests (tenant_id, control_id)
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_audit_findings_status
            ON audit_findings (tenant_id, status)
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_audit_findings_audit
            ON audit_findings (tenant_id, audit_id)
        """))


# ============================================================
# AUDIT PLANNING
# ============================================================

@router.get("/plans", summary="List Audit Plans")
def list_audit_plans(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_audit_tables()

    with get_engine().begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM audit_plans
                WHERE tenant_id = :tenant_id
                ORDER BY planned_start DESC, created_at DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/plans",
    status_code=status.HTTP_201_CREATED,
    summary="Create Audit Plan",
)
def create_audit_plan(
    payload: AuditPlanCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_audit_tables()

    if payload.planned_end < payload.planned_start:
        raise HTTPException(
            status_code=400,
            detail="Planned end cannot precede planned start.",
        )

    audit_id = uuid4()
    now = datetime.now(timezone.utc)

    values = {
        "id": audit_id,
        "tenant_id": identity.tenant_id,
        "audit_number": payload.audit_number,
        "title": payload.title,
        "audit_type": payload.audit_type,
        "department": payload.department,
        "vessel_id": payload.vessel_id,
        "vessel_name": payload.vessel_name,
        "scope": payload.scope,
        "objectives": payload.objectives,
        "lead_auditor": payload.lead_auditor,
        "planned_start": payload.planned_start,
        "planned_end": payload.planned_end,
        "created_by": identity.user_id,
        "now": now,
    }

    try:
        with get_engine().begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO audit_plans (
                        id, tenant_id, audit_number, title,
                        audit_type, department, vessel_id,
                        vessel_name, scope, objectives,
                        lead_auditor, planned_start, planned_end,
                        status, created_by, created_at, updated_at
                    )
                    VALUES (
                        :id, :tenant_id, :audit_number, :title,
                        :audit_type, :department, :vessel_id,
                        :vessel_name, :scope, :objectives,
                        :lead_auditor, :planned_start, :planned_end,
                        'planned', :created_by, :now, :now
                    )
                """),
                values,
            )
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="Audit number already exists.",
        )

    return {
        "id": str(audit_id),
        "audit_number": payload.audit_number,
        "status": "planned",
    }

# ============================================================
# BLOK 2: AUDIT MANAGEMENT & INTERNAL CONTROL
# ============================================================

@router.get("/plans/{audit_id}", summary="Get Audit Plan")
def get_audit_plan(
    audit_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_audit_tables()

    with get_engine().begin() as conn:
        row = conn.execute(
            text("""
                SELECT *
                FROM audit_plans
                WHERE id = :id
                  AND tenant_id = :tenant_id
            """),
            {
                "id": audit_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Audit plan not found.",
        )

    return serialize_row(row)


@router.post(
    "/plans/{audit_id}/status",
    summary="Update Audit Status",
)
def update_audit_status(
    audit_id: UUID,
    payload: AuditStatusUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_audit_tables()

    new_status = payload.status.strip().lower()

    if new_status not in {
        "planned",
        "in_progress",
        "completed",
        "cancelled",
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid audit status.",
        )

    now = datetime.now(timezone.utc)

    with get_engine().begin() as conn:
        row = conn.execute(
            text("""
                UPDATE audit_plans
                SET status = :status,
                    remarks = COALESCE(:remarks, remarks),
                    updated_at = :updated_at
                WHERE id = :id
                  AND tenant_id = :tenant_id
                RETURNING id
            """),
            {
                "status": new_status,
                "remarks": payload.remarks,
                "updated_at": now,
                "id": audit_id,
                "tenant_id": identity.tenant_id,
            },
        ).first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Audit plan not found.",
        )

    return {
        "id": str(audit_id),
        "status": new_status,
        "updated_at": now.isoformat(),
    }


# ============================================================
# INTERNAL CONTROL REGISTER
# ============================================================

@router.get("/controls", summary="List Internal Controls")
def list_internal_controls(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_audit_tables()

    with get_engine().begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM audit_controls
                WHERE tenant_id = :tenant_id
                ORDER BY created_at DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/controls",
    status_code=201,
    summary="Register Internal Control",
)
def create_internal_control(
    payload: ControlCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_audit_tables()

    control_id = uuid4()
    now = datetime.now(timezone.utc)

    try:
        with get_engine().begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO audit_controls (
                        id, tenant_id, control_number,
                        title, department, description,
                        control_owner, frequency, risk_category,
                        status, created_by, created_at, updated_at
                    )
                    VALUES (
                        :id, :tenant_id, :control_number,
                        :title, :department, :description,
                        :control_owner, :frequency, :risk_category,
                        'active', :created_by, :now, :now
                    )
                """),
                {
                    "id": control_id,
                    "tenant_id": identity.tenant_id,
                    "control_number": payload.control_number,
                    "title": payload.title,
                    "department": payload.department,
                    "description": payload.description,
                    "control_owner": payload.control_owner,
                    "frequency": payload.frequency,
                    "risk_category": payload.risk_category,
                    "created_by": identity.user_id,
                    "now": now,
                },
            )
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="Control number already exists.",
        )

    return {
        "id": str(control_id),
        "control_number": payload.control_number,
        "status": "active",
    }


# ============================================================
# CONTROL EFFECTIVENESS TESTING
# ============================================================

@router.post(
    "/control-tests",
    status_code=201,
    summary="Record Internal Control Test",
)
def create_control_test(
    payload: ControlTestCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_audit_tables()

    result = payload.result.strip().lower()

    if result not in {
        "effective",
        "ineffective",
        "partially_effective",
        "not_tested",
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid control test result.",
        )

    test_id = uuid4()
    now = datetime.now(timezone.utc)

    with get_engine().begin() as conn:
        control = conn.execute(
            text("""
                SELECT id
                FROM audit_controls
                WHERE id = :id
                  AND tenant_id = :tenant_id
            """),
            {
                "id": payload.control_id,
                "tenant_id": identity.tenant_id,
            },
        ).first()

        if control is None:
            raise HTTPException(
                status_code=404,
                detail="Internal control not found.",
            )

        if payload.audit_id is not None:
            audit = conn.execute(
                text("""
                    SELECT id
                    FROM audit_plans
                    WHERE id = :id
                      AND tenant_id = :tenant_id
                """),
                {
                    "id": payload.audit_id,
                    "tenant_id": identity.tenant_id,
                },
            ).first()

            if audit is None:
                raise HTTPException(
                    status_code=404,
                    detail="Audit plan not found.",
                )

        conn.execute(
            text("""
                INSERT INTO audit_control_tests (
                    id, tenant_id, control_id, audit_id,
                    test_date, result, observations,
                    evidence_reference, tested_by,
                    created_by, created_at
                )
                VALUES (
                    :id, :tenant_id, :control_id, :audit_id,
                    :test_date, :result, :observations,
                    :evidence_reference, :tested_by,
                    :created_by, :created_at
                )
            """),
            {
                "id": test_id,
                "tenant_id": identity.tenant_id,
                "control_id": payload.control_id,
                "audit_id": payload.audit_id,
                "test_date": payload.test_date,
                "result": result,
                "observations": payload.observations,
                "evidence_reference": payload.evidence_reference,
                "tested_by": payload.tested_by,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

    return {
        "id": str(test_id),
        "control_id": str(payload.control_id),
        "result": result,
    }


@router.get(
    "/control-tests",
    summary="List Internal Control Tests",
)
def list_control_tests(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_audit_tables()

    with get_engine().begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM audit_control_tests
                WHERE tenant_id = :tenant_id
                ORDER BY test_date DESC, created_at DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]

# ============================================================
# BLOK 3: AUDIT FINDINGS & CORRECTIVE ACTIONS
# ============================================================

@router.get("/findings", summary="List Audit Findings")
def list_audit_findings(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_audit_tables()

    with get_engine().begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM audit_findings
                WHERE tenant_id = :tenant_id
                ORDER BY created_at DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/findings",
    status_code=201,
    summary="Create Audit Finding",
)
def create_audit_finding(
    payload: AuditFindingCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_audit_tables()

    severity = payload.severity.strip().lower()

    if severity not in {
        "low", "medium", "high", "critical"
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid finding severity.",
        )

    finding_id = uuid4()
    now = datetime.now(timezone.utc)

    try:
        with get_engine().begin() as conn:
            audit = conn.execute(
                text("""
                    SELECT id
                    FROM audit_plans
                    WHERE id = :audit_id
                      AND tenant_id = :tenant_id
                """),
                {
                    "audit_id": payload.audit_id,
                    "tenant_id": identity.tenant_id,
                },
            ).first()

            if audit is None:
                raise HTTPException(
                    status_code=404,
                    detail="Audit plan not found.",
                )

            conn.execute(
                text("""
                    INSERT INTO audit_findings (
                        id, tenant_id, audit_id,
                        finding_number, title, description,
                        severity, status, responsible_person,
                        target_date, created_by,
                        created_at, updated_at
                    )
                    VALUES (
                        :id, :tenant_id, :audit_id,
                        :finding_number, :title, :description,
                        :severity, 'open', :responsible_person,
                        :target_date, :created_by,
                        :created_at, :updated_at
                    )
                """),
                {
                    "id": finding_id,
                    "tenant_id": identity.tenant_id,
                    "audit_id": payload.audit_id,
                    "finding_number": payload.finding_number,
                    "title": payload.title,
                    "description": payload.description,
                    "severity": severity,
                    "responsible_person": payload.responsible_person,
                    "target_date": payload.target_date,
                    "created_by": identity.user_id,
                    "created_at": now,
                    "updated_at": now,
                },
            )
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="Finding number already exists.",
        )

    return {
        "id": str(finding_id),
        "finding_number": payload.finding_number,
        "severity": severity,
        "status": "open",
    }


@router.get(
    "/findings/{finding_id}",
    summary="Get Audit Finding",
)
def get_audit_finding(
    finding_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_audit_tables()

    with get_engine().begin() as conn:
        row = conn.execute(
            text("""
                SELECT *
                FROM audit_findings
                WHERE id = :finding_id
                  AND tenant_id = :tenant_id
            """),
            {
                "finding_id": finding_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Audit finding not found.",
        )

    return serialize_row(row)


@router.post(
    "/findings/{finding_id}/status",
    summary="Update Finding and Corrective Action",
)
def update_audit_finding(
    finding_id: UUID,
    payload: AuditFindingUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_audit_tables()

    new_status = payload.status.strip().lower()

    if new_status not in {
        "open",
        "in_progress",
        "verification",
        "closed",
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid finding status.",
        )

    now = datetime.now(timezone.utc)

    with get_engine().begin() as conn:
        existing = conn.execute(
            text("""
                SELECT *
                FROM audit_findings
                WHERE id = :finding_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "finding_id": finding_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="Audit finding not found.",
            )

        action = (
            payload.corrective_action
            if payload.corrective_action is not None
            else existing["corrective_action"]
        )

        evidence = (
            payload.closure_evidence
            if payload.closure_evidence is not None
            else existing["closure_evidence"]
        )

        if new_status == "closed" and (
            not action or not evidence
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Corrective action and closure evidence "
                    "are required before closing a finding."
                ),
            )

        conn.execute(
            text("""
                UPDATE audit_findings
                SET status = :status,
                    corrective_action = :corrective_action,
                    closure_evidence = :closure_evidence,
                    closed_at = :closed_at,
                    updated_at = :updated_at
                WHERE id = :finding_id
                  AND tenant_id = :tenant_id
            """),
            {
                "status": new_status,
                "corrective_action": action,
                "closure_evidence": evidence,
                "closed_at": (
                    now if new_status == "closed" else None
                ),
                "updated_at": now,
                "finding_id": finding_id,
                "tenant_id": identity.tenant_id,
            },
        )

    return {
        "id": str(finding_id),
        "status": new_status,
        "updated_at": now.isoformat(),
    }

# ============================================================
# BLOK 4: AUDIT DASHBOARD & RISK INTELLIGENCE
# ============================================================

@router.get(
    "/dashboard",
    summary="Audit and Internal Control Dashboard",
)
def audit_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_audit_tables()

    with get_engine().begin() as conn:
        metrics = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_findings,
                    COUNT(*) FILTER (
                        WHERE status <> 'closed'
                    ) AS open_findings,
                    COUNT(*) FILTER (
                        WHERE severity = 'critical'
                          AND status <> 'closed'
                    ) AS critical_findings,
                    COUNT(*) FILTER (
                        WHERE severity = 'high'
                          AND status <> 'closed'
                    ) AS high_findings,
                    COUNT(*) FILTER (
                        WHERE status <> 'closed'
                          AND target_date < CURRENT_DATE
                    ) AS overdue_findings,
                    COUNT(*) FILTER (
                        WHERE status = 'closed'
                    ) AS closed_findings
                FROM audit_findings
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        audit_count = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM audit_plans
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

        control_count = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM audit_controls
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

    total = int(metrics["total_findings"])
    closed = int(metrics["closed_findings"])

    closure_rate = (
        round(closed / total * 100, 2)
        if total else 0.0
    )

    risk_score = (
        int(metrics["critical_findings"]) * 10
        + int(metrics["high_findings"]) * 6
        + int(metrics["overdue_findings"]) * 5
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
        "total_audits": int(audit_count),
        "registered_controls": int(control_count),
        "total_findings": total,
        "open_findings": int(metrics["open_findings"]),
        "critical_findings": int(
            metrics["critical_findings"]
        ),
        "high_findings": int(metrics["high_findings"]),
        "overdue_findings": int(
            metrics["overdue_findings"]
        ),
        "closed_findings": closed,
        "closure_rate_percent": closure_rate,
        "audit_risk_score": risk_score,
        "audit_risk_level": risk_level,
    }


# ============================================================
# AUDIT RISK INTELLIGENCE
# ============================================================

@router.get(
    "/risk-intelligence",
    summary="Audit Risk Intelligence",
)
def audit_risk_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_audit_tables()

    with get_engine().begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    id,
                    audit_id,
                    finding_number,
                    title,
                    severity,
                    status,
                    responsible_person,
                    target_date,
                    (
                        CASE
                            WHEN severity = 'critical' THEN 10
                            WHEN severity = 'high' THEN 6
                            WHEN severity = 'medium' THEN 3
                            ELSE 1
                        END
                        +
                        CASE
                            WHEN target_date < CURRENT_DATE
                                 AND status <> 'closed'
                            THEN 5
                            ELSE 0
                        END
                    ) AS risk_score
                FROM audit_findings
                WHERE tenant_id = :tenant_id
                  AND status <> 'closed'
                ORDER BY risk_score DESC,
                         target_date ASC NULLS LAST
                LIMIT 100
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "priority_findings": [
            serialize_row(row) for row in rows
        ],
        "priority_count": len(rows),
        "risk_model": "Severity + overdue weighting",
    }


# ============================================================
# INTERNAL CONTROL EFFECTIVENESS INTELLIGENCE
# ============================================================

@router.get(
    "/internal-control/intelligence",
    summary="Internal Control Effectiveness Intelligence",
)
def internal_control_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_audit_tables()

    with get_engine().begin() as conn:
        distribution = conn.execute(
            text("""
                SELECT
                    severity,
                    status,
                    COUNT(*) AS finding_count
                FROM audit_findings
                WHERE tenant_id = :tenant_id
                GROUP BY severity, status
                ORDER BY severity, status
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        latest_tests = conn.execute(
            text("""
                SELECT DISTINCT ON (control_id)
                    control_id,
                    test_date,
                    result,
                    observations
                FROM audit_control_tests
                WHERE tenant_id = :tenant_id
                ORDER BY control_id,
                         test_date DESC,
                         created_at DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "finding_distribution": [
            serialize_row(row) for row in distribution
        ],
        "latest_control_tests": [
            serialize_row(row) for row in latest_tests
        ],
        "assessment_note": (
            "These indicators support management review; "
            "they do not constitute independent certification."
        ),
    }


# ============================================================
# AUDIT ACTIVITY TRAIL
# ============================================================

@router.get(
    "/audit-trail",
    summary="Audit Activity Trail",
)
def audit_activity_trail(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_audit_tables()

    with get_engine().begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM (
                    SELECT
                        'audit_plan_created' AS event_type,
                        id AS entity_id,
                        audit_number AS reference,
                        status AS description,
                        created_by,
                        created_at
                    FROM audit_plans
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'internal_control_registered',
                        id,
                        control_number,
                        status,
                        created_by,
                        created_at
                    FROM audit_controls
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'control_test_recorded',
                        id,
                        CAST(control_id AS VARCHAR),
                        result,
                        created_by,
                        created_at
                    FROM audit_control_tests
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'audit_finding_created',
                        id,
                        finding_number,
                        status,
                        created_by,
                        created_at
                    FROM audit_findings
                    WHERE tenant_id = :tenant_id
                ) AS events
                ORDER BY created_at DESC
                LIMIT 500
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# ============================================================
# INITIALIZE AUDIT DATABASE SCHEMA
# ============================================================

init_audit_tables()

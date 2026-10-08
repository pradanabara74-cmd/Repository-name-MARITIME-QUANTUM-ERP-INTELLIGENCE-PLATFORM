# ============================================================
# MARITIME QUANTUM ERP & INTELLIGENCE PLATFORM
# BUSINESS INTELLIGENCE & ADVANCED ANALYTICS
# BLOK 1 - CORE MODELS AND DATABASE
# ============================================================

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4
import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..core.database import get_engine
from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/bi",
    tags=["Business Intelligence & Advanced Analytics"],
)


# ============================================================
# DATA MODELS
# ============================================================

class KPIRecordCreate(BaseModel):
    department: str = Field(min_length=1, max_length=100)
    kpi_name: str = Field(min_length=1, max_length=200)
    reporting_date: date
    actual_value: Decimal
    target_value: Optional[Decimal] = None
    unit: str = Field(min_length=1, max_length=50)
    vessel_id: Optional[str] = Field(
        default=None, max_length=100
    )
    source_module: Optional[str] = Field(
        default=None, max_length=100
    )
    remarks: Optional[str] = None


class KPIRecordUpdate(BaseModel):
    actual_value: Decimal
    target_value: Optional[Decimal] = None
    remarks: Optional[str] = None


class AnalyticsReportCreate(BaseModel):
    report_name: str = Field(min_length=1, max_length=200)
    department: str = Field(min_length=1, max_length=100)
    report_type: str = Field(min_length=1, max_length=100)
    description: Optional[str] = None
    parameters: dict = Field(default_factory=dict)


class BIAlertRuleCreate(BaseModel):
    rule_name: str = Field(min_length=1, max_length=200)
    department: str = Field(min_length=1, max_length=100)
    kpi_name: str = Field(min_length=1, max_length=200)
    operator: str = Field(
        pattern=r"^(gt|gte|lt|lte|eq)$"
    )
    threshold_value: Decimal
    severity: str = Field(
        default="medium",
        pattern=r"^(low|medium|high|critical)$",
    )


# ============================================================
# SERIALIZATION
# ============================================================

def serialize_bi_row(row):
    if row is None:
        return None

    result = dict(row)

    for key, value in result.items():
        if isinstance(value, UUID):
            result[key] = str(value)
        elif isinstance(value, Decimal):
            result[key] = float(value)
        elif isinstance(value, (date, datetime)):
            result[key] = value.isoformat()

    return result


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_bi_tables():
    engine = get_engine()

    with engine.begin() as conn:

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bi_kpi_records (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(100) NOT NULL,
                department VARCHAR(100) NOT NULL,
                kpi_name VARCHAR(200) NOT NULL,
                reporting_date DATE NOT NULL,
                actual_value NUMERIC(20,6) NOT NULL,
                target_value NUMERIC(20,6),
                unit VARCHAR(50) NOT NULL,
                vessel_id VARCHAR(100),
                source_module VARCHAR(100),
                remarks TEXT,
                created_by VARCHAR(100) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bi_reports (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(100) NOT NULL,
                report_name VARCHAR(200) NOT NULL,
                department VARCHAR(100) NOT NULL,
                report_type VARCHAR(100) NOT NULL,
                description TEXT,
                parameters_json TEXT NOT NULL DEFAULT '{}',
                created_by VARCHAR(100) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bi_alert_rules (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(100) NOT NULL,
                rule_name VARCHAR(200) NOT NULL,
                department VARCHAR(100) NOT NULL,
                kpi_name VARCHAR(200) NOT NULL,
                operator VARCHAR(10) NOT NULL,
                threshold_value NUMERIC(20,6) NOT NULL,
                severity VARCHAR(30) NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_by VARCHAR(100) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bi_audit_log (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(100) NOT NULL,
                entity_type VARCHAR(100) NOT NULL,
                entity_id UUID NOT NULL,
                action VARCHAR(100) NOT NULL,
                details TEXT,
                performed_by VARCHAR(100) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_bi_kpi_tenant
            ON bi_kpi_records (
                tenant_id, department, reporting_date
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_bi_kpi_vessel
            ON bi_kpi_records (
                tenant_id, vessel_id
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_bi_reports_tenant
            ON bi_reports (
                tenant_id, department
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_bi_alert_rules_tenant
            ON bi_alert_rules (
                tenant_id, department, is_active
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_bi_audit_tenant
            ON bi_audit_log (
                tenant_id, created_at
            )
        """))


# ============================================================
# AUDIT HELPER
# ============================================================

def record_bi_audit(
    conn,
    identity,
    entity_type,
    entity_id,
    action,
    details=None,
):
    conn.execute(
        text("""
            INSERT INTO bi_audit_log (
                id, tenant_id, entity_type, entity_id,
                action, details, performed_by, created_at
            )
            VALUES (
                :id, :tenant_id, :entity_type, :entity_id,
                :action, :details, :performed_by, :created_at
            )
        """),
        {
            "id": uuid4(),
            "tenant_id": identity.tenant_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "details": details,
            "performed_by": identity.user_id,
            "created_at": datetime.now(timezone.utc),
        },
    )

# ============================================================
# BI BLOK 2 - KPI MANAGEMENT & PERFORMANCE INTELLIGENCE
# ============================================================


# ============================================================
# CREATE KPI RECORD
# ============================================================

@router.post(
    "/kpis",
    status_code=status.HTTP_201_CREATED,
    summary="Create Business Intelligence KPI",
)
def create_bi_kpi(
    payload: KPIRecordCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()
    record_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO bi_kpi_records (
                    id, tenant_id, department, kpi_name,
                    reporting_date, actual_value, target_value,
                    unit, vessel_id, source_module, remarks,
                    created_by, created_at, updated_at
                )
                VALUES (
                    :id, :tenant_id, :department, :kpi_name,
                    :reporting_date, :actual_value, :target_value,
                    :unit, :vessel_id, :source_module, :remarks,
                    :created_by, :created_at, :updated_at
                )
            """),
            {
                "id": record_id,
                "tenant_id": identity.tenant_id,
                "department": payload.department,
                "kpi_name": payload.kpi_name,
                "reporting_date": payload.reporting_date,
                "actual_value": payload.actual_value,
                "target_value": payload.target_value,
                "unit": payload.unit,
                "vessel_id": payload.vessel_id,
                "source_module": payload.source_module,
                "remarks": payload.remarks,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

        record_bi_audit(
            conn,
            identity,
            "kpi",
            record_id,
            "created",
            f"KPI created: {payload.kpi_name}",
        )

    return {
        "id": str(record_id),
        "department": payload.department,
        "kpi_name": payload.kpi_name,
        "actual_value": float(payload.actual_value),
        "target_value": (
            float(payload.target_value)
            if payload.target_value is not None
            else None
        ),
        "status": "created",
    }


# ============================================================
# LIST KPI RECORDS
# ============================================================

@router.get(
    "/kpis",
    summary="List Business Intelligence KPIs",
)
def list_bi_kpis(
    department: Optional[str] = None,
    vessel_id: Optional[str] = None,
    kpi_name: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=1000),
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM bi_kpi_records
                WHERE tenant_id = :tenant_id
                  AND (
                      :department IS NULL
                      OR department = :department
                  )
                  AND (
                      :vessel_id IS NULL
                      OR vessel_id = :vessel_id
                  )
                  AND (
                      :kpi_name IS NULL
                      OR kpi_name = :kpi_name
                  )
                ORDER BY reporting_date DESC, created_at DESC
                LIMIT :limit
            """),
            {
                "tenant_id": identity.tenant_id,
                "department": department,
                "vessel_id": vessel_id,
                "kpi_name": kpi_name,
                "limit": limit,
            },
        ).mappings().all()

    return {
        "count": len(rows),
        "records": [
            serialize_bi_row(row)
            for row in rows
        ],
    }


# ============================================================
# GET KPI DETAILS
# ============================================================

@router.get(
    "/kpis/{kpi_id}",
    summary="Get KPI Details",
)
def get_bi_kpi(
    kpi_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text("""
                SELECT *
                FROM bi_kpi_records
                WHERE id = :kpi_id
                  AND tenant_id = :tenant_id
            """),
            {
                "kpi_id": kpi_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="KPI record not found.",
        )

    return serialize_bi_row(row)


# ============================================================
# UPDATE KPI RECORD
# ============================================================

@router.patch(
    "/kpis/{kpi_id}",
    summary="Update KPI Actual and Target Values",
)
def update_bi_kpi(
    kpi_id: UUID,
    payload: KPIRecordUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        existing = conn.execute(
            text("""
                SELECT id, actual_value, target_value
                FROM bi_kpi_records
                WHERE id = :kpi_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "kpi_id": kpi_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="KPI record not found.",
            )

        conn.execute(
            text("""
                UPDATE bi_kpi_records
                SET actual_value = :actual_value,
                    target_value = :target_value,
                    remarks = COALESCE(:remarks, remarks),
                    updated_at = :updated_at
                WHERE id = :kpi_id
                  AND tenant_id = :tenant_id
            """),
            {
                "actual_value": payload.actual_value,
                "target_value": payload.target_value,
                "remarks": payload.remarks,
                "updated_at": now,
                "kpi_id": kpi_id,
                "tenant_id": identity.tenant_id,
            },
        )

        record_bi_audit(
            conn,
            identity,
            "kpi",
            kpi_id,
            "updated",
            json.dumps({
                "previous_actual": str(
                    existing["actual_value"]
                ),
                "new_actual": str(payload.actual_value),
                "previous_target": (
                    str(existing["target_value"])
                    if existing["target_value"] is not None
                    else None
                ),
                "new_target": (
                    str(payload.target_value)
                    if payload.target_value is not None
                    else None
                ),
            }),
        )

    return {
        "id": str(kpi_id),
        "actual_value": float(payload.actual_value),
        "target_value": (
            float(payload.target_value)
            if payload.target_value is not None
            else None
        ),
        "updated_at": now.isoformat(),
    }


# ============================================================
# KPI PERFORMANCE INTELLIGENCE
# ============================================================

@router.get(
    "/intelligence/performance",
    summary="Department KPI Performance Intelligence",
)
def bi_performance_intelligence(
    department: Optional[str] = None,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    department,
                    kpi_name,
                    unit,
                    COUNT(*) AS record_count,
                    MIN(reporting_date) AS first_report,
                    MAX(reporting_date) AS latest_report,
                    AVG(actual_value) AS average_actual,
                    MIN(actual_value) AS minimum_actual,
                    MAX(actual_value) AS maximum_actual,
                    AVG(target_value) AS average_target
                FROM bi_kpi_records
                WHERE tenant_id = :tenant_id
                  AND (
                      :department IS NULL
                      OR department = :department
                  )
                GROUP BY department, kpi_name, unit
                ORDER BY department, kpi_name, unit
            """),
            {
                "tenant_id": identity.tenant_id,
                "department": department,
            },
        ).mappings().all()

    results = []

    for row in rows:
        data = serialize_bi_row(row)

        average_actual = row["average_actual"]
        average_target = row["average_target"]

        data["average_variance"] = (
            round(
                float(average_actual - average_target),
                6,
            )
            if average_target is not None
            else None
        )

        data["performance_direction"] = "not_configured"

        results.append(data)

    return {
        "module": "BI Performance Intelligence",
        "metric_count": len(results),
        "metrics": results,
        "note": (
            "Statistics are based on recorded KPI values. "
            "Positive variance is not necessarily better; "
            "performance direction must be defined per KPI."
        ),
    }


# ============================================================
# KPI TIME SERIES
# ============================================================

@router.get(
    "/intelligence/trends",
    summary="KPI Historical Trend Analytics",
)
def bi_kpi_trends(
    department: str,
    kpi_name: str,
    vessel_id: Optional[str] = None,
    limit: int = Query(default=365, ge=1, le=1000),
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    reporting_date,
                    actual_value,
                    target_value,
                    unit,
                    vessel_id
                FROM bi_kpi_records
                WHERE tenant_id = :tenant_id
                  AND department = :department
                  AND kpi_name = :kpi_name
                  AND (
                      :vessel_id IS NULL
                      OR vessel_id = :vessel_id
                  )
                ORDER BY reporting_date DESC, created_at DESC
                LIMIT :limit
            """),
            {
                "tenant_id": identity.tenant_id,
                "department": department,
                "kpi_name": kpi_name,
                "vessel_id": vessel_id,
                "limit": limit,
            },
        ).mappings().all()

    points = [
        serialize_bi_row(row)
        for row in reversed(rows)
    ]

    return {
        "department": department,
        "kpi_name": kpi_name,
        "vessel_id": vessel_id,
        "point_count": len(points),
        "time_series": points,
        "note": (
            "Results are individual records, not "
            "daily aggregated observations."
        ),
    }

# ============================================================
# BI BLOK 3 - ANALYTICS REPORTS & EXECUTIVE DASHBOARD
# ============================================================


# ============================================================
# CREATE ANALYTICS REPORT DEFINITION
# ============================================================

@router.post(
    "/reports",
    status_code=status.HTTP_201_CREATED,
    summary="Create Analytics Report Definition",
)
def create_bi_report(
    payload: AnalyticsReportCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()
    report_id = uuid4()
    now = datetime.now(timezone.utc)

    parameters_json = json.dumps(
        payload.parameters,
        allow_nan=False,
    )

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO bi_reports (
                    id,
                    tenant_id,
                    report_name,
                    department,
                    report_type,
                    description,
                    parameters_json,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :report_name,
                    :department,
                    :report_type,
                    :description,
                    :parameters_json,
                    :created_by,
                    :created_at
                )
            """),
            {
                "id": report_id,
                "tenant_id": identity.tenant_id,
                "report_name": payload.report_name,
                "department": payload.department,
                "report_type": payload.report_type,
                "description": payload.description,
                "parameters_json": parameters_json,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

        record_bi_audit(
            conn=conn,
            identity=identity,
            entity_type="analytics_report",
            entity_id=report_id,
            action="created",
            details=payload.report_name,
        )

    return {
        "id": str(report_id),
        "report_name": payload.report_name,
        "department": payload.department,
        "report_type": payload.report_type,
        "status": "created",
        "note": "Report definition saved.",
    }


# ============================================================
# LIST ANALYTICS REPORT DEFINITIONS
# ============================================================

@router.get(
    "/reports",
    summary="List Analytics Report Definitions",
)
def list_bi_reports(
    department: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM bi_reports
                WHERE tenant_id = :tenant_id
                  AND (
                      :department IS NULL
                      OR department = :department
                  )
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {
                "tenant_id": identity.tenant_id,
                "department": department,
                "limit": limit,
            },
        ).mappings().all()

    results = []

    for row in rows:
        item = serialize_bi_row(row)

        try:
            item["parameters"] = json.loads(
                item.pop("parameters_json")
            )
        except (TypeError, ValueError):
            item["parameters"] = {}
            item.pop("parameters_json", None)

        results.append(item)

    return {
        "count": len(results),
        "reports": results,
    }


# ============================================================
# EXECUTIVE BI DASHBOARD
# ============================================================

@router.get(
    "/dashboard",
    summary="Executive Business Intelligence Dashboard",
)
def bi_executive_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()
    params = {"tenant_id": identity.tenant_id}

    with engine.begin() as conn:

        kpi_summary = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_kpi_records,
                    COUNT(DISTINCT department)
                        AS department_count,
                    COUNT(DISTINCT kpi_name)
                        AS distinct_kpi_names,
                    COUNT(DISTINCT vessel_id)
                        AS vessels_with_kpis,
                    MAX(reporting_date)
                        AS latest_reporting_date
                FROM bi_kpi_records
                WHERE tenant_id = :tenant_id
            """),
            params,
        ).mappings().one()

        report_summary = conn.execute(
            text("""
                SELECT COUNT(*) AS total_reports
                FROM bi_reports
                WHERE tenant_id = :tenant_id
            """),
            params,
        ).mappings().one()

        alert_summary = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_alert_rules,
                    COUNT(*) FILTER (
                        WHERE is_active = TRUE
                    ) AS active_alert_rules
                FROM bi_alert_rules
                WHERE tenant_id = :tenant_id
            """),
            params,
        ).mappings().one()

        department_rows = conn.execute(
            text("""
                SELECT
                    department,
                    COUNT(*) AS kpi_record_count,
                    COUNT(DISTINCT kpi_name)
                        AS distinct_kpi_names,
                    MAX(reporting_date)
                        AS latest_reporting_date
                FROM bi_kpi_records
                WHERE tenant_id = :tenant_id
                GROUP BY department
                ORDER BY department
            """),
            params,
        ).mappings().all()

    return {
        "platform": "MQEIP",
        "module": "Executive Business Intelligence",
        "kpi_summary": serialize_bi_row(kpi_summary),
        "report_summary": serialize_bi_row(report_summary),
        "alert_summary": serialize_bi_row(alert_summary),
        "department_coverage": [
            serialize_bi_row(row)
            for row in department_rows
        ],
        "generated_at":
            datetime.now(timezone.utc).isoformat(),
        "data_scope": "Tenant BI records",
        "note": (
            "Dashboard currently summarizes BI records. "
            "Direct cross-module ERP aggregation "
            "requires validated source mappings."
        ),
    }


# ============================================================
# DEPARTMENT INTELLIGENCE
# ============================================================

@router.get(
    "/intelligence/departments",
    summary="Department Analytics Intelligence",
)
def bi_department_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    department,
                    COUNT(*) AS total_kpi_records,
                    COUNT(DISTINCT kpi_name)
                        AS distinct_kpi_names,
                    COUNT(DISTINCT vessel_id)
                        AS vessel_count,
                    MIN(reporting_date)
                        AS first_reporting_date,
                    MAX(reporting_date)
                        AS latest_reporting_date
                FROM bi_kpi_records
                WHERE tenant_id = :tenant_id
                GROUP BY department
                ORDER BY total_kpi_records DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "module": "Department Intelligence",
        "department_count": len(rows),
        "departments": [
            serialize_bi_row(row)
            for row in rows
        ],
    }


# ============================================================
# FLEET KPI COVERAGE INTELLIGENCE
# ============================================================

@router.get(
    "/intelligence/fleet",
    summary="Fleet KPI Coverage Intelligence",
)
def bi_fleet_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    vessel_id,
                    COUNT(*) AS kpi_record_count,
                    COUNT(DISTINCT department)
                        AS department_count,
                    COUNT(DISTINCT kpi_name)
                        AS distinct_kpi_names,
                    MIN(reporting_date)
                        AS first_reporting_date,
                    MAX(reporting_date)
                        AS latest_reporting_date
                FROM bi_kpi_records
                WHERE tenant_id = :tenant_id
                  AND vessel_id IS NOT NULL
                GROUP BY vessel_id
                ORDER BY kpi_record_count DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "module": "Fleet KPI Intelligence",
        "vessel_count": len(rows),
        "vessels": [
            serialize_bi_row(row)
            for row in rows
        ],
        "note": (
            "Only vessels with recorded BI KPIs "
            "are included in this response."
        ),
    }


# ============================================================
# KPI LATEST SNAPSHOT
# ============================================================

@router.get(
    "/intelligence/latest-kpis",
    summary="Latest KPI Snapshot by Department",
)
def bi_latest_kpi_snapshot(
    department: Optional[str] = None,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT DISTINCT ON (
                    department,
                    kpi_name,
                    unit,
                    COALESCE(vessel_id, '')
                )
                    id,
                    department,
                    kpi_name,
                    unit,
                    vessel_id,
                    actual_value,
                    target_value,
                    reporting_date,
                    source_module,
                    updated_at
                FROM bi_kpi_records
                WHERE tenant_id = :tenant_id
                  AND (
                      :department IS NULL
                      OR department = :department
                  )
                ORDER BY
                    department,
                    kpi_name,
                    unit,
                    COALESCE(vessel_id, ''),
                    reporting_date DESC,
                    updated_at DESC,
                    id DESC
            """),
            {
                "tenant_id": identity.tenant_id,
                "department": department,
            },
        ).mappings().all()

    return {
        "module": "Latest KPI Snapshot",
        "record_count": len(rows),
        "records": [
            serialize_bi_row(row)
            for row in rows
        ],
    }

# ============================================================
# BI BLOK 4 - INTELLIGENT ALERTS & PERFORMANCE MONITORING
# ============================================================


# ============================================================
# CREATE BI ALERT RULE
# ============================================================

@router.post(
    "/alerts/rules",
    status_code=status.HTTP_201_CREATED,
    summary="Create BI Performance Alert Rule",
)
def create_bi_alert_rule(
    payload: BIAlertRuleCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()
    rule_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO bi_alert_rules (
                    id, tenant_id, rule_name, department,
                    kpi_name, operator, threshold_value,
                    severity, is_active, created_by, created_at
                )
                VALUES (
                    :id, :tenant_id, :rule_name, :department,
                    :kpi_name, :operator, :threshold_value,
                    :severity, TRUE, :created_by, :created_at
                )
            """),
            {
                "id": rule_id,
                "tenant_id": identity.tenant_id,
                "rule_name": payload.rule_name,
                "department": payload.department,
                "kpi_name": payload.kpi_name,
                "operator": payload.operator,
                "threshold_value": payload.threshold_value,
                "severity": payload.severity,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

        record_bi_audit(
            conn,
            identity,
            "alert_rule",
            rule_id,
            "created",
            f"Alert rule created: {payload.rule_name}",
        )

    return {
        "id": str(rule_id),
        "rule_name": payload.rule_name,
        "severity": payload.severity,
        "is_active": True,
        "status": "created",
    }


# ============================================================
# LIST BI ALERT RULES
# ============================================================

@router.get(
    "/alerts/rules",
    summary="List BI Performance Alert Rules",
)
def list_bi_alert_rules(
    department: Optional[str] = None,
    active_only: bool = True,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM bi_alert_rules
                WHERE tenant_id = :tenant_id
                  AND (
                      :department IS NULL
                      OR department = :department
                  )
                  AND (
                      :active_only = FALSE
                      OR is_active = TRUE
                  )
                ORDER BY created_at DESC
                LIMIT 1000
            """),
            {
                "tenant_id": identity.tenant_id,
                "department": department,
                "active_only": active_only,
            },
        ).mappings().all()

    return {
        "count": len(rows),
        "rules": [
            serialize_bi_row(row)
            for row in rows
        ],
    }


# ============================================================
# ACTIVATE OR DEACTIVATE ALERT RULE
# ============================================================

@router.patch(
    "/alerts/rules/{rule_id}/status",
    summary="Activate or Deactivate BI Alert Rule",
)
def update_bi_alert_rule_status(
    rule_id: UUID,
    is_active: bool,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        existing = conn.execute(
            text("""
                SELECT id, is_active
                FROM bi_alert_rules
                WHERE id = :rule_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "rule_id": rule_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="BI alert rule not found.",
            )

        conn.execute(
            text("""
                UPDATE bi_alert_rules
                SET is_active = :is_active
                WHERE id = :rule_id
                  AND tenant_id = :tenant_id
            """),
            {
                "is_active": is_active,
                "rule_id": rule_id,
                "tenant_id": identity.tenant_id,
            },
        )

        record_bi_audit(
            conn,
            identity,
            "alert_rule",
            rule_id,
            "activated" if is_active else "deactivated",
            json.dumps({
                "previous_status": existing["is_active"],
                "new_status": is_active,
            }),
        )

    return {
        "id": str(rule_id),
        "is_active": is_active,
        "status": "updated",
    }


# ============================================================
# ALERT EVALUATION HELPER
# ============================================================

def evaluate_bi_threshold(actual, operator, threshold):
    actual = Decimal(str(actual))
    threshold = Decimal(str(threshold))

    comparisons = {
        "gt": actual > threshold,
        "gte": actual >= threshold,
        "lt": actual < threshold,
        "lte": actual <= threshold,
        "eq": actual == threshold,
    }

    return comparisons.get(operator, False)


# ============================================================
# EVALUATE ACTIVE BI ALERTS
# ============================================================

@router.get(
    "/alerts/evaluate",
    summary="Evaluate BI Performance Alerts",
)
def evaluate_bi_alerts(
    department: Optional[str] = None,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    r.id AS rule_id,
                    r.rule_name,
                    r.department,
                    r.kpi_name,
                    r.operator,
                    r.threshold_value,
                    r.severity,
                    k.id AS kpi_id,
                    k.vessel_id,
                    k.actual_value,
                    k.unit,
                    k.reporting_date
                FROM bi_alert_rules r
                JOIN LATERAL (
                    SELECT DISTINCT ON (
                        COALESCE(vessel_id, '')
                    )
                        id,
                        vessel_id,
                        actual_value,
                        unit,
                        reporting_date
                    FROM bi_kpi_records
                    WHERE tenant_id = r.tenant_id
                      AND department = r.department
                      AND kpi_name = r.kpi_name
                    ORDER BY
                        COALESCE(vessel_id, ''),
                        reporting_date DESC,
                        updated_at DESC,
                        id DESC
                ) k ON TRUE
                WHERE r.tenant_id = :tenant_id
                  AND r.is_active = TRUE
                  AND (
                      :department IS NULL
                      OR r.department = :department
                  )
                ORDER BY r.severity, r.rule_name
                LIMIT 5000
            """),
            {
                "tenant_id": identity.tenant_id,
                "department": department,
            },
        ).mappings().all()

    triggered = []
    evaluated_count = 0

    for row in rows:
        evaluated_count += 1

        matched = evaluate_bi_threshold(
            row["actual_value"],
            row["operator"],
            row["threshold_value"],
        )

        if matched:
            triggered.append({
                "rule_id": str(row["rule_id"]),
                "rule_name": row["rule_name"],
                "department": row["department"],
                "kpi_name": row["kpi_name"],
                "severity": row["severity"],
                "vessel_id": row["vessel_id"],
                "actual_value": float(
                    row["actual_value"]
                ),
                "operator": row["operator"],
                "threshold_value": float(
                    row["threshold_value"]
                ),
                "unit": row["unit"],
                "reporting_date":
                    row["reporting_date"].isoformat(),
                "status": "triggered",
            })

    severity_order = {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
    }

    triggered.sort(
        key=lambda item: (
            severity_order.get(item["severity"], 4),
            item["department"],
            item["rule_name"],
        )
    )

    return {
        "module": "BI Intelligent Alert Evaluation",
        "evaluated_record_count": evaluated_count,
        "triggered_alert_count": len(triggered),
        "alerts": triggered,
        "evaluation_time":
            datetime.now(timezone.utc).isoformat(),
        "note": (
            "Alerts are evaluated on request using "
            "the latest KPI record per vessel. "
            "Automatic background notifications "
            "are not enabled in this endpoint."
        ),
    }


# ============================================================
# BI PERFORMANCE MONITORING SUMMARY
# ============================================================

@router.get(
    "/monitoring/summary",
    summary="Business Intelligence Monitoring Summary",
)
def bi_monitoring_summary(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()
    params = {"tenant_id": identity.tenant_id}

    with engine.begin() as conn:
        kpis = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_records,
                    COUNT(*) FILTER (
                        WHERE reporting_date >=
                            CURRENT_DATE - INTERVAL '30 days'
                    ) AS records_last_30_days,
                    COUNT(DISTINCT department)
                        AS monitored_departments
                FROM bi_kpi_records
                WHERE tenant_id = :tenant_id
            """),
            params,
        ).mappings().one()

        rules = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_rules,
                    COUNT(*) FILTER (
                        WHERE is_active = TRUE
                    ) AS active_rules,
                    COUNT(*) FILTER (
                        WHERE severity = 'critical'
                          AND is_active = TRUE
                    ) AS active_critical_rules
                FROM bi_alert_rules
                WHERE tenant_id = :tenant_id
            """),
            params,
        ).mappings().one()

    return {
        "module": "BI Performance Monitoring",
        "kpi_monitoring": serialize_bi_row(kpis),
        "alert_configuration": serialize_bi_row(rules),
        "generated_at":
            datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# BI AUDIT TRAIL
# ============================================================

@router.get(
    "/audit-trail",
    summary="Business Intelligence Audit Trail",
)
def get_bi_audit_trail(
    entity_type: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=1000),
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM bi_audit_log
                WHERE tenant_id = :tenant_id
                  AND (
                      :entity_type IS NULL
                      OR entity_type = :entity_type
                  )
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {
                "tenant_id": identity.tenant_id,
                "entity_type": entity_type,
                "limit": limit,
            },
        ).mappings().all()

    return {
        "record_count": len(rows),
        "records": [
            serialize_bi_row(row)
            for row in rows
        ],
    }

# ============================================================
# BI BLOK 5 - DATA QUALITY & FINAL INTEGRATION
# ============================================================


# ============================================================
# BI DATA QUALITY INTELLIGENCE
# ============================================================

@router.get(
    "/data-quality",
    summary="Business Intelligence Data Quality",
)
def bi_data_quality(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        quality = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_kpi_records,

                    COUNT(*) FILTER (
                        WHERE target_value IS NULL
                    ) AS missing_targets,

                    COUNT(*) FILTER (
                        WHERE vessel_id IS NULL
                    ) AS records_without_vessel,

                    COUNT(*) FILTER (
                        WHERE source_module IS NULL
                    ) AS missing_source_module,

                    COUNT(*) FILTER (
                        WHERE reporting_date > CURRENT_DATE
                    ) AS future_dated_records,

                    COUNT(*) FILTER (
                        WHERE reporting_date <
                            CURRENT_DATE - INTERVAL '365 days'
                    ) AS records_older_than_one_year,

                    COUNT(*) FILTER (
                        WHERE unit IS NULL
                           OR TRIM(unit) = ''
                    ) AS missing_units

                FROM bi_kpi_records
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().one()

    return {
        "module": "BI Data Quality Intelligence",
        "quality_metrics": serialize_bi_row(quality),
        "generated_at":
            datetime.now(timezone.utc).isoformat(),
        "note": (
            "Missing vessel references may be valid "
            "for company-level KPIs. "
            "Quality indicators require contextual review."
        ),
    }


# ============================================================
# BI DATA FRESHNESS INTELLIGENCE
# ============================================================

@router.get(
    "/intelligence/data-freshness",
    summary="KPI Data Freshness Intelligence",
)
def bi_data_freshness(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    department,
                    kpi_name,
                    MAX(reporting_date)
                        AS latest_reporting_date,
                    CURRENT_DATE - MAX(reporting_date)
                        AS days_since_latest_record,
                    COUNT(*) AS total_records
                FROM bi_kpi_records
                WHERE tenant_id = :tenant_id
                GROUP BY department, kpi_name
                ORDER BY
                    latest_reporting_date ASC,
                    department,
                    kpi_name
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    results = []

    for row in rows:
        data = serialize_bi_row(row)

        days = int(data["days_since_latest_record"])

        if days < 0:
            freshness = "future_dated"
        elif days <= 7:
            freshness = "recent"
        elif days <= 30:
            freshness = "aging"
        else:
            freshness = "stale"

        data["freshness_status"] = freshness
        results.append(data)

    return {
        "module": "BI Data Freshness Intelligence",
        "metric_count": len(results),
        "metrics": results,
        "note": (
            "Freshness categories are generic. "
            "Different KPIs may require different "
            "reporting frequencies."
        ),
    }


# ============================================================
# BI SYSTEM STATUS
# ============================================================

@router.get(
    "/system/status",
    summary="Business Intelligence System Status",
)
def bi_system_status(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    return {
        "platform": "MQEIP",
        "module": (
            "Business Intelligence & Advanced Analytics"
        ),
        "database": "connected",
        "authentication": "required",
        "tenant_id": str(identity.tenant_id),
        "status": "operational",
        "timestamp":
            datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# INITIALIZE BUSINESS INTELLIGENCE DATABASE
# ============================================================

init_bi_tables()

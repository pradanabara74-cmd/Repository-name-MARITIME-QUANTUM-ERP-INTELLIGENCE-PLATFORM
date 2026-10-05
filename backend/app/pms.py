from datetime import date, datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from ..core.database import get_database_url
from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/pms",
    tags=["PMS / Planned Maintenance"],
)


def _engine():
    return create_engine(get_database_url(), pool_pre_ping=True)


def _init_tables() -> None:
    with _engine().begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS pms_equipment (
                    id VARCHAR(36) PRIMARY KEY,
                    tenant_id VARCHAR(120) NOT NULL,
                    vessel_id VARCHAR(120),
                    vessel_name VARCHAR(160),
                    system_name VARCHAR(160) NOT NULL,
                    equipment_name VARCHAR(200) NOT NULL,
                    component_name VARCHAR(200),
                    maker VARCHAR(160),
                    model VARCHAR(160),
                    serial_number VARCHAR(160),
                    criticality VARCHAR(40) NOT NULL DEFAULT 'NORMAL',
                    running_hours DOUBLE PRECISION NOT NULL DEFAULT 0,
                    status VARCHAR(40) NOT NULL DEFAULT 'active',
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS pms_jobs (
                    id VARCHAR(36) PRIMARY KEY,
                    tenant_id VARCHAR(120) NOT NULL,
                    equipment_id VARCHAR(36) NOT NULL,
                    job_title VARCHAR(200) NOT NULL,
                    job_description TEXT,
                    interval_hours DOUBLE PRECISION,
                    interval_days INTEGER,
                    last_done_date DATE,
                    last_done_hours DOUBLE PRECISION,
                    next_due_date DATE,
                    next_due_hours DOUBLE PRECISION,
                    responsible_person VARCHAR(160),
                    work_order_number VARCHAR(120),
                    spare_parts_used TEXT,
                    defect_reference VARCHAR(160),
                    job_status VARCHAR(40) NOT NULL DEFAULT 'OPEN',
                    completed_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )


class EquipmentCreate(BaseModel):
    vessel_id: Optional[str] = Field(default=None, max_length=120)
    vessel_name: Optional[str] = Field(default=None, max_length=160)
    system_name: str = Field(..., min_length=2, max_length=160)
    equipment_name: str = Field(..., min_length=2, max_length=200)
    component_name: Optional[str] = Field(default=None, max_length=200)
    maker: Optional[str] = Field(default=None, max_length=160)
    model: Optional[str] = Field(default=None, max_length=160)
    serial_number: Optional[str] = Field(default=None, max_length=160)
    criticality: str = Field(default="NORMAL", max_length=40)
    running_hours: float = Field(default=0, ge=0)
    status: str = Field(default="active", max_length=40)


class JobCreate(BaseModel):
    job_title: str = Field(..., min_length=2, max_length=200)
    job_description: Optional[str] = None
    interval_hours: Optional[float] = Field(default=None, ge=0)
    interval_days: Optional[int] = Field(default=None, ge=0)
    last_done_date: Optional[date] = None
    last_done_hours: Optional[float] = Field(default=None, ge=0)
    next_due_date: Optional[date] = None
    next_due_hours: Optional[float] = Field(default=None, ge=0)
    responsible_person: Optional[str] = Field(default=None, max_length=160)
    work_order_number: Optional[str] = Field(default=None, max_length=120)
    spare_parts_used: Optional[str] = None
    defect_reference: Optional[str] = Field(default=None, max_length=160)


def maintenance_status(
    next_due_date: Optional[date],
    next_due_hours: Optional[float],
    running_hours: float,
    criticality: str,
) -> str:
    overdue = False
    due_soon = False

    if next_due_date is not None:
        days_left = (next_due_date - date.today()).days
        if days_left < 0:
            overdue = True
        elif days_left <= 30:
            due_soon = True

    if next_due_hours is not None:
        hours_left = next_due_hours - running_hours
        if hours_left < 0:
            overdue = True
        elif hours_left <= 100:
            due_soon = True

    if overdue and criticality.upper() == "CRITICAL":
        return "CRITICAL"
    if overdue:
        return "OVERDUE"
    if due_soon:
        return "DUE SOON"

    return "NORMAL"


@router.get("/equipment")
def list_equipment(
    identity: TokenIdentity = Depends(verify_access_token),
):
    _init_tables()

    with _engine().begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM pms_equipment
                WHERE tenant_id = :tenant_id
                ORDER BY vessel_name, system_name, equipment_name
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [dict(row) for row in rows]


@router.post("/equipment", status_code=status.HTTP_201_CREATED)
def create_equipment(
    payload: EquipmentCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    _init_tables()

    equipment_id = str(uuid4())
    created_at = datetime.now(timezone.utc)

    values = {
        "id": equipment_id,
        "tenant_id": identity.tenant_id,
        **payload.model_dump(),
        "created_at": created_at,
    }

    with _engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO pms_equipment (
                    id, tenant_id, vessel_id, vessel_name,
                    system_name, equipment_name, component_name,
                    maker, model, serial_number, criticality,
                    running_hours, status, created_at
                )
                VALUES (
                    :id, :tenant_id, :vessel_id, :vessel_name,
                    :system_name, :equipment_name, :component_name,
                    :maker, :model, :serial_number, :criticality,
                    :running_hours, :status, :created_at
                )
                """
            ),
            values,
        )

    return values


@router.post(
    "/equipment/{equipment_id}/jobs",
    status_code=status.HTTP_201_CREATED,
)
def create_maintenance_job(
    equipment_id: str,
    payload: JobCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    _init_tables()

    with _engine().begin() as conn:
        equipment = conn.execute(
            text(
                """
                SELECT id, running_hours, criticality
                FROM pms_equipment
                WHERE id = :equipment_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "equipment_id": equipment_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if equipment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="PMS equipment not found.",
            )

        job_id = str(uuid4())
        created_at = datetime.now(timezone.utc)

        values = {
            "id": job_id,
            "tenant_id": identity.tenant_id,
            "equipment_id": equipment_id,
            **payload.model_dump(),
            "job_status": "OPEN",
            "created_at": created_at,
        }

        conn.execute(
            text(
                """
                INSERT INTO pms_jobs (
                    id, tenant_id, equipment_id,
                    job_title, job_description,
                    interval_hours, interval_days,
                    last_done_date, last_done_hours,
                    next_due_date, next_due_hours,
                    responsible_person, work_order_number,
                    spare_parts_used, defect_reference,
                    job_status, created_at
                )
                VALUES (
                    :id, :tenant_id, :equipment_id,
                    :job_title, :job_description,
                    :interval_hours, :interval_days,
                    :last_done_date, :last_done_hours,
                    :next_due_date, :next_due_hours,
                    :responsible_person, :work_order_number,
                    :spare_parts_used, :defect_reference,
                    :job_status, :created_at
                )
                """
            ),
            values,
        )

    values["maintenance_status"] = maintenance_status(
        payload.next_due_date,
        payload.next_due_hours,
        float(equipment["running_hours"]),
        str(equipment["criticality"]),
    )

    return values


@router.get("/jobs")
def list_maintenance_jobs(
    identity: TokenIdentity = Depends(verify_access_token),
):
    _init_tables()

    with _engine().begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    j.*,
                    e.vessel_id,
                    e.vessel_name,
                    e.system_name,
                    e.equipment_name,
                    e.component_name,
                    e.running_hours,
                    e.criticality
                FROM pms_jobs j
                JOIN pms_equipment e
                  ON e.id = j.equipment_id
                 AND e.tenant_id = j.tenant_id
                WHERE j.tenant_id = :tenant_id
                ORDER BY j.next_due_date NULLS LAST,
                         e.vessel_name,
                         e.equipment_name
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    result = []

    for row in rows:
        item = dict(row)
        item["maintenance_status"] = maintenance_status(
            item["next_due_date"],
            item["next_due_hours"],
            float(item["running_hours"]),
            str(item["criticality"]),
        )
        result.append(item)

    return result


@router.patch("/equipment/{equipment_id}/running-hours")
def update_running_hours(
    equipment_id: str,
    running_hours: float,
    identity: TokenIdentity = Depends(verify_access_token),
):
    if running_hours < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Running hours cannot be negative.",
        )

    _init_tables()

    with _engine().begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE pms_equipment
                SET running_hours = :running_hours
                WHERE id = :equipment_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "running_hours": running_hours,
                "equipment_id": equipment_id,
                "tenant_id": identity.tenant_id,
            },
        )

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="PMS equipment not found.",
        )

    return {
        "equipment_id": equipment_id,
        "running_hours": running_hours,
        "status": "updated",
    }


@router.patch("/jobs/{job_id}/complete")
def complete_maintenance_job(
    job_id: str,
    identity: TokenIdentity = Depends(verify_access_token),
):
    _init_tables()
    completed_at = datetime.now(timezone.utc)

    with _engine().begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE pms_jobs
                SET job_status = 'COMPLETED',
                    completed_at = :completed_at
                WHERE id = :job_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "completed_at": completed_at,
                "job_id": job_id,
                "tenant_id": identity.tenant_id,
            },
        )

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Maintenance job not found.",
        )

    return {
        "job_id": job_id,
        "job_status": "COMPLETED",
        "completed_at": completed_at,
    }


@router.get("/intelligence")
def pms_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    jobs = list_maintenance_jobs(identity)

    summary = {
        "NORMAL": 0,
        "DUE SOON": 0,
        "OVERDUE": 0,
        "CRITICAL": 0,
    }

    for job in jobs:
        state = job["maintenance_status"]
        if state in summary:
            summary[state] += 1

    return {
        "tenant_id": identity.tenant_id,
        "generated_at": datetime.now(timezone.utc),
        "total_jobs": len(jobs),
        "summary": summary,
        "jobs": jobs,
    }

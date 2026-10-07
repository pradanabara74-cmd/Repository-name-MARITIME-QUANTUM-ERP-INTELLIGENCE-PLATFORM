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
    prefix="/api/v1/hr",
    tags=["HR & Shore Workforce Intelligence"],
)


# ============================================================
# REQUEST MODELS
# ============================================================

class EmployeeCreate(BaseModel):
    employee_number: str = Field(min_length=1, max_length=100)
    full_name: str = Field(min_length=1, max_length=255)
    department: str = Field(min_length=1, max_length=150)
    position: str = Field(min_length=1, max_length=150)
    employment_type: str = Field(default="permanent", max_length=50)
    join_date: date
    email: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=100)
    work_location: Optional[str] = Field(default=None, max_length=255)
    manager_employee_number: Optional[str] = Field(
        default=None,
        max_length=100,
    )
    notes: Optional[str] = None


class LeaveRequestCreate(BaseModel):
    employee_id: UUID
    leave_type: str = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date
    reason: Optional[str] = None


class LeaveDecision(BaseModel):
    decision: str = Field(min_length=1, max_length=50)
    remarks: Optional[str] = None


class TrainingCreate(BaseModel):
    employee_id: UUID
    training_name: str = Field(min_length=1, max_length=255)
    provider: Optional[str] = Field(default=None, max_length=255)
    start_date: date
    end_date: Optional[date] = None
    expiry_date: Optional[date] = None
    certificate_reference: Optional[str] = Field(
        default=None,
        max_length=255,
    )
    cost: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=10)
    notes: Optional[str] = None


class PerformanceReviewCreate(BaseModel):
    employee_id: UUID
    review_period: str = Field(min_length=1, max_length=100)
    performance_score: Decimal = Field(ge=0, le=100)
    reviewer: str = Field(min_length=1, max_length=255)
    strengths: Optional[str] = None
    development_actions: Optional[str] = None
    notes: Optional[str] = None


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

def init_hr_tables():
    engine = get_engine()

    with engine.begin() as conn:

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS hr_employees (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    employee_number VARCHAR(100) NOT NULL,
                    full_name VARCHAR(255) NOT NULL,
                    department VARCHAR(150) NOT NULL,
                    position VARCHAR(150) NOT NULL,
                    employment_type VARCHAR(50) NOT NULL,
                    join_date DATE NOT NULL,
                    email VARCHAR(255),
                    phone VARCHAR(100),
                    work_location VARCHAR(255),
                    manager_employee_number VARCHAR(100),
                    employment_status VARCHAR(50)
                        NOT NULL DEFAULT 'active',
                    notes TEXT,
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (tenant_id, employee_number)
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS hr_leave_requests (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    employee_id UUID NOT NULL,
                    leave_type VARCHAR(100) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    reason TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'pending',
                    decision_by VARCHAR(255),
                    decision_at TIMESTAMPTZ,
                    decision_remarks TEXT,
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
                CREATE TABLE IF NOT EXISTS hr_training_records (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    employee_id UUID NOT NULL,
                    training_name VARCHAR(255) NOT NULL,
                    provider VARCHAR(255),
                    start_date DATE NOT NULL,
                    end_date DATE,
                    expiry_date DATE,
                    certificate_reference VARCHAR(255),
                    cost NUMERIC(18,2) NOT NULL DEFAULT 0,
                    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                    status VARCHAR(50) NOT NULL DEFAULT 'recorded',
                    notes TEXT,
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS hr_performance_reviews (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    employee_id UUID NOT NULL,
                    review_period VARCHAR(100) NOT NULL,
                    performance_score NUMERIC(5,2) NOT NULL,
                    reviewer VARCHAR(255) NOT NULL,
                    strengths TEXT,
                    development_actions TEXT,
                    notes TEXT,
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_hr_employee_department
                ON hr_employees (tenant_id, department)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_hr_leave_status
                ON hr_leave_requests (tenant_id, status)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_hr_training_expiry
                ON hr_training_records (tenant_id, expiry_date)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_hr_performance_employee
                ON hr_performance_reviews (tenant_id, employee_id)
                """
            )
        )

  # ============================================================
# EMPLOYEE MASTER / SHORE WORKFORCE
# ============================================================

@router.get(
    "/employees",
    summary="List Shore Employees",
)
def list_employees(
    department: Optional[str] = None,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hr_tables()
    engine = get_engine()

    sql = """
        SELECT *
        FROM hr_employees
        WHERE tenant_id = :tenant_id
    """

    params = {"tenant_id": identity.tenant_id}

    if department:
        sql += " AND department = :department"
        params["department"] = department

    sql += " ORDER BY full_name"

    with engine.begin() as conn:
        rows = conn.execute(
            text(sql),
            params,
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/employees",
    status_code=status.HTTP_201_CREATED,
    summary="Create Shore Employee",
)
def create_employee(
    payload: EmployeeCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hr_tables()
    engine = get_engine()
    employee_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT id
                FROM hr_employees
                WHERE tenant_id = :tenant_id
                  AND employee_number = :employee_number
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "employee_number": payload.employee_number,
            },
        ).first()

        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Employee number already exists.",
            )

        conn.execute(
            text(
                """
                INSERT INTO hr_employees (
                    id,
                    tenant_id,
                    employee_number,
                    full_name,
                    department,
                    position,
                    employment_type,
                    join_date,
                    email,
                    phone,
                    work_location,
                    manager_employee_number,
                    employment_status,
                    notes,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :employee_number,
                    :full_name,
                    :department,
                    :position,
                    :employment_type,
                    :join_date,
                    :email,
                    :phone,
                    :work_location,
                    :manager_employee_number,
                    'active',
                    :notes,
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": employee_id,
                "tenant_id": identity.tenant_id,
                "employee_number": payload.employee_number,
                "full_name": payload.full_name,
                "department": payload.department,
                "position": payload.position,
                "employment_type": payload.employment_type,
                "join_date": payload.join_date,
                "email": payload.email,
                "phone": payload.phone,
                "work_location": payload.work_location,
                "manager_employee_number":
                    payload.manager_employee_number,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(employee_id),
        "employee_number": payload.employee_number,
        "full_name": payload.full_name,
        "department": payload.department,
        "position": payload.position,
        "employment_status": "active",
    }


@router.get(
    "/employees/{employee_id}",
    summary="Get Shore Employee",
)
def get_employee(
    employee_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hr_tables()
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT *
                FROM hr_employees
                WHERE id = :employee_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "employee_id": employee_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found.",
        )

    return serialize_row(row)


@router.get(
    "/workforce/intelligence",
    summary="Shore Workforce Intelligence",
)
def workforce_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hr_tables()
    engine = get_engine()

    with engine.begin() as conn:
        summary = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_employees,
                    COUNT(*) FILTER (
                        WHERE employment_status = 'active'
                    ) AS active_employees,
                    COUNT(*) FILTER (
                        WHERE employment_status <> 'active'
                    ) AS inactive_employees
                FROM hr_employees
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        departments = conn.execute(
            text(
                """
                SELECT
                    department,
                    COUNT(*) AS employee_count
                FROM hr_employees
                WHERE tenant_id = :tenant_id
                  AND employment_status = 'active'
                GROUP BY department
                ORDER BY employee_count DESC, department
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "total_employees": int(summary["total_employees"]),
        "active_employees": int(summary["active_employees"]),
        "inactive_employees": int(summary["inactive_employees"]),
        "department_distribution": [
            serialize_row(row)
            for row in departments
        ],
    }

# ============================================================
# LEAVE MANAGEMENT / APPROVAL WORKFLOW
# ============================================================

@router.get(
    "/leave-requests",
    summary="List Leave Requests",
)
def list_leave_requests(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hr_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    l.*,
                    e.employee_number,
                    e.full_name,
                    e.department,
                    e.position
                FROM hr_leave_requests l
                JOIN hr_employees e
                  ON e.id = l.employee_id
                 AND e.tenant_id = l.tenant_id
                WHERE l.tenant_id = :tenant_id
                ORDER BY l.created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/leave-requests",
    status_code=status.HTTP_201_CREATED,
    summary="Create Leave Request",
)
def create_leave_request(
    payload: LeaveRequestCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hr_tables()

    if payload.end_date < payload.start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date cannot be earlier than start date.",
        )

    engine = get_engine()
    request_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        employee = conn.execute(
            text(
                """
                SELECT id
                FROM hr_employees
                WHERE id = :employee_id
                  AND tenant_id = :tenant_id
                  AND employment_status = 'active'
                """
            ),
            {
                "employee_id": payload.employee_id,
                "tenant_id": identity.tenant_id,
            },
        ).first()

        if employee is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Active employee not found.",
            )

        overlapping = conn.execute(
            text(
                """
                SELECT id
                FROM hr_leave_requests
                WHERE tenant_id = :tenant_id
                  AND employee_id = :employee_id
                  AND status IN ('pending', 'approved')
                  AND start_date <= :end_date
                  AND end_date >= :start_date
                LIMIT 1
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "employee_id": payload.employee_id,
                "start_date": payload.start_date,
                "end_date": payload.end_date,
            },
        ).first()

        if overlapping is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Overlapping leave request already exists.",
            )

        conn.execute(
            text(
                """
                INSERT INTO hr_leave_requests (
                    id,
                    tenant_id,
                    employee_id,
                    leave_type,
                    start_date,
                    end_date,
                    reason,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :employee_id,
                    :leave_type,
                    :start_date,
                    :end_date,
                    :reason,
                    'pending',
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": request_id,
                "tenant_id": identity.tenant_id,
                "employee_id": payload.employee_id,
                "leave_type": payload.leave_type,
                "start_date": payload.start_date,
                "end_date": payload.end_date,
                "reason": payload.reason,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(request_id),
        "employee_id": str(payload.employee_id),
        "leave_type": payload.leave_type,
        "start_date": payload.start_date.isoformat(),
        "end_date": payload.end_date.isoformat(),
        "status": "pending",
    }


@router.post(
    "/leave-requests/{request_id}/decision",
    summary="Approve or Reject Leave Request",
)
def decide_leave_request(
    request_id: UUID,
    payload: LeaveDecision,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hr_tables()

    decision = payload.decision.strip().lower()

    if decision not in {"approved", "rejected"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Decision must be approved or rejected.",
        )

    engine = get_engine()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        leave_request = conn.execute(
            text(
                """
                SELECT id, status
                FROM hr_leave_requests
                WHERE id = :request_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {
                "request_id": request_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if leave_request is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Leave request not found.",
            )

        if leave_request["status"] != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Leave request has already been decided.",
            )

        conn.execute(
            text(
                """
                UPDATE hr_leave_requests
                SET status = :decision,
                    decision_by = :decision_by,
                    decision_at = :decision_at,
                    decision_remarks = :decision_remarks,
                    updated_at = :updated_at
                WHERE id = :request_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "decision": decision,
                "decision_by": identity.user_id,
                "decision_at": now,
                "decision_remarks": payload.remarks,
                "updated_at": now,
                "request_id": request_id,
                "tenant_id": identity.tenant_id,
            },
        )

    return {
        "id": str(request_id),
        "status": decision,
        "decision_by": identity.user_id,
        "decision_at": now.isoformat(),
    }

# ============================================================
# TRAINING / COMPETENCY MANAGEMENT
# ============================================================

@router.post(
    "/training",
    status_code=status.HTTP_201_CREATED,
    summary="Record Employee Training",
)
def create_training(
    payload: TrainingCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hr_tables()
    engine = get_engine()
    training_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        employee = conn.execute(
            text(
                """
                SELECT id
                FROM hr_employees
                WHERE id = :employee_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "employee_id": payload.employee_id,
                "tenant_id": identity.tenant_id,
            },
        ).first()

        if employee is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Employee not found.",
            )

        conn.execute(
            text(
                """
                INSERT INTO hr_training_records (
                    id,
                    tenant_id,
                    employee_id,
                    training_name,
                    provider,
                    start_date,
                    end_date,
                    expiry_date,
                    certificate_reference,
                    cost,
                    currency,
                    status,
                    notes,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :employee_id,
                    :training_name,
                    :provider,
                    :start_date,
                    :end_date,
                    :expiry_date,
                    :certificate_reference,
                    :cost,
                    :currency,
                    'recorded',
                    :notes,
                    :created_by,
                    :created_at
                )
                """
            ),
            {
                "id": training_id,
                "tenant_id": identity.tenant_id,
                "employee_id": payload.employee_id,
                "training_name": payload.training_name,
                "provider": payload.provider,
                "start_date": payload.start_date,
                "end_date": payload.end_date,
                "expiry_date": payload.expiry_date,
                "certificate_reference":
                    payload.certificate_reference,
                "cost": payload.cost,
                "currency": payload.currency.upper(),
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

    return {
        "id": str(training_id),
        "employee_id": str(payload.employee_id),
        "training_name": payload.training_name,
        "expiry_date": (
            payload.expiry_date.isoformat()
            if payload.expiry_date
            else None
        ),
        "status": "recorded",
    }


@router.get(
    "/training",
    summary="List Training Records",
)
def list_training(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hr_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    t.*,
                    e.employee_number,
                    e.full_name,
                    e.department,
                    e.position
                FROM hr_training_records t
                JOIN hr_employees e
                  ON e.id = t.employee_id
                 AND e.tenant_id = t.tenant_id
                WHERE t.tenant_id = :tenant_id
                ORDER BY t.start_date DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.get(
    "/training/expiring",
    summary="Training and Certificate Expiry Intelligence",
)
def expiring_training(
    days: int = 90,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hr_tables()

    if days < 1 or days > 3650:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Days must be between 1 and 3650.",
        )

    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    t.*,
                    e.employee_number,
                    e.full_name,
                    e.department,
                    e.position,
                    (t.expiry_date - CURRENT_DATE)
                        AS days_remaining
                FROM hr_training_records t
                JOIN hr_employees e
                  ON e.id = t.employee_id
                 AND e.tenant_id = t.tenant_id
                WHERE t.tenant_id = :tenant_id
                  AND t.expiry_date IS NOT NULL
                  AND t.expiry_date
                      <= CURRENT_DATE + :days
                ORDER BY t.expiry_date ASC
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "days": days,
            },
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# ============================================================
# PERFORMANCE MANAGEMENT
# ============================================================

@router.post(
    "/performance-reviews",
    status_code=status.HTTP_201_CREATED,
    summary="Create Performance Review",
)
def create_performance_review(
    payload: PerformanceReviewCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hr_tables()
    engine = get_engine()
    review_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        employee = conn.execute(
            text(
                """
                SELECT id
                FROM hr_employees
                WHERE id = :employee_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "employee_id": payload.employee_id,
                "tenant_id": identity.tenant_id,
            },
        ).first()

        if employee is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Employee not found.",
            )

        conn.execute(
            text(
                """
                INSERT INTO hr_performance_reviews (
                    id,
                    tenant_id,
                    employee_id,
                    review_period,
                    performance_score,
                    reviewer,
                    strengths,
                    development_actions,
                    notes,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :employee_id,
                    :review_period,
                    :performance_score,
                    :reviewer,
                    :strengths,
                    :development_actions,
                    :notes,
                    :created_by,
                    :created_at
                )
                """
            ),
            {
                "id": review_id,
                "tenant_id": identity.tenant_id,
                "employee_id": payload.employee_id,
                "review_period": payload.review_period,
                "performance_score":
                    payload.performance_score,
                "reviewer": payload.reviewer,
                "strengths": payload.strengths,
                "development_actions":
                    payload.development_actions,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

    return {
        "id": str(review_id),
        "employee_id": str(payload.employee_id),
        "review_period": payload.review_period,
        "performance_score":
            float(payload.performance_score),
    }


@router.get(
    "/performance-reviews",
    summary="List Performance Reviews",
)
def list_performance_reviews(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hr_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    p.*,
                    e.employee_number,
                    e.full_name,
                    e.department,
                    e.position
                FROM hr_performance_reviews p
                JOIN hr_employees e
                  ON e.id = p.employee_id
                 AND e.tenant_id = p.tenant_id
                WHERE p.tenant_id = :tenant_id
                ORDER BY p.created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]

# ============================================================
# HR INTELLIGENCE DASHBOARD
# ============================================================

@router.get(
    "/dashboard",
    summary="HR Intelligence Dashboard",
)
def hr_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hr_tables()
    engine = get_engine()

    with engine.begin() as conn:
        workforce = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_employees,
                    COUNT(*) FILTER (
                        WHERE employment_status = 'active'
                    ) AS active_employees
                FROM hr_employees
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        leave_summary = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE status = 'pending'
                    ) AS pending,
                    COUNT(*) FILTER (
                        WHERE status = 'approved'
                    ) AS approved,
                    COUNT(*) FILTER (
                        WHERE status = 'rejected'
                    ) AS rejected
                FROM hr_leave_requests
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        training_risk = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE expiry_date < CURRENT_DATE
                    ) AS expired,
                    COUNT(*) FILTER (
                        WHERE expiry_date >= CURRENT_DATE
                          AND expiry_date
                              <= CURRENT_DATE + 90
                    ) AS expiring_90_days
                FROM hr_training_records
                WHERE tenant_id = :tenant_id
                  AND expiry_date IS NOT NULL
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        performance = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_reviews,
                    COALESCE(
                        AVG(performance_score),
                        0
                    ) AS average_score
                FROM hr_performance_reviews
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

    return {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "workforce": {
            "total_employees": int(
                workforce["total_employees"]
            ),
            "active_employees": int(
                workforce["active_employees"]
            ),
        },
        "leave_management": {
            "pending": int(leave_summary["pending"]),
            "approved": int(leave_summary["approved"]),
            "rejected": int(leave_summary["rejected"]),
        },
        "competency_risk": {
            "expired_training": int(
                training_risk["expired"]
            ),
            "expiring_within_90_days": int(
                training_risk["expiring_90_days"]
            ),
        },
        "performance": {
            "total_reviews": int(
                performance["total_reviews"]
            ),
            "average_score": round(
                float(performance["average_score"]),
                2,
            ),
        },
    }


# ============================================================
# WORKFORCE RISK INTELLIGENCE
# ============================================================

@router.get(
    "/risk-intelligence",
    summary="Workforce Risk Intelligence",
)
def workforce_risk_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hr_tables()
    engine = get_engine()

    with engine.begin() as conn:
        metrics = conn.execute(
            text(
                """
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM hr_leave_requests
                        WHERE tenant_id = :tenant_id
                          AND status = 'pending'
                    ) AS pending_leave,
                    (
                        SELECT COUNT(*)
                        FROM hr_training_records
                        WHERE tenant_id = :tenant_id
                          AND expiry_date IS NOT NULL
                          AND expiry_date < CURRENT_DATE
                    ) AS expired_training,
                    (
                        SELECT COUNT(*)
                        FROM hr_training_records
                        WHERE tenant_id = :tenant_id
                          AND expiry_date IS NOT NULL
                          AND expiry_date >= CURRENT_DATE
                          AND expiry_date
                              <= CURRENT_DATE + 30
                    ) AS training_due_30_days
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

    pending_leave = int(metrics["pending_leave"])
    expired_training = int(metrics["expired_training"])
    training_due = int(metrics["training_due_30_days"])

    risk_score = (
        expired_training * 5
        + training_due * 2
        + pending_leave
    )

    if risk_score >= 20:
        risk_level = "HIGH"
    elif risk_score >= 5:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "risk_level": risk_level,
        "risk_score": risk_score,
        "indicators": {
            "pending_leave_requests": pending_leave,
            "expired_training_records":
                expired_training,
            "training_due_within_30_days":
                training_due,
        },
    }


# ============================================================
# HR AUDIT TRAIL
# ============================================================

@router.get(
    "/audit-trail",
    summary="HR Audit Trail",
)
def hr_audit_trail(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_hr_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM (
                    SELECT
                        'employee_created' AS event_type,
                        id AS entity_id,
                        employee_number AS reference,
                        full_name AS description,
                        created_by,
                        created_at
                    FROM hr_employees
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'leave_request_created' AS event_type,
                        id AS entity_id,
                        CAST(employee_id AS VARCHAR)
                            AS reference,
                        leave_type AS description,
                        created_by,
                        created_at
                    FROM hr_leave_requests
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'training_recorded' AS event_type,
                        id AS entity_id,
                        CAST(employee_id AS VARCHAR)
                            AS reference,
                        training_name AS description,
                        created_by,
                        created_at
                    FROM hr_training_records
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'performance_review_created'
                            AS event_type,
                        id AS entity_id,
                        CAST(employee_id AS VARCHAR)
                            AS reference,
                        review_period AS description,
                        created_by,
                        created_at
                    FROM hr_performance_reviews
                    WHERE tenant_id = :tenant_id
                ) AS events
                ORDER BY created_at DESC
                LIMIT 500
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# Initialize HR schema when module is loaded.
init_hr_tables()

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..core.database import get_engine
from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/workflow",
    tags=["Workflow & Approval Intelligence"],
)


# ============================================================
# DATA MODELS
# ============================================================

class WorkflowCreate(BaseModel):
    workflow_code: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=255)
    department: str = Field(min_length=1, max_length=100)
    workflow_type: str = Field(min_length=1, max_length=100)
    description: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    priority: str = "normal"
    amount: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(default="USD", max_length=10)
    requested_by: str = Field(min_length=1, max_length=255)


class ApprovalStepCreate(BaseModel):
    step_number: int = Field(ge=1)
    approver_role: str = Field(min_length=1, max_length=100)
    approver_user_id: Optional[str] = None
    required: bool = True


class ApprovalDecision(BaseModel):
    decision: str
    remarks: Optional[str] = None


class WorkflowStatusUpdate(BaseModel):
    status: str
    remarks: Optional[str] = None


def serialize_workflow_row(row):
    result = dict(row)

    for key, value in result.items():
        if isinstance(value, UUID):
            result[key] = str(value)
        elif isinstance(value, Decimal):
            result[key] = float(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()

    return result


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_workflow_tables():
    engine = get_engine()

    with engine.begin() as conn:

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mq_workflows (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(100) NOT NULL,
                workflow_code VARCHAR(100) NOT NULL,
                title VARCHAR(255) NOT NULL,
                department VARCHAR(100) NOT NULL,
                workflow_type VARCHAR(100) NOT NULL,
                description TEXT,
                reference_type VARCHAR(100),
                reference_id VARCHAR(255),
                vessel_id VARCHAR(100),
                vessel_name VARCHAR(255),
                priority VARCHAR(30) DEFAULT 'normal',
                amount NUMERIC(18,2) DEFAULT 0,
                currency VARCHAR(10) DEFAULT 'USD',
                requested_by VARCHAR(255) NOT NULL,
                status VARCHAR(40) DEFAULT 'draft',
                created_by VARCHAR(255) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE (tenant_id, workflow_code)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mq_workflow_steps (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(100) NOT NULL,
                workflow_id UUID NOT NULL
                    REFERENCES mq_workflows(id),
                step_number INTEGER NOT NULL,
                approver_role VARCHAR(100) NOT NULL,
                approver_user_id VARCHAR(255),
                required BOOLEAN DEFAULT TRUE,
                status VARCHAR(40) DEFAULT 'pending',
                decision_by VARCHAR(255),
                decision_remarks TEXT,
                decided_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL,
                UNIQUE (workflow_id, step_number)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mq_workflow_activity (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(100) NOT NULL,
                workflow_id UUID NOT NULL
                    REFERENCES mq_workflows(id),
                action VARCHAR(100) NOT NULL,
                remarks TEXT,
                performed_by VARCHAR(255) NOT NULL,
                performed_at TIMESTAMPTZ NOT NULL
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS
                idx_mq_workflow_tenant_status
            ON mq_workflows(tenant_id, status)
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS
                idx_mq_workflow_steps
            ON mq_workflow_steps(
                tenant_id, workflow_id, step_number
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS
                idx_mq_workflow_activity
            ON mq_workflow_activity(
                tenant_id, workflow_id, performed_at
            )
        """))

  # ============================================================
# WORKFLOW BLOK 2 — REGISTRATION & MANAGEMENT
# ============================================================

VALID_PRIORITIES = {"low", "normal", "high", "critical"}


def record_workflow_activity(
    conn, tenant_id, workflow_id, action, performed_by,
    remarks=None,
):
    conn.execute(
        text("""
            INSERT INTO mq_workflow_activity (
                id, tenant_id, workflow_id, action,
                remarks, performed_by, performed_at
            )
            VALUES (
                :id, :tenant_id, :workflow_id, :action,
                :remarks, :performed_by, :performed_at
            )
        """),
        {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "workflow_id": workflow_id,
            "action": action,
            "remarks": remarks,
            "performed_by": performed_by,
            "performed_at": datetime.now(timezone.utc),
        },
    )


@router.get(
    "/requests",
    summary="List Workflow Requests",
)
def list_workflow_requests(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM mq_workflows
                WHERE tenant_id = :tenant_id
                ORDER BY created_at DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_workflow_row(row) for row in rows]


@router.post(
    "/requests",
    status_code=status.HTTP_201_CREATED,
    summary="Create Workflow Request",
)
def create_workflow_request(
    payload: WorkflowCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    priority = payload.priority.strip().lower()

    if priority not in VALID_PRIORITIES:
        raise HTTPException(
            status_code=400,
            detail="Invalid workflow priority.",
        )

    workflow_id = uuid4()
    now = datetime.now(timezone.utc)
    engine = get_engine()

    with engine.begin() as conn:
        existing = conn.execute(
            text("""
                SELECT id
                FROM mq_workflows
                WHERE tenant_id = :tenant_id
                  AND workflow_code = :workflow_code
            """),
            {
                "tenant_id": identity.tenant_id,
                "workflow_code": payload.workflow_code,
            },
        ).first()

        if existing:
            raise HTTPException(
                status_code=409,
                detail="Workflow code already exists.",
            )

        conn.execute(
            text("""
                INSERT INTO mq_workflows (
                    id, tenant_id, workflow_code,
                    title, department, workflow_type,
                    description, reference_type, reference_id,
                    vessel_id, vessel_name, priority,
                    amount, currency, requested_by,
                    status, created_by, created_at, updated_at
                )
                VALUES (
                    :id, :tenant_id, :workflow_code,
                    :title, :department, :workflow_type,
                    :description, :reference_type, :reference_id,
                    :vessel_id, :vessel_name, :priority,
                    :amount, :currency, :requested_by,
                    'draft', :created_by, :created_at, :updated_at
                )
            """),
            {
                "id": workflow_id,
                "tenant_id": identity.tenant_id,
                "workflow_code": payload.workflow_code,
                "title": payload.title,
                "department": payload.department,
                "workflow_type": payload.workflow_type,
                "description": payload.description,
                "reference_type": payload.reference_type,
                "reference_id": payload.reference_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "priority": priority,
                "amount": payload.amount,
                "currency": payload.currency.upper(),
                "requested_by": payload.requested_by,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

        record_workflow_activity(
            conn,
            identity.tenant_id,
            workflow_id,
            "workflow_created",
            identity.user_id,
        )

    return {
        "id": str(workflow_id),
        "workflow_code": payload.workflow_code,
        "status": "draft",
    }


@router.get(
    "/requests/{workflow_id}",
    summary="Get Workflow Request Details",
)
def get_workflow_request(
    workflow_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        workflow = conn.execute(
            text("""
                SELECT *
                FROM mq_workflows
                WHERE id = :workflow_id
                  AND tenant_id = :tenant_id
            """),
            {
                "workflow_id": workflow_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if workflow is None:
            raise HTTPException(
                status_code=404,
                detail="Workflow request not found.",
            )

        steps = conn.execute(
            text("""
                SELECT *
                FROM mq_workflow_steps
                WHERE workflow_id = :workflow_id
                  AND tenant_id = :tenant_id
                ORDER BY step_number
            """),
            {
                "workflow_id": workflow_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().all()

    return {
        "workflow": serialize_workflow_row(workflow),
        "approval_steps": [
            serialize_workflow_row(row) for row in steps
        ],
    }


@router.post(
    "/requests/{workflow_id}/steps",
    status_code=status.HTTP_201_CREATED,
    summary="Add Workflow Approval Step",
)
def add_workflow_approval_step(
    workflow_id: UUID,
    payload: ApprovalStepCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()
    step_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        workflow = conn.execute(
            text("""
                SELECT status, created_by
                FROM mq_workflows
                WHERE id = :workflow_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "workflow_id": workflow_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if workflow is None:
            raise HTTPException(
                status_code=404,
                detail="Workflow request not found.",
            )

        if workflow["created_by"] != identity.user_id:
            raise HTTPException(
                status_code=403,
                detail="Only the workflow creator may configure steps.",
            )

        if workflow["status"] != "draft":
            raise HTTPException(
                status_code=409,
                detail="Approval steps can only be added in draft.",
            )

        duplicate = conn.execute(
            text("""
                SELECT id
                FROM mq_workflow_steps
                WHERE workflow_id = :workflow_id
                  AND tenant_id = :tenant_id
                  AND step_number = :step_number
            """),
            {
                "workflow_id": workflow_id,
                "tenant_id": identity.tenant_id,
                "step_number": payload.step_number,
            },
        ).first()

        if duplicate:
            raise HTTPException(
                status_code=409,
                detail="Approval step number already exists.",
            )

        conn.execute(
            text("""
                INSERT INTO mq_workflow_steps (
                    id, tenant_id, workflow_id,
                    step_number, approver_role,
                    approver_user_id, required,
                    status, created_at
                )
                VALUES (
                    :id, :tenant_id, :workflow_id,
                    :step_number, :approver_role,
                    :approver_user_id, :required,
                    'pending', :created_at
                )
            """),
            {
                "id": step_id,
                "tenant_id": identity.tenant_id,
                "workflow_id": workflow_id,
                "step_number": payload.step_number,
                "approver_role": payload.approver_role,
                "approver_user_id": payload.approver_user_id,
                "required": payload.required,
                "created_at": now,
            },
        )

        record_workflow_activity(
            conn,
            identity.tenant_id,
            workflow_id,
            "approval_step_added",
            identity.user_id,
            f"Step {payload.step_number}: {payload.approver_role}",
        )

    return {
        "id": str(step_id),
        "workflow_id": str(workflow_id),
        "step_number": payload.step_number,
        "status": "pending",
    }

# ============================================================
# WORKFLOW BLOK 3 — SUBMISSION & APPROVAL DECISIONS
# ============================================================

@router.post(
    "/requests/{workflow_id}/submit",
    summary="Submit Workflow for Approval",
)
def submit_workflow(
    workflow_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        workflow = conn.execute(
            text("""
                SELECT status, created_by
                FROM mq_workflows
                WHERE id = :workflow_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "workflow_id": workflow_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if workflow is None:
            raise HTTPException(404, "Workflow not found.")

        if workflow["created_by"] != identity.user_id:
            raise HTTPException(
                403, "Only the creator can submit this workflow."
            )

        if workflow["status"] != "draft":
            raise HTTPException(
                409, "Only draft workflows can be submitted."
            )

        steps = conn.execute(
            text("""
                SELECT step_number, required
                FROM mq_workflow_steps
                WHERE workflow_id = :workflow_id
                  AND tenant_id = :tenant_id
                ORDER BY step_number
            """),
            {
                "workflow_id": workflow_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().all()

        if not steps or not any(s["required"] for s in steps):
            raise HTTPException(
                400, "At least one required approval step is needed."
            )

        conn.execute(
            text("""
                UPDATE mq_workflows
                SET status = 'pending_approval',
                    updated_at = :now
                WHERE id = :workflow_id
                  AND tenant_id = :tenant_id
            """),
            {
                "now": now,
                "workflow_id": workflow_id,
                "tenant_id": identity.tenant_id,
            },
        )

        record_workflow_activity(
            conn, identity.tenant_id, workflow_id,
            "submitted_for_approval", identity.user_id,
        )

    return {
        "workflow_id": str(workflow_id),
        "status": "pending_approval",
    }


@router.post(
    "/requests/{workflow_id}/steps/{step_id}/decision",
    summary="Record Approval Decision",
)
def decide_workflow_step(
    workflow_id: UUID,
    step_id: UUID,
    payload: ApprovalDecision,
    identity: TokenIdentity = Depends(verify_access_token),
):
    decision = payload.decision.strip().lower()

    if decision not in {"approved", "rejected"}:
        raise HTTPException(
            400, "Decision must be approved or rejected."
        )

    engine = get_engine()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        workflow = conn.execute(
            text("""
                SELECT status, created_by
                FROM mq_workflows
                WHERE id = :workflow_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "workflow_id": workflow_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if workflow is None:
            raise HTTPException(404, "Workflow not found.")

        if workflow["status"] != "pending_approval":
            raise HTTPException(
                409, "Workflow is not awaiting approval."
            )

        step = conn.execute(
            text("""
                SELECT *
                FROM mq_workflow_steps
                WHERE id = :step_id
                  AND workflow_id = :workflow_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "step_id": step_id,
                "workflow_id": workflow_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if step is None:
            raise HTTPException(404, "Approval step not found.")

        if step["status"] != "pending":
            raise HTTPException(
                409, "This step has already been decided."
            )

        # Enforce explicit user assignment.
        # Role-only steps require a separate trusted RBAC
        # assignment mechanism before they can be approved.
        assigned_user = step["approver_user_id"]

        if not assigned_user:
            raise HTTPException(
                409,
                "Approver user must be assigned before decision.",
            )

        if assigned_user != identity.user_id:
            raise HTTPException(
                403, "You are not the assigned approver."
            )

        if workflow["created_by"] == identity.user_id:
            raise HTTPException(
                403, "Self-approval is not permitted."
            )

        # Enforce sequential approval.
        earlier = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM mq_workflow_steps
                WHERE workflow_id = :workflow_id
                  AND tenant_id = :tenant_id
                  AND step_number < :step_number
                  AND required = TRUE
                  AND status <> 'approved'
            """),
            {
                "workflow_id": workflow_id,
                "tenant_id": identity.tenant_id,
                "step_number": step["step_number"],
            },
        ).scalar_one()

        if earlier > 0:
            raise HTTPException(
                409, "Previous required steps must be approved first."
            )

        conn.execute(
            text("""
                UPDATE mq_workflow_steps
                SET status = :decision,
                    decision_by = :decision_by,
                    decision_remarks = :remarks,
                    decided_at = :now
                WHERE id = :step_id
                  AND tenant_id = :tenant_id
            """),
            {
                "decision": decision,
                "decision_by": identity.user_id,
                "remarks": payload.remarks,
                "now": now,
                "step_id": step_id,
                "tenant_id": identity.tenant_id,
            },
        )

        if decision == "rejected":
            new_status = "rejected"
        else:
            remaining = conn.execute(
                text("""
                    SELECT COUNT(*)
                    FROM mq_workflow_steps
                    WHERE workflow_id = :workflow_id
                      AND tenant_id = :tenant_id
                      AND required = TRUE
                      AND status <> 'approved'
                """),
                {
                    "workflow_id": workflow_id,
                    "tenant_id": identity.tenant_id,
                },
            ).scalar_one()

            new_status = (
                "approved" if remaining == 0
                else "pending_approval"
            )

        conn.execute(
            text("""
                UPDATE mq_workflows
                SET status = :status,
                    updated_at = :now
                WHERE id = :workflow_id
                  AND tenant_id = :tenant_id
            """),
            {
                "status": new_status,
                "now": now,
                "workflow_id": workflow_id,
                "tenant_id": identity.tenant_id,
            },
        )

        record_workflow_activity(
            conn, identity.tenant_id, workflow_id,
            f"approval_{decision}", identity.user_id,
            payload.remarks,
        )

    return {
        "workflow_id": str(workflow_id),
        "step_id": str(step_id),
        "decision": decision,
        "workflow_status": new_status,
    }


@router.get(
    "/requests/{workflow_id}/history",
    summary="Get Workflow Approval History",
)
def get_workflow_history(
    workflow_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        existing = conn.execute(
            text("""
                SELECT id
                FROM mq_workflows
                WHERE id = :workflow_id
                  AND tenant_id = :tenant_id
            """),
            {
                "workflow_id": workflow_id,
                "tenant_id": identity.tenant_id,
            },
        ).first()

        if existing is None:
            raise HTTPException(404, "Workflow not found.")

        rows = conn.execute(
            text("""
                SELECT *
                FROM mq_workflow_activity
                WHERE workflow_id = :workflow_id
                  AND tenant_id = :tenant_id
                ORDER BY performed_at DESC
            """),
            {
                "workflow_id": workflow_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().all()

    return [serialize_workflow_row(row) for row in rows]

# ============================================================
# WORKFLOW BLOK 4 — DASHBOARD & INTELLIGENCE
# ============================================================

@router.get(
    "/dashboard",
    summary="Workflow & Approval Dashboard",
)
def workflow_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT status, COUNT(*) AS total
                FROM mq_workflows
                WHERE tenant_id = :tenant_id
                GROUP BY status
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        pending_steps = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM mq_workflow_steps s
                JOIN mq_workflows w
                  ON w.id = s.workflow_id
                 AND w.tenant_id = s.tenant_id
                WHERE s.tenant_id = :tenant_id
                  AND s.status = 'pending'
                  AND w.status = 'pending_approval'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM mq_workflow_steps previous
                      WHERE previous.workflow_id = s.workflow_id
                        AND previous.tenant_id = s.tenant_id
                        AND previous.required = TRUE
                        AND previous.step_number < s.step_number
                        AND previous.status <> 'approved'
                  )
            """),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

    totals = {row["status"]: row["total"] for row in rows}

    return {
        "total_workflows": sum(totals.values()),
        "by_status": totals,
        "pending_approval_steps": pending_steps,
    }


@router.get(
    "/my-approvals",
    summary="My Pending Approval Tasks",
)
def my_pending_approvals(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    s.id AS step_id,
                    s.workflow_id,
                    s.step_number,
                    s.approver_role,
                    s.status AS step_status,
                    w.workflow_code,
                    w.title,
                    w.department,
                    w.priority,
                    w.amount,
                    w.currency,
                    w.created_at
                FROM mq_workflow_steps s
                JOIN mq_workflows w
                  ON w.id = s.workflow_id
                 AND w.tenant_id = s.tenant_id
                WHERE s.tenant_id = :tenant_id
                  AND s.approver_user_id = :user_id
                  AND s.status = 'pending'
                  AND w.status = 'pending_approval'
                  AND w.created_by <> :user_id
                  AND NOT EXISTS (
                      SELECT 1
                      FROM mq_workflow_steps previous
                      WHERE previous.workflow_id = s.workflow_id
                        AND previous.tenant_id = s.tenant_id
                        AND previous.required = TRUE
                        AND previous.step_number < s.step_number
                        AND previous.status <> 'approved'
                  )
                ORDER BY
                    CASE w.priority
                        WHEN 'critical' THEN 1
                        WHEN 'high' THEN 2
                        WHEN 'normal' THEN 3
                        ELSE 4
                    END,
                    w.created_at ASC
            """),
            {
                "tenant_id": identity.tenant_id,
                "user_id": identity.user_id,
            },
        ).mappings().all()

    return [serialize_workflow_row(row) for row in rows]


@router.get(
    "/risk-intelligence",
    summary="Workflow Risk Intelligence",
)
def workflow_risk_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    department,
                    priority,
                    status,
                    COUNT(*) AS total
                FROM mq_workflows
                WHERE tenant_id = :tenant_id
                  AND status IN ('draft', 'pending_approval')
                GROUP BY department, priority, status
                ORDER BY department, priority
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        unassigned = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM mq_workflow_steps s
                JOIN mq_workflows w
                  ON w.id = s.workflow_id
                 AND w.tenant_id = s.tenant_id
                WHERE s.tenant_id = :tenant_id
                  AND s.approver_user_id IS NULL
                  AND s.required = TRUE
                  AND s.status = 'pending'
                  AND w.status = 'pending_approval'
            """),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

    critical = sum(
        row["total"]
        for row in rows
        if row["priority"] == "critical"
    )

    high = sum(
        row["total"]
        for row in rows
        if row["priority"] == "high"
    )

    return {
        "critical_open_workflows": critical,
        "high_priority_open_workflows": high,
        "unassigned_required_approval_steps": unassigned,
        "risk_breakdown": [
            serialize_workflow_row(row) for row in rows
        ],
    }


@router.get(
    "/audit-trail",
    summary="Workflow Activity Audit Trail",
)
def workflow_audit_trail(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    a.id,
                    a.workflow_id,
                    w.workflow_code,
                    w.department,
                    a.action,
                    a.remarks,
                    a.performed_by,
                    a.performed_at
                FROM mq_workflow_activity a
                JOIN mq_workflows w
                  ON w.id = a.workflow_id
                 AND w.tenant_id = a.tenant_id
                WHERE a.tenant_id = :tenant_id
                ORDER BY a.performed_at DESC, a.id DESC
                LIMIT 500
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_workflow_row(row) for row in rows]


# ============================================================
# INITIALIZE WORKFLOW DATABASE
# ============================================================

init_workflow_tables()

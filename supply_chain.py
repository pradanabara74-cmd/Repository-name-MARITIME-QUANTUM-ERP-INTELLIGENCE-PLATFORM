from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from ..core.database import get_database_url
from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/supply-chain",
    tags=["Supply Chain & Procurement"],
)


def get_engine():
    return create_engine(get_database_url(), pool_pre_ping=True)


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_supply_chain_tables():
    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS purchase_requisitions (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    pr_number VARCHAR(100) NOT NULL,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    department VARCHAR(100) NOT NULL,
                    requested_by VARCHAR(255) NOT NULL,
                    request_date TIMESTAMPTZ NOT NULL,
                    required_date DATE,
                    priority VARCHAR(50) NOT NULL DEFAULT 'normal',
                    description TEXT NOT NULL,
                    justification TEXT,
                    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                    estimated_total NUMERIC(18,2) NOT NULL DEFAULT 0,
                    status VARCHAR(50) NOT NULL DEFAULT 'draft',
                    approved_by VARCHAR(255),
                    approved_at TIMESTAMPTZ,
                    rejection_reason TEXT,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (tenant_id, pr_number)
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS purchase_requisition_items (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    requisition_id UUID NOT NULL
                        REFERENCES purchase_requisitions(id)
                        ON DELETE CASCADE,
                    item_code VARCHAR(100),
                    item_name VARCHAR(255) NOT NULL,
                    specification TEXT,
                    quantity NUMERIC(18,3) NOT NULL,
                    unit VARCHAR(50) NOT NULL,
                    estimated_unit_price NUMERIC(18,2) NOT NULL DEFAULT 0,
                    estimated_total NUMERIC(18,2) NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_pr_tenant_status
                ON purchase_requisitions (tenant_id, status)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_pr_tenant_vessel
                ON purchase_requisitions (tenant_id, vessel_id)
                """
            )
        )


# ============================================================
# SCHEMAS
# ============================================================

class PurchaseRequisitionItemCreate(BaseModel):
    item_code: Optional[str] = None
    item_name: str = Field(min_length=1, max_length=255)
    specification: Optional[str] = None
    quantity: float = Field(gt=0)
    unit: str = Field(min_length=1, max_length=50)
    estimated_unit_price: float = Field(default=0, ge=0)


class PurchaseRequisitionCreate(BaseModel):
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None

    department: str = Field(min_length=1, max_length=100)
    requested_by: str = Field(min_length=1, max_length=255)

    required_date: Optional[str] = None

    priority: str = Field(default="normal", max_length=50)

    description: str = Field(min_length=1)
    justification: Optional[str] = None

    currency: str = Field(default="USD", min_length=3, max_length=10)

    items: list[PurchaseRequisitionItemCreate] = Field(min_length=1)


class PurchaseRequisitionDecision(BaseModel):
    action: str
    reason: Optional[str] = None


# ============================================================
# HELPERS
# ============================================================

def generate_pr_number() -> str:
    now = datetime.now(timezone.utc)
    return f"PR-{now:%Y%m%d}-{uuid4().hex[:8].upper()}"


def serialize_row(row):
    if row is None:
        return None

    result = dict(row)

    for key, value in result.items():
        if isinstance(value, UUID):
            result[key] = str(value)

    return result


# ============================================================
# CREATE PURCHASE REQUISITION
# ============================================================

@router.post(
    "/requisitions",
    status_code=status.HTTP_201_CREATED,
)
def create_purchase_requisition(
    payload: PurchaseRequisitionCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_supply_chain_tables()

    engine = get_engine()

    requisition_id = uuid4()
    pr_number = generate_pr_number()
    now = datetime.now(timezone.utc)

    estimated_total = sum(
        item.quantity * item.estimated_unit_price
        for item in payload.items
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO purchase_requisitions (
                    id,
                    tenant_id,
                    pr_number,
                    vessel_id,
                    vessel_name,
                    department,
                    requested_by,
                    request_date,
                    required_date,
                    priority,
                    description,
                    justification,
                    currency,
                    estimated_total,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :pr_number,
                    :vessel_id,
                    :vessel_name,
                    :department,
                    :requested_by,
                    :request_date,
                    :required_date,
                    :priority,
                    :description,
                    :justification,
                    :currency,
                    :estimated_total,
                    'submitted',
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": requisition_id,
                "tenant_id": identity.tenant_id,
                "pr_number": pr_number,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "department": payload.department,
                "requested_by": payload.requested_by,
                "request_date": now,
                "required_date": payload.required_date,
                "priority": payload.priority.lower(),
                "description": payload.description,
                "justification": payload.justification,
                "currency": payload.currency.upper(),
                "estimated_total": estimated_total,
                "created_at": now,
                "updated_at": now,
            },
        )

        for item in payload.items:
            item_total = item.quantity * item.estimated_unit_price

            conn.execute(
                text(
                    """
                    INSERT INTO purchase_requisition_items (
                        id,
                        tenant_id,
                        requisition_id,
                        item_code,
                        item_name,
                        specification,
                        quantity,
                        unit,
                        estimated_unit_price,
                        estimated_total,
                        created_at
                    )
                    VALUES (
                        :id,
                        :tenant_id,
                        :requisition_id,
                        :item_code,
                        :item_name,
                        :specification,
                        :quantity,
                        :unit,
                        :estimated_unit_price,
                        :estimated_total,
                        :created_at
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "tenant_id": identity.tenant_id,
                    "requisition_id": requisition_id,
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "specification": item.specification,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "estimated_unit_price": item.estimated_unit_price,
                    "estimated_total": item_total,
                    "created_at": now,
                },
            )

    return {
        "id": str(requisition_id),
        "pr_number": pr_number,
        "tenant_id": identity.tenant_id,
        "status": "submitted",
        "currency": payload.currency.upper(),
        "estimated_total": estimated_total,
    }


# ============================================================
# LIST PURCHASE REQUISITIONS
# ============================================================

@router.get("/requisitions")
def list_purchase_requisitions(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_supply_chain_tables()

    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM purchase_requisitions
                WHERE tenant_id = :tenant_id
                ORDER BY created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# ============================================================
# GET PURCHASE REQUISITION
# ============================================================

@router.get("/requisitions/{requisition_id}")
def get_purchase_requisition(
    requisition_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_supply_chain_tables()

    engine = get_engine()

    with engine.begin() as conn:
        requisition = conn.execute(
            text(
                """
                SELECT *
                FROM purchase_requisitions
                WHERE id = :id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "id": requisition_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if not requisition:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Purchase requisition not found.",
            )

        items = conn.execute(
            text(
                """
                SELECT *
                FROM purchase_requisition_items
                WHERE requisition_id = :requisition_id
                  AND tenant_id = :tenant_id
                ORDER BY created_at
                """
            ),
            {
                "requisition_id": requisition_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().all()

    result = serialize_row(requisition)
    result["items"] = [serialize_row(item) for item in items]

    return result


# ============================================================
# APPROVE / REJECT PURCHASE REQUISITION
# ============================================================

@router.post("/requisitions/{requisition_id}/decision")
def decide_purchase_requisition(
    requisition_id: UUID,
    payload: PurchaseRequisitionDecision,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_supply_chain_tables()

    action = payload.action.strip().lower()

    if action not in {"approve", "reject"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Action must be 'approve' or 'reject'.",
        )

    if action == "reject" and not payload.reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rejection reason is required.",
        )

    engine = get_engine()
    now = datetime.now(timezone.utc)

    new_status = "approved" if action == "approve" else "rejected"

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT id, status
                FROM purchase_requisitions
                WHERE id = :id
                  AND tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {
                "id": requisition_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Purchase requisition not found.",
            )

        if existing["status"] != "submitted":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only submitted requisitions can be approved or rejected.",
            )

        conn.execute(
            text(
                """
                UPDATE purchase_requisitions
                SET
                    status = :status,
                    approved_by = :approved_by,
                    approved_at = :approved_at,
                    rejection_reason = :rejection_reason,
                    updated_at = :updated_at
                WHERE id = :id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "status": new_status,
                "approved_by": identity.user_id,
                "approved_at": now if action == "approve" else None,
                "rejection_reason": payload.reason if action == "reject" else None,
                "updated_at": now,
                "id": requisition_id,
                "tenant_id": identity.tenant_id,
            },
        )

    return {
        "id": str(requisition_id),
        "status": new_status,
        "decided_by": identity.user_id,
        "decided_at": now,
    }


# ============================================================
# SUPPLY CHAIN DASHBOARD
# ============================================================

@router.get("/dashboard")
def supply_chain_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_supply_chain_tables()

    engine = get_engine()

    with engine.begin() as conn:
        summary = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_requisitions,
                    COUNT(*) FILTER (
                        WHERE status = 'submitted'
                    ) AS pending_approval,
                    COUNT(*) FILTER (
                        WHERE status = 'approved'
                    ) AS approved,
                    COUNT(*) FILTER (
                        WHERE status = 'rejected'
                    ) AS rejected,
                    COALESCE(
                        SUM(estimated_total) FILTER (
                            WHERE status = 'approved'
                        ),
                        0
                    ) AS approved_value
                FROM purchase_requisitions
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

    return {
        "tenant_id": identity.tenant_id,
        "generated_at": datetime.now(timezone.utc),
        "summary": dict(summary),
    }

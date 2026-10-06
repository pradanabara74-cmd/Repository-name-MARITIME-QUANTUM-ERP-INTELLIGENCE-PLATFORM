from datetime import date, datetime, timezone
from uuid import UUID, uuid4
from typing import Optional

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

# ============================================================
# RFQ & VENDOR QUOTATION MANAGEMENT
# ============================================================

class RFQCreate(BaseModel):
    requisition_id: str
    rfq_title: str
    closing_date: Optional[date] = None
    notes: Optional[str] = None


class VendorQuotationCreate(BaseModel):
    vendor_name: str
    vendor_code: Optional[str] = None
    currency: str = "USD"
    quotation_amount: float = Field(gt=0)
    delivery_days: int = Field(ge=0)
    payment_terms: Optional[str] = None
    quotation_reference: Optional[str] = None
    notes: Optional[str] = None


def init_rfq_tables():
    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS supply_chain_rfqs (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    requisition_id UUID NOT NULL,
                    rfq_number VARCHAR(100) NOT NULL,
                    rfq_title VARCHAR(255) NOT NULL,
                    closing_date DATE,
                    status VARCHAR(50) NOT NULL DEFAULT 'open',
                    notes TEXT,
                    created_by VARCHAR(255) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (tenant_id, rfq_number)
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS supply_chain_vendor_quotations (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    rfq_id UUID NOT NULL,
                    vendor_name VARCHAR(255) NOT NULL,
                    vendor_code VARCHAR(100),
                    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                    quotation_amount NUMERIC(18,2) NOT NULL,
                    delivery_days INTEGER NOT NULL DEFAULT 0,
                    payment_terms VARCHAR(255),
                    quotation_reference VARCHAR(255),
                    notes TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'submitted',
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )


@router.post("/rfqs", status_code=status.HTTP_201_CREATED)
def create_rfq(
    payload: RFQCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_rfq_tables()
    engine = get_engine()

    try:
        requisition_uuid = UUID(payload.requisition_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid requisition_id.",
        )

    now = datetime.now(timezone.utc)
    rfq_id = uuid4()

    with engine.begin() as conn:
        requisition = conn.execute(
            text(
                """
                SELECT id, pr_number, status
                FROM purchase_requisitions
                WHERE id = :requisition_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "requisition_id": requisition_uuid,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if not requisition:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Purchase requisition not found.",
            )

        if requisition["status"] != "approved":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only approved purchase requisitions can proceed to RFQ.",
            )

        rfq_number = (
            f"RFQ-{now.strftime('%Y%m%d')}-"
            f"{str(rfq_id).split('-')[0].upper()}"
        )

        conn.execute(
            text(
                """
                INSERT INTO supply_chain_rfqs (
                    id,
                    tenant_id,
                    requisition_id,
                    rfq_number,
                    rfq_title,
                    closing_date,
                    status,
                    notes,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :requisition_id,
                    :rfq_number,
                    :rfq_title,
                    :closing_date,
                    'open',
                    :notes,
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": rfq_id,
                "tenant_id": identity.tenant_id,
                "requisition_id": requisition_uuid,
                "rfq_number": rfq_number,
                "rfq_title": payload.rfq_title,
                "closing_date": payload.closing_date,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(rfq_id),
        "rfq_number": rfq_number,
        "requisition_id": str(requisition_uuid),
        "status": "open",
    }


@router.get("/rfqs")
def list_rfqs(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_rfq_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    r.id,
                    r.rfq_number,
                    r.requisition_id,
                    pr.pr_number,
                    r.rfq_title,
                    r.closing_date,
                    r.status,
                    r.notes,
                    r.created_by,
                    r.created_at,
                    r.updated_at
                FROM supply_chain_rfqs r
                JOIN purchase_requisitions pr
                  ON pr.id = r.requisition_id
                 AND pr.tenant_id = r.tenant_id
                WHERE r.tenant_id = :tenant_id
                ORDER BY r.created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [dict(row) for row in rows]


@router.post(
    "/rfqs/{rfq_id}/quotations",
    status_code=status.HTTP_201_CREATED,
)
def create_vendor_quotation(
    rfq_id: UUID,
    payload: VendorQuotationCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_rfq_tables()
    engine = get_engine()

    now = datetime.now(timezone.utc)
    quotation_id = uuid4()

    with engine.begin() as conn:
    rfq = conn.execute(
        text(
            """
            SELECT
                id,
                rfq_number,
                status,
                tenant_id
            FROM supply_chain_rfqs
            WHERE CAST(id AS TEXT) = CAST(:rfq_id AS TEXT)
            LIMIT 1
            """
        ),
        {
            "rfq_id": str(rfq_id),
        },
    ).mappings().first()

    if rfq is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RFQ not found.",
        )

    if str(rfq["tenant_id"]) != str(identity.tenant_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="RFQ does not belong to the authenticated tenant.",
        )

        if rfq["status"] != "open":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Quotation can only be submitted to an open RFQ.",
            )

        conn.execute(
            text(
                """
                INSERT INTO supply_chain_vendor_quotations (
                    id,
                    tenant_id,
                    rfq_id,
                    vendor_name,
                    vendor_code,
                    currency,
                    quotation_amount,
                    delivery_days,
                    payment_terms,
                    quotation_reference,
                    notes,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :rfq_id,
                    :vendor_name,
                    :vendor_code,
                    :currency,
                    :quotation_amount,
                    :delivery_days,
                    :payment_terms,
                    :quotation_reference,
                    :notes,
                    'submitted',
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": quotation_id,
                "tenant_id": identity.tenant_id,
                "rfq_id": rfq_id,
                "vendor_name": payload.vendor_name,
                "vendor_code": payload.vendor_code,
                "currency": payload.currency.upper(),
                "quotation_amount": payload.quotation_amount,
                "delivery_days": payload.delivery_days,
                "payment_terms": payload.payment_terms,
                "quotation_reference": payload.quotation_reference,
                "notes": payload.notes,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(quotation_id),
        "rfq_id": str(rfq_id),
        "vendor_name": payload.vendor_name,
        "currency": payload.currency.upper(),
        "quotation_amount": payload.quotation_amount,
        "status": "submitted",
    }


@router.get("/rfqs/{rfq_id}/quotations")
def list_vendor_quotations(
    rfq_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_rfq_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rfq = conn.execute(
            text(
                """
                SELECT id
                FROM supply_chain_rfqs
                WHERE id = :rfq_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "rfq_id": rfq_id,
                "tenant_id": identity.tenant_id,
            },
        ).first()

        if not rfq:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="RFQ not found.",
            )

        rows = conn.execute(
            text(
                """
                SELECT
                    id,
                    rfq_id,
                    vendor_name,
                    vendor_code,
                    currency,
                    quotation_amount,
                    delivery_days,
                    payment_terms,
                    quotation_reference,
                    notes,
                    status,
                    created_at,
                    updated_at
                FROM supply_chain_vendor_quotations
                WHERE tenant_id = :tenant_id
                  AND rfq_id = :rfq_id
                ORDER BY quotation_amount ASC, delivery_days ASC
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "rfq_id": rfq_id,
            },
        ).mappings().all()

    return [dict(row) for row in rows]

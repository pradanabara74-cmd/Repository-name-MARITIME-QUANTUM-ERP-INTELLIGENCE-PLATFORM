from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..core.database import get_engine
from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/inventory",
    tags=["Inventory & Vessel Stores Intelligence"],
)


# ============================================================
# DATA MODELS
# ============================================================

class InventoryItemCreate(BaseModel):
    item_code: str = Field(min_length=1, max_length=100)
    item_name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    category: str = Field(default="general", max_length=100)
    unit: str = Field(min_length=1, max_length=50)
    quantity: float = Field(default=0, ge=0)
    minimum_stock: float = Field(default=0, ge=0)
    maximum_stock: Optional[float] = Field(default=None, ge=0)
    reorder_level: float = Field(default=0, ge=0)
    unit_cost: float = Field(default=0, ge=0)
    currency: str = Field(default="USD", max_length=10)
    location: str = Field(min_length=1, max_length=255)
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    manufacturer: Optional[str] = None
    part_number: Optional[str] = None
    critical_spare: bool = False
    notes: Optional[str] = None


class StockMovementCreate(BaseModel):
    inventory_item_id: UUID
    movement_type: str = Field(min_length=1, max_length=50)
    quantity: float = Field(gt=0)
    reference: Optional[str] = None
    source_location: Optional[str] = None
    destination_location: Optional[str] = None
    department: Optional[str] = None
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    notes: Optional[str] = None


class StockTransferCreate(BaseModel):
    inventory_item_id: UUID
    quantity: float = Field(gt=0)
    destination_location: str = Field(min_length=1, max_length=255)
    destination_vessel_id: Optional[str] = None
    destination_vessel_name: Optional[str] = None
    reference: Optional[str] = None
    notes: Optional[str] = None


# ============================================================
# SERIALIZATION
# ============================================================

def serialize_row(row):
    result = dict(row)

    for key, value in result.items():
        if isinstance(value, UUID):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()

    return result


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_inventory_tables():
    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS inventory_items (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    item_code VARCHAR(100) NOT NULL,
                    item_name VARCHAR(255) NOT NULL,
                    description TEXT,
                    category VARCHAR(100) NOT NULL,
                    unit VARCHAR(50) NOT NULL,
                    quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
                    minimum_stock DOUBLE PRECISION NOT NULL DEFAULT 0,
                    maximum_stock DOUBLE PRECISION,
                    reorder_level DOUBLE PRECISION NOT NULL DEFAULT 0,
                    unit_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
                    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                    location VARCHAR(255) NOT NULL,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    manufacturer VARCHAR(255),
                    part_number VARCHAR(255),
                    critical_spare BOOLEAN NOT NULL DEFAULT FALSE,
                    status VARCHAR(50) NOT NULL DEFAULT 'active',
                    notes TEXT,
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (tenant_id, item_code, location)
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_inventory_items_tenant
                ON inventory_items (tenant_id)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_inventory_items_vessel
                ON inventory_items (tenant_id, vessel_id)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS inventory_movements (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    inventory_item_id UUID NOT NULL,
                    movement_type VARCHAR(50) NOT NULL,
                    quantity DOUBLE PRECISION NOT NULL,
                    balance_before DOUBLE PRECISION NOT NULL,
                    balance_after DOUBLE PRECISION NOT NULL,
                    reference VARCHAR(255),
                    source_location VARCHAR(255),
                    destination_location VARCHAR(255),
                    department VARCHAR(100),
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
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
                CREATE INDEX IF NOT EXISTS idx_inventory_movements_item
                ON inventory_movements (
                    tenant_id,
                    inventory_item_id,
                    created_at
                )
                """
            )
        )

  # ============================================================
# INVENTORY ITEM MANAGEMENT
# ============================================================

@router.get(
    "/items",
    summary="List Inventory Items",
)
def list_inventory_items(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_inventory_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM inventory_items
                WHERE tenant_id = :tenant_id
                  AND status = 'active'
                ORDER BY item_name, location
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/items",
    status_code=status.HTTP_201_CREATED,
    summary="Create Inventory Item",
)
def create_inventory_item(
    payload: InventoryItemCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_inventory_tables()

    engine = get_engine()
    now = datetime.now(timezone.utc)
    item_id = uuid4()

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT id
                FROM inventory_items
                WHERE tenant_id = :tenant_id
                  AND item_code = :item_code
                  AND location = :location
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "item_code": payload.item_code,
                "location": payload.location,
            },
        ).first()

        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Inventory item already exists at this location.",
            )

        conn.execute(
            text(
                """
                INSERT INTO inventory_items (
                    id,
                    tenant_id,
                    item_code,
                    item_name,
                    description,
                    category,
                    unit,
                    quantity,
                    minimum_stock,
                    maximum_stock,
                    reorder_level,
                    unit_cost,
                    currency,
                    location,
                    vessel_id,
                    vessel_name,
                    manufacturer,
                    part_number,
                    critical_spare,
                    status,
                    notes,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :item_code,
                    :item_name,
                    :description,
                    :category,
                    :unit,
                    :quantity,
                    :minimum_stock,
                    :maximum_stock,
                    :reorder_level,
                    :unit_cost,
                    :currency,
                    :location,
                    :vessel_id,
                    :vessel_name,
                    :manufacturer,
                    :part_number,
                    :critical_spare,
                    'active',
                    :notes,
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": item_id,
                "tenant_id": identity.tenant_id,
                "item_code": payload.item_code,
                "item_name": payload.item_name,
                "description": payload.description,
                "category": payload.category,
                "unit": payload.unit,
                "quantity": payload.quantity,
                "minimum_stock": payload.minimum_stock,
                "maximum_stock": payload.maximum_stock,
                "reorder_level": payload.reorder_level,
                "unit_cost": payload.unit_cost,
                "currency": payload.currency.upper(),
                "location": payload.location,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "manufacturer": payload.manufacturer,
                "part_number": payload.part_number,
                "critical_spare": payload.critical_spare,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

        if payload.quantity > 0:
            conn.execute(
                text(
                    """
                    INSERT INTO inventory_movements (
                        id,
                        tenant_id,
                        inventory_item_id,
                        movement_type,
                        quantity,
                        balance_before,
                        balance_after,
                        reference,
                        source_location,
                        destination_location,
                        department,
                        vessel_id,
                        vessel_name,
                        notes,
                        created_by,
                        created_at
                    )
                    VALUES (
                        :id,
                        :tenant_id,
                        :inventory_item_id,
                        'opening_balance',
                        :quantity,
                        0,
                        :balance_after,
                        'INITIAL-STOCK',
                        NULL,
                        :destination_location,
                        NULL,
                        :vessel_id,
                        :vessel_name,
                        'Initial inventory balance',
                        :created_by,
                        :created_at
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "tenant_id": identity.tenant_id,
                    "inventory_item_id": item_id,
                    "quantity": payload.quantity,
                    "balance_after": payload.quantity,
                    "destination_location": payload.location,
                    "vessel_id": payload.vessel_id,
                    "vessel_name": payload.vessel_name,
                    "created_by": identity.user_id,
                    "created_at": now,
                },
            )

    return {
        "id": str(item_id),
        "item_code": payload.item_code,
        "item_name": payload.item_name,
        "quantity": payload.quantity,
        "unit": payload.unit,
        "location": payload.location,
        "critical_spare": payload.critical_spare,
        "status": "active",
    }


@router.get(
    "/items/{inventory_item_id}",
    summary="Get Inventory Item",
)
def get_inventory_item(
    inventory_item_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_inventory_tables()
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT *
                FROM inventory_items
                WHERE id = :inventory_item_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "inventory_item_id": inventory_item_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventory item not found.",
        )

    return serialize_row(row)


# ============================================================
# STOCK RECEIPT / ISSUE
# ============================================================

@router.post(
    "/movements",
    status_code=status.HTTP_201_CREATED,
    summary="Post Inventory Stock Movement",
)
def create_stock_movement(
    payload: StockMovementCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_inventory_tables()

    movement_type = payload.movement_type.strip().lower()

    inbound_types = {
        "receipt",
        "return",
        "adjustment_in",
    }

    outbound_types = {
        "issue",
        "consumption",
        "adjustment_out",
    }

    if movement_type not in inbound_types | outbound_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid stock movement type.",
        )

    engine = get_engine()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        item = conn.execute(
            text(
                """
                SELECT *
                FROM inventory_items
                WHERE id = :inventory_item_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {
                "inventory_item_id": payload.inventory_item_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Inventory item not found.",
            )

        balance_before = float(item["quantity"])

        if movement_type in inbound_types:
            balance_after = balance_before + payload.quantity
        else:
            if payload.quantity > balance_before:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Insufficient inventory stock.",
                )

            balance_after = balance_before - payload.quantity

        conn.execute(
            text(
                """
                UPDATE inventory_items
                SET quantity = :quantity,
                    updated_at = :updated_at
                WHERE id = :inventory_item_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "quantity": balance_after,
                "updated_at": now,
                "inventory_item_id": payload.inventory_item_id,
                "tenant_id": identity.tenant_id,
            },
        )

        movement_id = uuid4()

        conn.execute(
            text(
                """
                INSERT INTO inventory_movements (
                    id,
                    tenant_id,
                    inventory_item_id,
                    movement_type,
                    quantity,
                    balance_before,
                    balance_after,
                    reference,
                    source_location,
                    destination_location,
                    department,
                    vessel_id,
                    vessel_name,
                    notes,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :inventory_item_id,
                    :movement_type,
                    :quantity,
                    :balance_before,
                    :balance_after,
                    :reference,
                    :source_location,
                    :destination_location,
                    :department,
                    :vessel_id,
                    :vessel_name,
                    :notes,
                    :created_by,
                    :created_at
                )
                """
            ),
            {
                "id": movement_id,
                "tenant_id": identity.tenant_id,
                "inventory_item_id": payload.inventory_item_id,
                "movement_type": movement_type,
                "quantity": payload.quantity,
                "balance_before": balance_before,
                "balance_after": balance_after,
                "reference": payload.reference,
                "source_location": payload.source_location,
                "destination_location": payload.destination_location,
                "department": payload.department,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

    return {
        "id": str(movement_id),
        "inventory_item_id": str(payload.inventory_item_id),
        "movement_type": movement_type,
        "quantity": payload.quantity,
        "balance_before": balance_before,
        "balance_after": balance_after,
        "status": "posted",
    }

# ============================================================
# INVENTORY TRANSFER MANAGEMENT
# ============================================================

@router.post(
    "/transfers",
    status_code=status.HTTP_201_CREATED,
    summary="Transfer Inventory Stock",
)
def transfer_inventory_stock(
    payload: StockTransferCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_inventory_tables()

    if payload.source_inventory_item_id == payload.destination_inventory_item_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Source and destination inventory items must be different.",
        )

    engine = get_engine()
    now = datetime.now(timezone.utc)
    transfer_id = uuid4()
    transfer_reference = (
        f"TRF-{now.strftime('%Y%m%d')}-"
        f"{str(transfer_id).split('-')[0].upper()}"
    )

    with engine.begin() as conn:
        source = conn.execute(
            text(
                """
                SELECT *
                FROM inventory_items
                WHERE id = :item_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {
                "item_id": payload.source_inventory_item_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        destination = conn.execute(
            text(
                """
                SELECT *
                FROM inventory_items
                WHERE id = :item_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {
                "item_id": payload.destination_inventory_item_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if source is None or destination is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Source or destination inventory item not found.",
            )

        if source["item_code"] != destination["item_code"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Inventory transfer requires matching item codes.",
            )

        source_before = float(source["quantity"])
        destination_before = float(destination["quantity"])

        if payload.quantity > source_before:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Insufficient stock at source location.",
            )

        source_after = source_before - payload.quantity
        destination_after = destination_before + payload.quantity

        conn.execute(
            text(
                """
                UPDATE inventory_items
                SET quantity = :quantity,
                    updated_at = :updated_at
                WHERE id = :item_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "quantity": source_after,
                "updated_at": now,
                "item_id": payload.source_inventory_item_id,
                "tenant_id": identity.tenant_id,
            },
        )

        conn.execute(
            text(
                """
                UPDATE inventory_items
                SET quantity = :quantity,
                    updated_at = :updated_at
                WHERE id = :item_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "quantity": destination_after,
                "updated_at": now,
                "item_id": payload.destination_inventory_item_id,
                "tenant_id": identity.tenant_id,
            },
        )

        conn.execute(
            text(
                """
                INSERT INTO inventory_movements (
                    id,
                    tenant_id,
                    inventory_item_id,
                    movement_type,
                    quantity,
                    balance_before,
                    balance_after,
                    reference,
                    source_location,
                    destination_location,
                    department,
                    vessel_id,
                    vessel_name,
                    notes,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :inventory_item_id,
                    'transfer_out',
                    :quantity,
                    :balance_before,
                    :balance_after,
                    :reference,
                    :source_location,
                    :destination_location,
                    :department,
                    :vessel_id,
                    :vessel_name,
                    :notes,
                    :created_by,
                    :created_at
                )
                """
            ),
            {
                "id": uuid4(),
                "tenant_id": identity.tenant_id,
                "inventory_item_id": payload.source_inventory_item_id,
                "quantity": payload.quantity,
                "balance_before": source_before,
                "balance_after": source_after,
                "reference": transfer_reference,
                "source_location": source["location"],
                "destination_location": destination["location"],
                "department": payload.department,
                "vessel_id": source["vessel_id"],
                "vessel_name": source["vessel_name"],
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

        conn.execute(
            text(
                """
                INSERT INTO inventory_movements (
                    id,
                    tenant_id,
                    inventory_item_id,
                    movement_type,
                    quantity,
                    balance_before,
                    balance_after,
                    reference,
                    source_location,
                    destination_location,
                    department,
                    vessel_id,
                    vessel_name,
                    notes,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :inventory_item_id,
                    'transfer_in',
                    :quantity,
                    :balance_before,
                    :balance_after,
                    :reference,
                    :source_location,
                    :destination_location,
                    :department,
                    :vessel_id,
                    :vessel_name,
                    :notes,
                    :created_by,
                    :created_at
                )
                """
            ),
            {
                "id": uuid4(),
                "tenant_id": identity.tenant_id,
                "inventory_item_id": payload.destination_inventory_item_id,
                "quantity": payload.quantity,
                "balance_before": destination_before,
                "balance_after": destination_after,
                "reference": transfer_reference,
                "source_location": source["location"],
                "destination_location": destination["location"],
                "department": payload.department,
                "vessel_id": destination["vessel_id"],
                "vessel_name": destination["vessel_name"],
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

    return {
        "transfer_id": str(transfer_id),
        "transfer_reference": transfer_reference,
        "item_code": source["item_code"],
        "quantity": payload.quantity,
        "source_location": source["location"],
        "destination_location": destination["location"],
        "source_balance": source_after,
        "destination_balance": destination_after,
        "status": "completed",
    }


# ============================================================
# INVENTORY MOVEMENT HISTORY
# ============================================================

@router.get(
    "/items/{inventory_item_id}/movements",
    summary="Inventory Movement History",
)
def inventory_movement_history(
    inventory_item_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_inventory_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM inventory_movements
                WHERE tenant_id = :tenant_id
                  AND inventory_item_id = :inventory_item_id
                ORDER BY created_at DESC
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "inventory_item_id": inventory_item_id,
            },
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# ============================================================
# INVENTORY & VESSEL STORES INTELLIGENCE
# ============================================================

@router.get(
    "/dashboard",
    summary="Inventory & Vessel Stores Intelligence Dashboard",
)
def inventory_intelligence_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_inventory_tables()
    engine = get_engine()

    with engine.begin() as conn:
        summary = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_items,
                    COALESCE(SUM(quantity), 0) AS total_quantity,
                    COALESCE(SUM(quantity * unit_cost), 0) AS stock_value,
                    COUNT(*) FILTER (
                        WHERE quantity <= minimum_stock
                    ) AS low_stock_items,
                    COUNT(*) FILTER (
                        WHERE quantity = 0
                    ) AS out_of_stock_items,
                    COUNT(*) FILTER (
                        WHERE critical_spare = TRUE
                    ) AS critical_spares
                FROM inventory_items
                WHERE tenant_id = :tenant_id
                  AND status = 'active'
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        low_stock = conn.execute(
            text(
                """
                SELECT
                    id,
                    item_code,
                    item_name,
                    quantity,
                    minimum_stock,
                    reorder_level,
                    unit,
                    location,
                    vessel_name,
                    critical_spare
                FROM inventory_items
                WHERE tenant_id = :tenant_id
                  AND status = 'active'
                  AND quantity <= minimum_stock
                ORDER BY critical_spare DESC,
                         quantity ASC,
                         item_name ASC
                LIMIT 100
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        critical = conn.execute(
            text(
                """
                SELECT
                    id,
                    item_code,
                    item_name,
                    quantity,
                    minimum_stock,
                    reorder_level,
                    unit,
                    location,
                    vessel_name
                FROM inventory_items
                WHERE tenant_id = :tenant_id
                  AND status = 'active'
                  AND critical_spare = TRUE
                ORDER BY quantity ASC, item_name ASC
                LIMIT 100
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        recent_movements = conn.execute(
            text(
                """
                SELECT
                    m.id,
                    m.inventory_item_id,
                    i.item_code,
                    i.item_name,
                    m.movement_type,
                    m.quantity,
                    m.balance_before,
                    m.balance_after,
                    m.reference,
                    m.source_location,
                    m.destination_location,
                    m.department,
                    m.vessel_name,
                    m.created_at
                FROM inventory_movements m
                JOIN inventory_items i
                  ON i.id = m.inventory_item_id
                 AND i.tenant_id = m.tenant_id
                WHERE m.tenant_id = :tenant_id
                ORDER BY m.created_at DESC
                LIMIT 100
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "total_items": summary["total_items"],
        "total_quantity": summary["total_quantity"],
        "stock_value": summary["stock_value"],
        "low_stock_items": summary["low_stock_items"],
        "out_of_stock_items": summary["out_of_stock_items"],
        "critical_spares": summary["critical_spares"],
        "low_stock_alerts": [
            serialize_row(row) for row in low_stock
        ],
        "critical_spare_monitor": [
            serialize_row(row) for row in critical
        ],
        "recent_stock_movements": [
            serialize_row(row) for row in recent_movements
        ],
    }

# ============================================================
# REORDER INTELLIGENCE
# ============================================================

@router.get(
    "/reorder-alerts",
    summary="Inventory Reorder Intelligence",
)
def inventory_reorder_alerts(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_inventory_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    id,
                    item_code,
                    item_name,
                    category,
                    quantity,
                    minimum_stock,
                    reorder_level,
                    maximum_stock,
                    unit,
                    unit_cost,
                    currency,
                    location,
                    vessel_id,
                    vessel_name,
                    critical_spare,
                    CASE
                        WHEN quantity = 0 THEN 'OUT_OF_STOCK'
                        WHEN quantity <= minimum_stock
                            THEN 'CRITICAL'
                        WHEN quantity <= reorder_level
                            THEN 'REORDER'
                        ELSE 'NORMAL'
                    END AS stock_priority,
                    GREATEST(maximum_stock - quantity, 0)
                        AS suggested_order_quantity
                FROM inventory_items
                WHERE tenant_id = :tenant_id
                  AND status = 'active'
                  AND (
                        quantity <= reorder_level
                        OR quantity <= minimum_stock
                      )
                ORDER BY
                    CASE
                        WHEN quantity = 0 THEN 1
                        WHEN quantity <= minimum_stock THEN 2
                        ELSE 3
                    END,
                    critical_spare DESC,
                    item_name ASC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "alert_count": len(rows),
        "reorder_alerts": [
            serialize_row(row) for row in rows
        ],
    }


# ============================================================
# INVENTORY VALUATION
# ============================================================

@router.get(
    "/valuation",
    summary="Inventory Valuation Intelligence",
)
def inventory_valuation(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_inventory_tables()
    engine = get_engine()

    with engine.begin() as conn:
        overall = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_items,
                    COALESCE(SUM(quantity), 0) AS total_quantity,
                    COALESCE(SUM(quantity * unit_cost), 0)
                        AS total_inventory_value
                FROM inventory_items
                WHERE tenant_id = :tenant_id
                  AND status = 'active'
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        by_location = conn.execute(
            text(
                """
                SELECT
                    location,
                    COUNT(*) AS total_items,
                    COALESCE(SUM(quantity), 0) AS total_quantity,
                    COALESCE(SUM(quantity * unit_cost), 0)
                        AS inventory_value
                FROM inventory_items
                WHERE tenant_id = :tenant_id
                  AND status = 'active'
                GROUP BY location
                ORDER BY inventory_value DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        by_vessel = conn.execute(
            text(
                """
                SELECT
                    vessel_id,
                    vessel_name,
                    COUNT(*) AS total_items,
                    COALESCE(SUM(quantity), 0) AS total_quantity,
                    COALESCE(SUM(quantity * unit_cost), 0)
                        AS inventory_value
                FROM inventory_items
                WHERE tenant_id = :tenant_id
                  AND status = 'active'
                  AND vessel_name IS NOT NULL
                GROUP BY vessel_id, vessel_name
                ORDER BY inventory_value DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "total_items": overall["total_items"],
        "total_quantity": overall["total_quantity"],
        "total_inventory_value":
            overall["total_inventory_value"],
        "valuation_by_location": [
            serialize_row(row) for row in by_location
        ],
        "valuation_by_vessel": [
            serialize_row(row) for row in by_vessel
        ],
    }


# ============================================================
# INVENTORY AUDIT TRAIL
# ============================================================

@router.get(
    "/audit-trail",
    summary="Inventory Audit Trail",
)
def inventory_audit_trail(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_inventory_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    m.id,
                    m.inventory_item_id,
                    i.item_code,
                    i.item_name,
                    m.movement_type,
                    m.quantity,
                    m.balance_before,
                    m.balance_after,
                    m.reference,
                    m.source_location,
                    m.destination_location,
                    m.department,
                    m.vessel_id,
                    m.vessel_name,
                    m.notes,
                    m.created_by,
                    m.created_at
                FROM inventory_movements m
                JOIN inventory_items i
                  ON i.id = m.inventory_item_id
                 AND i.tenant_id = m.tenant_id
                WHERE m.tenant_id = :tenant_id
                ORDER BY m.created_at DESC
                LIMIT 1000
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]

from datetime import date, datetime, timezone
from decimal import Decimal
from functools import lru_cache
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from ..core.database import get_database_url
from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/commercial",
    tags=["Commercial & Chartering Intelligence"],
)


@lru_cache(maxsize=1)
def get_engine():
    # Satu engine/connection pool dipakai ulang (sebelumnya membuat engine baru
    # di setiap request sehingga koneksi database cepat habis).
    return create_engine(
        get_database_url(),
        pool_pre_ping=True,
    )


def utc_now():
    return datetime.now(timezone.utc)


def serialize_row(row):
    data = dict(row)

    for key, value in list(data.items()):
        if isinstance(value, UUID):
            data[key] = str(value)
        elif isinstance(value, (datetime, date)):
            data[key] = value.isoformat()
        elif isinstance(value, Decimal):
            data[key] = float(value)

    return data


# ============================================================
# REQUEST MODELS
# ============================================================

class ChartererCreate(BaseModel):
    charterer_code: str = Field(min_length=1, max_length=100)
    charterer_name: str = Field(min_length=1, max_length=255)
    country: Optional[str] = None
    contact_person: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    credit_limit: float = Field(default=0, ge=0)
    currency: str = Field(default="USD", max_length=10)
    notes: Optional[str] = None


class CharterPartyCreate(BaseModel):
    charterer_id: UUID
    vessel_id: Optional[str] = None
    vessel_name: str = Field(min_length=1, max_length=255)
    contract_type: str = Field(min_length=1, max_length=50)
    contract_number: str = Field(min_length=1, max_length=100)
    fixture_date: str
    commencement_date: str
    completion_date: Optional[str] = None
    currency: str = Field(default="USD", max_length=10)
    hire_rate: float = Field(default=0, ge=0)
    freight_rate: float = Field(default=0, ge=0)
    laytime_hours: float = Field(default=0, ge=0)
    demurrage_rate: float = Field(default=0, ge=0)
    despatch_rate: float = Field(default=0, ge=0)
    trading_area: Optional[str] = None
    cargo_description: Optional[str] = None
    notes: Optional[str] = None


class FixtureCreate(BaseModel):
    charter_party_id: UUID
    fixture_reference: str = Field(min_length=1, max_length=100)
    broker_name: Optional[str] = None
    commission_percent: float = Field(default=0, ge=0)
    agreed_rate: float = Field(default=0, ge=0)
    currency: str = Field(default="USD", max_length=10)
    notes: Optional[str] = None


class CommercialVoyageCreate(BaseModel):
    charter_party_id: UUID
    voyage_number: str = Field(min_length=1, max_length=100)
    vessel_id: Optional[str] = None
    vessel_name: str = Field(min_length=1, max_length=255)
    load_port: str = Field(min_length=1, max_length=255)
    discharge_port: str = Field(min_length=1, max_length=255)
    cargo_description: Optional[str] = None
    cargo_quantity: float = Field(default=0, ge=0)
    cargo_unit: str = Field(default="MT", max_length=30)
    planned_departure: Optional[str] = None
    planned_arrival: Optional[str] = None
    revenue: float = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0, ge=0)
    currency: str = Field(default="USD", max_length=10)
    notes: Optional[str] = None


class VoyageFinancialUpdate(BaseModel):
    actual_revenue: float = Field(default=0, ge=0)
    actual_cost: float = Field(default=0, ge=0)
    bunker_cost: float = Field(default=0, ge=0)
    port_cost: float = Field(default=0, ge=0)
    agency_cost: float = Field(default=0, ge=0)
    other_cost: float = Field(default=0, ge=0)
    notes: Optional[str] = None


class LaytimeCreate(BaseModel):
    commercial_voyage_id: UUID
    allowed_laytime_hours: float = Field(default=0, ge=0)
    actual_laytime_hours: float = Field(default=0, ge=0)
    demurrage_rate: float = Field(default=0, ge=0)
    despatch_rate: float = Field(default=0, ge=0)
    currency: str = Field(default="USD", max_length=10)
    notes: Optional[str] = None


class VoyageCommercialCreate(BaseModel):
    charter_party_id: UUID
    voyage_number: str = Field(min_length=1, max_length=100)
    load_port: str = Field(min_length=1, max_length=255)
    discharge_port: str = Field(min_length=1, max_length=255)
    cargo_description: Optional[str] = None
    cargo_quantity: float = Field(default=0, ge=0)
    cargo_unit: str = Field(default="MT", max_length=30)
    departure_date: Optional[str] = None
    arrival_date: Optional[str] = None
    estimated_revenue: float = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0, ge=0)
    notes: Optional[str] = None


class VoyageRevenueCreate(BaseModel):
    revenue_type: str = Field(default="freight", min_length=1, max_length=50)
    reference: Optional[str] = None
    amount: float = Field(ge=0)
    revenue_date: Optional[date] = None
    notes: Optional[str] = None


class DemurrageDespatchCreate(BaseModel):
    allowed_laytime_hours: Optional[float] = Field(default=None, ge=0)
    used_laytime_hours: float = Field(ge=0)
    demurrage_rate: Optional[float] = Field(default=None, ge=0)
    despatch_rate: Optional[float] = Field(default=None, ge=0)
    calculation_date: Optional[date] = None
    notes: Optional[str] = None


class ContractDecision(BaseModel):
    decision: str = Field(min_length=1, max_length=30)
    remarks: Optional[str] = None


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

_TABLES_READY = False


def init_commercial_tables():
    global _TABLES_READY

    if _TABLES_READY:
        return

    engine = get_engine()

    with engine.begin() as conn:

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS commercial_charterers (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    charterer_code VARCHAR(100) NOT NULL,
                    charterer_name VARCHAR(255) NOT NULL,
                    country VARCHAR(100),
                    contact_person VARCHAR(255),
                    email VARCHAR(255),
                    phone VARCHAR(100),
                    credit_limit DOUBLE PRECISION NOT NULL DEFAULT 0,
                    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                    notes TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'active',
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (tenant_id, charterer_code)
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS commercial_charter_parties (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    charterer_id UUID NOT NULL,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255) NOT NULL,
                    contract_type VARCHAR(50) NOT NULL,
                    contract_number VARCHAR(100) NOT NULL,
                    fixture_date VARCHAR(50) NOT NULL,
                    commencement_date VARCHAR(50) NOT NULL,
                    completion_date VARCHAR(50),
                    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                    hire_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                    freight_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                    laytime_hours DOUBLE PRECISION NOT NULL DEFAULT 0,
                    demurrage_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                    despatch_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                    trading_area TEXT,
                    cargo_description TEXT,
                    notes TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'draft',
                    approved_by VARCHAR(255),
                    approved_at TIMESTAMPTZ,
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (tenant_id, contract_number)
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS commercial_fixtures (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    charter_party_id UUID NOT NULL,
                    fixture_reference VARCHAR(100) NOT NULL,
                    broker_name VARCHAR(255),
                    commission_percent DOUBLE PRECISION NOT NULL DEFAULT 0,
                    agreed_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                    notes TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'fixed',
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (tenant_id, fixture_reference)
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS commercial_voyages (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    charter_party_id UUID NOT NULL,
                    voyage_number VARCHAR(100) NOT NULL,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255) NOT NULL,
                    load_port VARCHAR(255) NOT NULL,
                    discharge_port VARCHAR(255) NOT NULL,
                    cargo_description TEXT,
                    cargo_quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
                    cargo_unit VARCHAR(30) NOT NULL DEFAULT 'MT',
                    planned_departure VARCHAR(50),
                    planned_arrival VARCHAR(50),
                    revenue DOUBLE PRECISION NOT NULL DEFAULT 0,
                    estimated_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
                    actual_revenue DOUBLE PRECISION NOT NULL DEFAULT 0,
                    actual_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
                    bunker_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
                    port_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
                    agency_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
                    other_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
                    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                    notes TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'planned',
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (tenant_id, voyage_number)
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS commercial_laytime (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    commercial_voyage_id UUID NOT NULL,
                    allowed_laytime_hours DOUBLE PRECISION NOT NULL DEFAULT 0,
                    actual_laytime_hours DOUBLE PRECISION NOT NULL DEFAULT 0,
                    demurrage_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                    despatch_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                    demurrage_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
                    despatch_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
                    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                    notes TEXT,
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
                CREATE TABLE IF NOT EXISTS commercial_audit_log (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    action VARCHAR(100) NOT NULL,
                    entity_type VARCHAR(100) NOT NULL,
                    entity_id VARCHAR(255),
                    details TEXT,
                    performed_by VARCHAR(255),
                    performed_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_commercial_cp_tenant_status
                ON commercial_charter_parties (tenant_id, status)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_commercial_voyage_tenant_status
                ON commercial_voyages (tenant_id, status)
                """
            )
        )

        # Kolom yang dipakai endpoint voyage tetapi belum ada di skema lama.
        # ADD COLUMN IF NOT EXISTS aman untuk tabel yang sudah ada di database.
        for ddl in (
            "ALTER TABLE commercial_voyages ADD COLUMN IF NOT EXISTS charterer_id UUID",
            "ALTER TABLE commercial_voyages ADD COLUMN IF NOT EXISTS departure_date VARCHAR(50)",
            "ALTER TABLE commercial_voyages ADD COLUMN IF NOT EXISTS arrival_date VARCHAR(50)",
            "ALTER TABLE commercial_voyages ADD COLUMN IF NOT EXISTS estimated_revenue DOUBLE PRECISION NOT NULL DEFAULT 0",
        ):
            conn.execute(text(ddl))

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS commercial_voyage_revenue (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    voyage_id UUID NOT NULL,
                    revenue_type VARCHAR(50) NOT NULL,
                    reference VARCHAR(255),
                    amount DOUBLE PRECISION NOT NULL DEFAULT 0,
                    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                    revenue_date DATE,
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
                CREATE TABLE IF NOT EXISTS commercial_demurrage_despatch (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    voyage_id UUID NOT NULL,
                    allowed_laytime_hours DOUBLE PRECISION NOT NULL DEFAULT 0,
                    used_laytime_hours DOUBLE PRECISION NOT NULL DEFAULT 0,
                    chargeable_hours DOUBLE PRECISION NOT NULL DEFAULT 0,
                    result_type VARCHAR(30) NOT NULL,
                    rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                    amount DOUBLE PRECISION NOT NULL DEFAULT 0,
                    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                    calculation_date DATE,
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
                CREATE INDEX IF NOT EXISTS idx_commercial_revenue_voyage
                ON commercial_voyage_revenue (tenant_id, voyage_id)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_commercial_dd_voyage
                ON commercial_demurrage_despatch (tenant_id, voyage_id)
                """
            )
        )

    _TABLES_READY = True


def write_audit(
    conn,
    identity: TokenIdentity,
    action: str,
    entity_type: str,
    entity_id,
    details: Optional[str] = None,
):
    conn.execute(
        text(
            """
            INSERT INTO commercial_audit_log (
                id,
                tenant_id,
                action,
                entity_type,
                entity_id,
                details,
                performed_by,
                performed_at
            )
            VALUES (
                :id,
                :tenant_id,
                :action,
                :entity_type,
                :entity_id,
                :details,
                :performed_by,
                :performed_at
            )
            """
        ),
        {
            "id": uuid4(),
            "tenant_id": identity.tenant_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id) if entity_id else None,
            "details": details,
            "performed_by": identity.user_id,
            "performed_at": utc_now(),
        },
    )

# ============================================================
# CHARTERER MANAGEMENT
# ============================================================

@router.post(
    "/charterers",
    status_code=status.HTTP_201_CREATED,
    summary="Create Charterer",
)
def create_charterer(
    payload: ChartererCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()
    now = utc_now()
    charterer_id = uuid4()

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT id
                FROM commercial_charterers
                WHERE tenant_id = :tenant_id
                  AND charterer_code = :charterer_code
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "charterer_code": payload.charterer_code.strip(),
            },
        ).first()

        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Charterer code already exists.",
            )

        conn.execute(
            text(
                """
                INSERT INTO commercial_charterers (
                    id,
                    tenant_id,
                    charterer_code,
                    charterer_name,
                    country,
                    contact_person,
                    email,
                    phone,
                    credit_limit,
                    currency,
                    notes,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :charterer_code,
                    :charterer_name,
                    :country,
                    :contact_person,
                    :email,
                    :phone,
                    :credit_limit,
                    :currency,
                    :notes,
                    'active',
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": charterer_id,
                "tenant_id": identity.tenant_id,
                "charterer_code": payload.charterer_code.strip(),
                "charterer_name": payload.charterer_name.strip(),
                "country": payload.country,
                "contact_person": payload.contact_person,
                "email": payload.email,
                "phone": payload.phone,
                "credit_limit": payload.credit_limit,
                "currency": payload.currency.upper(),
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

        write_audit(
            conn,
            identity,
            "CREATE",
            "CHARTERER",
            charterer_id,
            f"Created charterer {payload.charterer_code.strip()}",
        )

    return {
        "id": str(charterer_id),
        "charterer_code": payload.charterer_code.strip(),
        "charterer_name": payload.charterer_name.strip(),
        "currency": payload.currency.upper(),
        "status": "active",
    }


@router.get(
    "/charterers",
    summary="List Charterers",
)
def list_charterers(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM commercial_charterers
                WHERE tenant_id = :tenant_id
                ORDER BY charterer_name ASC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.get(
    "/charterers/{charterer_id}",
    summary="Get Charterer",
)
def get_charterer(
    charterer_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT *
                FROM commercial_charterers
                WHERE id = :charterer_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "charterer_id": charterer_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Charterer not found.",
        )

    return serialize_row(row)


# ============================================================
# CHARTER PARTY MANAGEMENT
# ============================================================

@router.post(
    "/charter-parties",
    status_code=status.HTTP_201_CREATED,
    summary="Create Charter Party",
)
def create_charter_party(
    payload: CharterPartyCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()
    now = utc_now()
    charter_party_id = uuid4()

    contract_type = payload.contract_type.strip().lower()

    valid_contract_types = {
        "time_charter",
        "voyage_charter",
        "bareboat_charter",
        "contract_of_affreightment",
        "spot",
        "other",
    }

    if contract_type not in valid_contract_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid charter party contract type.",
        )

    with engine.begin() as conn:
        charterer = conn.execute(
            text(
                """
                SELECT id, charterer_name, status
                FROM commercial_charterers
                WHERE id = :charterer_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "charterer_id": payload.charterer_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if charterer is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Charterer not found.",
            )

        if charterer["status"] != "active":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Charterer is not active.",
            )

        existing = conn.execute(
            text(
                """
                SELECT id
                FROM commercial_charter_parties
                WHERE tenant_id = :tenant_id
                  AND contract_number = :contract_number
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "contract_number": payload.contract_number.strip(),
            },
        ).first()

        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Charter Party contract number already exists.",
            )

        conn.execute(
            text(
                """
                INSERT INTO commercial_charter_parties (
                    id,
                    tenant_id,
                    charterer_id,
                    vessel_id,
                    vessel_name,
                    contract_type,
                    contract_number,
                    fixture_date,
                    commencement_date,
                    completion_date,
                    currency,
                    hire_rate,
                    freight_rate,
                    laytime_hours,
                    demurrage_rate,
                    despatch_rate,
                    trading_area,
                    cargo_description,
                    notes,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :charterer_id,
                    :vessel_id,
                    :vessel_name,
                    :contract_type,
                    :contract_number,
                    :fixture_date,
                    :commencement_date,
                    :completion_date,
                    :currency,
                    :hire_rate,
                    :freight_rate,
                    :laytime_hours,
                    :demurrage_rate,
                    :despatch_rate,
                    :trading_area,
                    :cargo_description,
                    :notes,
                    'draft',
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": charter_party_id,
                "tenant_id": identity.tenant_id,
                "charterer_id": payload.charterer_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name.strip(),
                "contract_type": contract_type,
                "contract_number": payload.contract_number.strip(),
                "fixture_date": payload.fixture_date,
                "commencement_date": payload.commencement_date,
                "completion_date": payload.completion_date,
                "currency": payload.currency.upper(),
                "hire_rate": payload.hire_rate,
                "freight_rate": payload.freight_rate,
                "laytime_hours": payload.laytime_hours,
                "demurrage_rate": payload.demurrage_rate,
                "despatch_rate": payload.despatch_rate,
                "trading_area": payload.trading_area,
                "cargo_description": payload.cargo_description,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

        write_audit(
            conn,
            identity,
            "CREATE",
            "CHARTER_PARTY",
            charter_party_id,
            f"Created contract {payload.contract_number.strip()}",
        )

    return {
        "id": str(charter_party_id),
        "contract_number": payload.contract_number.strip(),
        "contract_type": contract_type,
        "charterer_id": str(payload.charterer_id),
        "vessel_name": payload.vessel_name.strip(),
        "currency": payload.currency.upper(),
        "status": "draft",
    }


@router.get(
    "/charter-parties",
    summary="List Charter Parties",
)
def list_charter_parties(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    cp.*,
                    c.charterer_code,
                    c.charterer_name
                FROM commercial_charter_parties cp
                JOIN commercial_charterers c
                  ON c.id = cp.charterer_id
                 AND c.tenant_id = cp.tenant_id
                WHERE cp.tenant_id = :tenant_id
                ORDER BY cp.created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.get(
    "/charter-parties/{charter_party_id}",
    summary="Get Charter Party",
)
def get_charter_party(
    charter_party_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT
                    cp.*,
                    c.charterer_code,
                    c.charterer_name
                FROM commercial_charter_parties cp
                JOIN commercial_charterers c
                  ON c.id = cp.charterer_id
                 AND c.tenant_id = cp.tenant_id
                WHERE cp.id = :charter_party_id
                  AND cp.tenant_id = :tenant_id
                """
            ),
            {
                "charter_party_id": charter_party_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Charter Party not found.",
        )

    return serialize_row(row)


# ============================================================
# CHARTER PARTY APPROVAL WORKFLOW
# ============================================================

@router.post(
    "/charter-parties/{charter_party_id}/decision",
    summary="Charter Party Approval Decision",
)
def charter_party_decision(
    charter_party_id: UUID,
    payload: ContractDecision,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()
    now = utc_now()

    decision = payload.decision.strip().lower()

    valid_decisions = {
        "submit",
        "approve",
        "reject",
        "activate",
        "complete",
        "cancel",
    }

    if decision not in valid_decisions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Charter Party decision.",
        )

    with engine.begin() as conn:
        contract = conn.execute(
            text(
                """
                SELECT *
                FROM commercial_charter_parties
                WHERE id = :charter_party_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {
                "charter_party_id": charter_party_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if contract is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Charter Party not found.",
            )

        current_status = contract["status"]

        transitions = {
            "draft": {
                "submit": "pending_approval",
                "cancel": "cancelled",
            },
            "pending_approval": {
                "approve": "approved",
                "reject": "rejected",
                "cancel": "cancelled",
            },
            "rejected": {
                "submit": "pending_approval",
                "cancel": "cancelled",
            },
            "approved": {
                "activate": "active",
                "cancel": "cancelled",
            },
            "active": {
                "complete": "completed",
                "cancel": "cancelled",
            },
        }

        allowed = transitions.get(current_status, {})

        if decision not in allowed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Decision '{decision}' is not allowed "
                    f"when contract status is '{current_status}'."
                ),
            )

        new_status = allowed[decision]

        approved_by = contract["approved_by"]
        approved_at = contract["approved_at"]

        if decision == "approve":
            approved_by = identity.user_id
            approved_at = now

        conn.execute(
            text(
                """
                UPDATE commercial_charter_parties
                SET status = :status,
                    approved_by = :approved_by,
                    approved_at = :approved_at,
                    updated_at = :updated_at
                WHERE id = :charter_party_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "status": new_status,
                "approved_by": approved_by,
                "approved_at": approved_at,
                "updated_at": now,
                "charter_party_id": charter_party_id,
                "tenant_id": identity.tenant_id,
            },
        )

        write_audit(
            conn,
            identity,
            decision.upper(),
            "CHARTER_PARTY",
            charter_party_id,
            payload.remarks,
        )

    return {
        "id": str(charter_party_id),
        "previous_status": current_status,
        "decision": decision,
        "status": new_status,
        "remarks": payload.remarks,
    }

# ============================================================
# VOYAGE COMMERCIAL MANAGEMENT
# ============================================================

@router.post(
    "/voyages",
    status_code=status.HTTP_201_CREATED,
    summary="Create Commercial Voyage",
)
def create_commercial_voyage(
    payload: VoyageCommercialCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()
    now = utc_now()
    voyage_id = uuid4()

    with engine.begin() as conn:
        contract = conn.execute(
            text(
                """
                SELECT
                    cp.id,
                    cp.contract_number,
                    cp.charterer_id,
                    cp.vessel_id,
                    cp.vessel_name,
                    cp.currency,
                    cp.status,
                    c.charterer_name
                FROM commercial_charter_parties cp
                JOIN commercial_charterers c
                  ON c.id = cp.charterer_id
                 AND c.tenant_id = cp.tenant_id
                WHERE cp.id = :charter_party_id
                  AND cp.tenant_id = :tenant_id
                """
            ),
            {
                "charter_party_id": payload.charter_party_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if contract is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Charter Party not found.",
            )

        if contract["status"] not in {"approved", "active"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Charter Party must be approved or active "
                    "before creating a voyage."
                ),
            )

        existing = conn.execute(
            text(
                """
                SELECT id
                FROM commercial_voyages
                WHERE tenant_id = :tenant_id
                  AND voyage_number = :voyage_number
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "voyage_number": payload.voyage_number.strip(),
            },
        ).first()

        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Voyage number already exists.",
            )

        conn.execute(
            text(
                """
                INSERT INTO commercial_voyages (
                    id,
                    tenant_id,
                    charter_party_id,
                    voyage_number,
                    vessel_id,
                    vessel_name,
                    charterer_id,
                    load_port,
                    discharge_port,
                    cargo_description,
                    cargo_quantity,
                    cargo_unit,
                    departure_date,
                    arrival_date,
                    currency,
                    estimated_revenue,
                    estimated_cost,
                    notes,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :charter_party_id,
                    :voyage_number,
                    :vessel_id,
                    :vessel_name,
                    :charterer_id,
                    :load_port,
                    :discharge_port,
                    :cargo_description,
                    :cargo_quantity,
                    :cargo_unit,
                    :departure_date,
                    :arrival_date,
                    :currency,
                    :estimated_revenue,
                    :estimated_cost,
                    :notes,
                    'planned',
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": voyage_id,
                "tenant_id": identity.tenant_id,
                "charter_party_id": payload.charter_party_id,
                "voyage_number": payload.voyage_number.strip(),
                "vessel_id": contract["vessel_id"],
                "vessel_name": contract["vessel_name"],
                "charterer_id": contract["charterer_id"],
                "load_port": payload.load_port,
                "discharge_port": payload.discharge_port,
                "cargo_description": payload.cargo_description,
                "cargo_quantity": payload.cargo_quantity,
                "cargo_unit": payload.cargo_unit,
                "departure_date": payload.departure_date,
                "arrival_date": payload.arrival_date,
                "currency": contract["currency"],
                "estimated_revenue": payload.estimated_revenue,
                "estimated_cost": payload.estimated_cost,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

        write_audit(
            conn,
            identity,
            "CREATE",
            "COMMERCIAL_VOYAGE",
            voyage_id,
            f"Created voyage {payload.voyage_number.strip()}",
        )

    return {
        "id": str(voyage_id),
        "voyage_number": payload.voyage_number.strip(),
        "charter_party_id": str(payload.charter_party_id),
        "vessel_name": contract["vessel_name"],
        "charterer_name": contract["charterer_name"],
        "currency": contract["currency"],
        "status": "planned",
    }


@router.get(
    "/voyages",
    summary="List Commercial Voyages",
)
def list_commercial_voyages(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    v.*,
                    cp.contract_number,
                    c.charterer_name
                FROM commercial_voyages v
                JOIN commercial_charter_parties cp
                  ON cp.id = v.charter_party_id
                 AND cp.tenant_id = v.tenant_id
                JOIN commercial_charterers c
                  ON c.id = cp.charterer_id
                 AND c.tenant_id = cp.tenant_id
                WHERE v.tenant_id = :tenant_id
                ORDER BY v.created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.get(
    "/voyages/{voyage_id}",
    summary="Get Commercial Voyage",
)
def get_commercial_voyage(
    voyage_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT
                    v.*,
                    cp.contract_number,
                    c.charterer_name
                FROM commercial_voyages v
                JOIN commercial_charter_parties cp
                  ON cp.id = v.charter_party_id
                 AND cp.tenant_id = v.tenant_id
                JOIN commercial_charterers c
                  ON c.id = cp.charterer_id
                 AND c.tenant_id = cp.tenant_id
                WHERE v.id = :voyage_id
                  AND v.tenant_id = :tenant_id
                """
            ),
            {
                "voyage_id": voyage_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Commercial voyage not found.",
        )

    return serialize_row(row)


# ============================================================
# VOYAGE REVENUE
# ============================================================

@router.post(
    "/voyages/{voyage_id}/revenue",
    status_code=status.HTTP_201_CREATED,
    summary="Record Voyage Revenue",
)
def create_voyage_revenue(
    voyage_id: UUID,
    payload: VoyageRevenueCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()
    now = utc_now()
    revenue_id = uuid4()

    with engine.begin() as conn:
        voyage = conn.execute(
            text(
                """
                SELECT id, voyage_number, currency
                FROM commercial_voyages
                WHERE id = :voyage_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "voyage_id": voyage_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if voyage is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Commercial voyage not found.",
            )

        conn.execute(
            text(
                """
                INSERT INTO commercial_voyage_revenue (
                    id,
                    tenant_id,
                    voyage_id,
                    revenue_type,
                    reference,
                    amount,
                    currency,
                    revenue_date,
                    notes,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :voyage_id,
                    :revenue_type,
                    :reference,
                    :amount,
                    :currency,
                    :revenue_date,
                    :notes,
                    :created_by,
                    :created_at
                )
                """
            ),
            {
                "id": revenue_id,
                "tenant_id": identity.tenant_id,
                "voyage_id": voyage_id,
                "revenue_type": payload.revenue_type.strip().lower(),
                "reference": payload.reference,
                "amount": payload.amount,
                "currency": voyage["currency"],
                "revenue_date": payload.revenue_date,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

        write_audit(
            conn,
            identity,
            "CREATE",
            "VOYAGE_REVENUE",
            revenue_id,
            f"Revenue recorded for voyage {voyage['voyage_number']}",
        )

    return {
        "id": str(revenue_id),
        "voyage_id": str(voyage_id),
        "revenue_type": payload.revenue_type.strip().lower(),
        "amount": payload.amount,
        "currency": voyage["currency"],
        "status": "recorded",
    }


@router.get(
    "/voyages/{voyage_id}/revenue",
    summary="List Voyage Revenue",
)
def list_voyage_revenue(
    voyage_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM commercial_voyage_revenue
                WHERE tenant_id = :tenant_id
                  AND voyage_id = :voyage_id
                ORDER BY created_at DESC
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "voyage_id": voyage_id,
            },
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# ============================================================
# DEMURRAGE / DESPATCH
# ============================================================

@router.post(
    "/voyages/{voyage_id}/demurrage-despatch",
    status_code=status.HTTP_201_CREATED,
    summary="Create Demurrage / Despatch Calculation",
)
def create_demurrage_despatch(
    voyage_id: UUID,
    payload: DemurrageDespatchCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()
    now = utc_now()
    calculation_id = uuid4()

    with engine.begin() as conn:
        voyage = conn.execute(
            text(
                """
                SELECT
                    v.id,
                    v.voyage_number,
                    v.currency,
                    cp.laytime_hours,
                    cp.demurrage_rate,
                    cp.despatch_rate
                FROM commercial_voyages v
                JOIN commercial_charter_parties cp
                  ON cp.id = v.charter_party_id
                 AND cp.tenant_id = v.tenant_id
                WHERE v.id = :voyage_id
                  AND v.tenant_id = :tenant_id
                """
            ),
            {
                "voyage_id": voyage_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if voyage is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Commercial voyage not found.",
            )

        allowed_hours = float(
            payload.allowed_laytime_hours
            if payload.allowed_laytime_hours is not None
            else (voyage["laytime_hours"] or 0)
        )

        used_hours = float(payload.used_laytime_hours)

        demurrage_rate = float(
            payload.demurrage_rate
            if payload.demurrage_rate is not None
            else (voyage["demurrage_rate"] or 0)
        )

        despatch_rate = float(
            payload.despatch_rate
            if payload.despatch_rate is not None
            else (voyage["despatch_rate"] or 0)
        )

        calculation_date = payload.calculation_date or utc_now().date()

        time_difference = used_hours - allowed_hours

        if time_difference > 0:
            result_type = "demurrage"
            chargeable_hours = time_difference
            amount = (chargeable_hours / 24.0) * demurrage_rate
        elif time_difference < 0:
            result_type = "despatch"
            chargeable_hours = abs(time_difference)
            amount = (chargeable_hours / 24.0) * despatch_rate
        else:
            result_type = "on_laytime"
            chargeable_hours = 0.0
            amount = 0.0

        conn.execute(
            text(
                """
                INSERT INTO commercial_demurrage_despatch (
                    id,
                    tenant_id,
                    voyage_id,
                    allowed_laytime_hours,
                    used_laytime_hours,
                    chargeable_hours,
                    result_type,
                    rate,
                    amount,
                    currency,
                    calculation_date,
                    notes,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :voyage_id,
                    :allowed_laytime_hours,
                    :used_laytime_hours,
                    :chargeable_hours,
                    :result_type,
                    :rate,
                    :amount,
                    :currency,
                    :calculation_date,
                    :notes,
                    :created_by,
                    :created_at
                )
                """
            ),
            {
                "id": calculation_id,
                "tenant_id": identity.tenant_id,
                "voyage_id": voyage_id,
                "allowed_laytime_hours": allowed_hours,
                "used_laytime_hours": used_hours,
                "chargeable_hours": chargeable_hours,
                "result_type": result_type,
                "rate": (
                    demurrage_rate
                    if result_type == "demurrage"
                    else despatch_rate
                    if result_type == "despatch"
                    else 0
                ),
                "amount": amount,
                "currency": voyage["currency"],
                "calculation_date": calculation_date,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

        write_audit(
            conn,
            identity,
            "CALCULATE",
            "DEMURRAGE_DESPATCH",
            calculation_id,
            f"{result_type} calculation for {voyage['voyage_number']}",
        )

    return {
        "id": str(calculation_id),
        "voyage_id": str(voyage_id),
        "result_type": result_type,
        "allowed_laytime_hours": allowed_hours,
        "used_laytime_hours": used_hours,
        "chargeable_hours": chargeable_hours,
        "rate": (
            demurrage_rate
            if result_type == "demurrage"
            else despatch_rate
            if result_type == "despatch"
            else 0
        ),
        "amount": amount,
        "currency": voyage["currency"],
    }


@router.get(
    "/voyages/{voyage_id}/demurrage-despatch",
    summary="List Demurrage / Despatch Calculations",
)
def list_demurrage_despatch(
    voyage_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM commercial_demurrage_despatch
                WHERE tenant_id = :tenant_id
                  AND voyage_id = :voyage_id
                ORDER BY created_at DESC
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "voyage_id": voyage_id,
            },
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# ============================================================
# COMMERCIAL INTELLIGENCE DASHBOARD
# ============================================================

@router.get(
    "/dashboard",
    summary="Commercial Intelligence Dashboard",
)
def commercial_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()

    with engine.begin() as conn:
        charterers = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM commercial_charterers
                WHERE tenant_id = :tenant_id
                  AND status = 'active'
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

        contracts = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_contracts,
                    COUNT(*) FILTER (
                        WHERE status = 'active'
                    ) AS active_contracts,
                    COUNT(*) FILTER (
                        WHERE status = 'pending_approval'
                    ) AS pending_approval
                FROM commercial_charter_parties
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        voyages = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_voyages,
                    COUNT(*) FILTER (
                        WHERE status = 'planned'
                    ) AS planned_voyages,
                    COUNT(*) FILTER (
                        WHERE status = 'active'
                    ) AS active_voyages,
                    COALESCE(SUM(estimated_revenue), 0)
                        AS estimated_revenue,
                    COALESCE(SUM(estimated_cost), 0)
                        AS estimated_cost
                FROM commercial_voyages
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        revenue = conn.execute(
            text(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM commercial_voyage_revenue
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

        demurrage = conn.execute(
            text(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM commercial_demurrage_despatch
                WHERE tenant_id = :tenant_id
                  AND result_type = 'demurrage'
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

        despatch = conn.execute(
            text(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM commercial_demurrage_despatch
                WHERE tenant_id = :tenant_id
                  AND result_type = 'despatch'
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

    with engine.begin() as conn:
        actual_cost = conn.execute(
            text("""
                SELECT COALESCE(SUM(actual_cost), 0)
                FROM commercial_voyages
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

    actual_cost = float(actual_cost or 0)
    actual_margin = float(revenue or 0) - actual_cost

    estimated_revenue = float(voyages["estimated_revenue"] or 0)
    estimated_cost = float(voyages["estimated_cost"] or 0)

    return {
        "system": "MQEIP COMMERCIAL & CHARTERING INTELLIGENCE",
        "active_charterers": charterers,
        "total_contracts": contracts["total_contracts"],
        "active_contracts": contracts["active_contracts"],
        "pending_contract_approvals": contracts["pending_approval"],
        "total_voyages": voyages["total_voyages"],
        "planned_voyages": voyages["planned_voyages"],
        "active_voyages": voyages["active_voyages"],
        "estimated_revenue": estimated_revenue,
        "estimated_cost": estimated_cost,
        "estimated_margin": estimated_revenue - estimated_cost,
        "recorded_revenue": float(revenue or 0),
        "actual_cost": actual_cost,
        "actual_margin": actual_margin,
        "demurrage_amount": float(demurrage or 0),
        "despatch_amount": float(despatch or 0),
        "tenant_isolation": True,
        "audit_traceability": True,
    }


# ============================================================
# COMMERCIAL AUDIT TRAIL
# ============================================================

@router.get(
    "/audit-trail",
    summary="Commercial Audit Trail",
)
def commercial_audit_trail(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM commercial_audit_log
                WHERE tenant_id = :tenant_id
                ORDER BY performed_at DESC
                LIMIT 1000
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]

# ================================================
# COMMERCIAL VOYAGE COST RECORD
# ================================================

class VoyageCostRecord(BaseModel):
    bunker_cost: float = Field(default=0, ge=0)
    port_cost: float = Field(default=0, ge=0)
    agency_cost: float = Field(default=0, ge=0)
    other_cost: float = Field(default=0, ge=0)


@router.post(
    "/voyages/{voyage_id}/cost",
    summary="Record Commercial Voyage Cost",
)
def record_commercial_voyage_cost(
    voyage_id: UUID,
    payload: VoyageCostRecord,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_commercial_tables()
    engine = get_engine()

    with engine.begin() as conn:
        voyage = conn.execute(
            text("""
                SELECT id
                FROM commercial_voyages
                WHERE id = :voyage_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "voyage_id": voyage_id,
                "tenant_id": identity.tenant_id,
            },
        ).first()

        if voyage is None:
            raise HTTPException(
                status_code=404,
                detail="Commercial voyage not found",
            )

        total_cost = (
            payload.bunker_cost
            + payload.port_cost
            + payload.agency_cost
            + payload.other_cost
        )

        conn.execute(
            text("""
                UPDATE commercial_voyages
                SET bunker_cost = :bunker_cost,
                    port_cost = :port_cost,
                    agency_cost = :agency_cost,
                    other_cost = :other_cost,
                    actual_cost = :actual_cost,
                    updated_at = :updated_at
                WHERE id = :voyage_id
                  AND tenant_id = :tenant_id
            """),
            {
                "bunker_cost": payload.bunker_cost,
                "port_cost": payload.port_cost,
                "agency_cost": payload.agency_cost,
                "other_cost": payload.other_cost,
                "actual_cost": total_cost,
                "updated_at": utc_now(),
                "voyage_id": voyage_id,
                "tenant_id": identity.tenant_id,
            },
        )

    return {
        "voyage_id": str(voyage_id),
        "bunker_cost": payload.bunker_cost,
        "port_cost": payload.port_cost,
        "agency_cost": payload.agency_cost,
        "other_cost": payload.other_cost,
        "actual_cost": total_cost,
        "currency": "USD",
        "status": "recorded",
    }


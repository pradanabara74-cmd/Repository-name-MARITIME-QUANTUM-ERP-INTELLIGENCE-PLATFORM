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
    prefix="/api/v1/finance",
    tags=["Finance & Accounting Intelligence"],
)


# ============================================================
# FINANCE CORE — REQUEST MODELS
# ============================================================

class AccountCreate(BaseModel):
    account_code: str = Field(min_length=1, max_length=50)
    account_name: str = Field(min_length=1, max_length=255)
    account_type: str = Field(min_length=1, max_length=50)
    currency: str = Field(default="USD", min_length=3, max_length=10)
    parent_account_code: Optional[str] = Field(default=None, max_length=50)
    notes: Optional[str] = None


class BudgetCreate(BaseModel):
    budget_code: str = Field(min_length=1, max_length=100)
    fiscal_year: int = Field(ge=2000, le=2200)
    budget_type: str = Field(min_length=1, max_length=50)
    department: str = Field(min_length=1, max_length=100)
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    currency: str = Field(default="USD", min_length=3, max_length=10)
    approved_amount: Decimal = Field(ge=0)
    notes: Optional[str] = None


class InvoiceCreate(BaseModel):
    invoice_number: str = Field(min_length=1, max_length=100)
    invoice_type: str = Field(min_length=1, max_length=50)
    counterparty_name: str = Field(min_length=1, max_length=255)
    invoice_date: date
    due_date: Optional[date] = None
    currency: str = Field(default="USD", min_length=3, max_length=10)
    amount: Decimal = Field(gt=0)
    department: Optional[str] = None
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    voyage_id: Optional[str] = None
    po_id: Optional[str] = None
    notes: Optional[str] = None


class PaymentCreate(BaseModel):
    invoice_id: UUID
    payment_reference: str = Field(min_length=1, max_length=100)
    payment_date: date
    amount: Decimal = Field(gt=0)
    payment_method: Optional[str] = Field(default=None, max_length=100)
    bank_reference: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = None


class VoyageCostCreate(BaseModel):
    voyage_id: str = Field(min_length=1, max_length=255)
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    cost_category: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    currency: str = Field(default="USD", min_length=3, max_length=10)
    amount: Decimal = Field(gt=0)
    transaction_date: date
    reference: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = None


# ============================================================
# DATABASE FOUNDATION
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


def init_finance_tables():
    engine = get_engine()

    with engine.begin() as conn:

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS finance_accounts (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    account_code VARCHAR(50) NOT NULL,
                    account_name VARCHAR(255) NOT NULL,
                    account_type VARCHAR(50) NOT NULL,
                    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                    parent_account_code VARCHAR(50),
                    notes TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'active',
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (tenant_id, account_code)
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS finance_budgets (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    budget_code VARCHAR(100) NOT NULL,
                    fiscal_year INTEGER NOT NULL,
                    budget_type VARCHAR(50) NOT NULL,
                    department VARCHAR(100) NOT NULL,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                    approved_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
                    committed_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
                    actual_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
                    notes TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'active',
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (tenant_id, budget_code)
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS finance_invoices (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    invoice_number VARCHAR(100) NOT NULL,
                    invoice_type VARCHAR(50) NOT NULL,
                    counterparty_name VARCHAR(255) NOT NULL,
                    invoice_date DATE NOT NULL,
                    due_date DATE,
                    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                    amount NUMERIC(18,2) NOT NULL,
                    paid_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
                    department VARCHAR(100),
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    voyage_id VARCHAR(255),
                    po_id VARCHAR(255),
                    notes TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'open',
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (tenant_id, invoice_number)
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS finance_payments (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    invoice_id UUID NOT NULL,
                    payment_reference VARCHAR(100) NOT NULL,
                    payment_date DATE NOT NULL,
                    amount NUMERIC(18,2) NOT NULL,
                    payment_method VARCHAR(100),
                    bank_reference VARCHAR(255),
                    notes TEXT,
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (tenant_id, payment_reference)
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS finance_voyage_costs (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    voyage_id VARCHAR(255) NOT NULL,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    cost_category VARCHAR(100) NOT NULL,
                    description VARCHAR(500) NOT NULL,
                    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                    amount NUMERIC(18,2) NOT NULL,
                    transaction_date DATE NOT NULL,
                    reference VARCHAR(255),
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
                CREATE INDEX IF NOT EXISTS idx_finance_invoice_tenant_status
                ON finance_invoices (tenant_id, status)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_finance_budget_tenant_year
                ON finance_budgets (tenant_id, fiscal_year)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_finance_voyage_cost
                ON finance_voyage_costs (tenant_id, voyage_id)
                """
            )
        )

  # ============================================================
# CHART OF ACCOUNTS
# ============================================================

@router.get("/accounts", summary="List Finance Accounts")
def list_accounts(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM finance_accounts
                WHERE tenant_id = :tenant_id
                ORDER BY account_code
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/accounts",
    status_code=status.HTTP_201_CREATED,
    summary="Create Finance Account",
)
def create_account(
    payload: AccountCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()
    now = datetime.now(timezone.utc)
    account_id = uuid4()

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT id
                FROM finance_accounts
                WHERE tenant_id = :tenant_id
                  AND account_code = :account_code
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "account_code": payload.account_code,
            },
        ).first()

        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account code already exists.",
            )

        conn.execute(
            text(
                """
                INSERT INTO finance_accounts (
                    id,
                    tenant_id,
                    account_code,
                    account_name,
                    account_type,
                    currency,
                    parent_account_code,
                    notes,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :account_code,
                    :account_name,
                    :account_type,
                    :currency,
                    :parent_account_code,
                    :notes,
                    'active',
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": account_id,
                "tenant_id": identity.tenant_id,
                "account_code": payload.account_code,
                "account_name": payload.account_name,
                "account_type": payload.account_type.lower(),
                "currency": payload.currency.upper(),
                "parent_account_code": payload.parent_account_code,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(account_id),
        "account_code": payload.account_code,
        "account_name": payload.account_name,
        "account_type": payload.account_type.lower(),
        "currency": payload.currency.upper(),
        "status": "active",
    }


@router.get(
    "/accounts/{account_id}",
    summary="Get Finance Account",
)
def get_account(
    account_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT *
                FROM finance_accounts
                WHERE id = :account_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "account_id": account_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Finance account not found.",
        )

    return serialize_row(row)


# ============================================================
# BUDGET / OPEX / CAPEX
# ============================================================

@router.get("/budgets", summary="List Budgets")
def list_budgets(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM finance_budgets
                WHERE tenant_id = :tenant_id
                ORDER BY fiscal_year DESC, budget_code
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/budgets",
    status_code=status.HTTP_201_CREATED,
    summary="Create OPEX / CAPEX Budget",
)
def create_budget(
    payload: BudgetCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()

    budget_type = payload.budget_type.strip().lower()

    if budget_type not in {"opex", "capex", "operational", "project"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Budget type must be OPEX, CAPEX, operational, or project.",
        )

    engine = get_engine()
    now = datetime.now(timezone.utc)
    budget_id = uuid4()

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT id
                FROM finance_budgets
                WHERE tenant_id = :tenant_id
                  AND budget_code = :budget_code
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "budget_code": payload.budget_code,
            },
        ).first()

        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Budget code already exists.",
            )

        conn.execute(
            text(
                """
                INSERT INTO finance_budgets (
                    id,
                    tenant_id,
                    budget_code,
                    fiscal_year,
                    budget_type,
                    department,
                    vessel_id,
                    vessel_name,
                    currency,
                    approved_amount,
                    committed_amount,
                    actual_amount,
                    notes,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :budget_code,
                    :fiscal_year,
                    :budget_type,
                    :department,
                    :vessel_id,
                    :vessel_name,
                    :currency,
                    :approved_amount,
                    0,
                    0,
                    :notes,
                    'active',
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": budget_id,
                "tenant_id": identity.tenant_id,
                "budget_code": payload.budget_code,
                "fiscal_year": payload.fiscal_year,
                "budget_type": budget_type,
                "department": payload.department,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "currency": payload.currency.upper(),
                "approved_amount": payload.approved_amount,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(budget_id),
        "budget_code": payload.budget_code,
        "fiscal_year": payload.fiscal_year,
        "budget_type": budget_type,
        "approved_amount": float(payload.approved_amount),
        "committed_amount": 0,
        "actual_amount": 0,
        "currency": payload.currency.upper(),
        "status": "active",
    }


@router.get(
    "/budgets/{budget_id}",
    summary="Get Budget",
)
def get_budget(
    budget_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT *
                FROM finance_budgets
                WHERE id = :budget_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "budget_id": budget_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Budget not found.",
        )

    return serialize_row(row)

# ============================================================
# ACCOUNTS PAYABLE / ACCOUNTS RECEIVABLE
# ============================================================

@router.get(
    "/invoices",
    summary="List AP / AR Invoices",
)
def list_invoices(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM finance_invoices
                WHERE tenant_id = :tenant_id
                ORDER BY invoice_date DESC, created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/invoices",
    status_code=status.HTTP_201_CREATED,
    summary="Create AP / AR Invoice",
)
def create_invoice(
    payload: InvoiceCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()

    invoice_type = payload.invoice_type.strip().lower()

    if invoice_type not in {
        "accounts_payable",
        "accounts_receivable",
        "ap",
        "ar",
    }:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invoice type must be AP or AR.",
        )

    engine = get_engine()
    now = datetime.now(timezone.utc)
    invoice_id = uuid4()

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT id
                FROM finance_invoices
                WHERE tenant_id = :tenant_id
                  AND invoice_number = :invoice_number
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "invoice_number": payload.invoice_number,
            },
        ).first()

        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Invoice number already exists.",
            )

        conn.execute(
            text(
                """
                INSERT INTO finance_invoices (
                    id,
                    tenant_id,
                    invoice_number,
                    invoice_type,
                    counterparty_name,
                    invoice_date,
                    due_date,
                    currency,
                    amount,
                    paid_amount,
                    department,
                    vessel_id,
                    vessel_name,
                    voyage_id,
                    po_id,
                    notes,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :invoice_number,
                    :invoice_type,
                    :counterparty_name,
                    :invoice_date,
                    :due_date,
                    :currency,
                    :amount,
                    0,
                    :department,
                    :vessel_id,
                    :vessel_name,
                    :voyage_id,
                    :po_id,
                    :notes,
                    'open',
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": invoice_id,
                "tenant_id": identity.tenant_id,
                "invoice_number": payload.invoice_number,
                "invoice_type": invoice_type,
                "counterparty_name": payload.counterparty_name,
                "invoice_date": payload.invoice_date,
                "due_date": payload.due_date,
                "currency": payload.currency.upper(),
                "amount": payload.amount,
                "department": payload.department,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "voyage_id": payload.voyage_id,
                "po_id": payload.po_id,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(invoice_id),
        "invoice_number": payload.invoice_number,
        "invoice_type": invoice_type,
        "counterparty_name": payload.counterparty_name,
        "amount": float(payload.amount),
        "paid_amount": 0,
        "currency": payload.currency.upper(),
        "status": "open",
    }


@router.get(
    "/invoices/{invoice_id}",
    summary="Get AP / AR Invoice",
)
def get_invoice(
    invoice_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT *
                FROM finance_invoices
                WHERE id = :invoice_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "invoice_id": invoice_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    return serialize_row(row)


# ============================================================
# PAYMENT MANAGEMENT
# ============================================================

@router.post(
    "/payments",
    status_code=status.HTTP_201_CREATED,
    summary="Record Invoice Payment",
)
def create_payment(
    payload: PaymentCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()
    now = datetime.now(timezone.utc)
    payment_id = uuid4()

    with engine.begin() as conn:
        invoice = conn.execute(
            text(
                """
                SELECT *
                FROM finance_invoices
                WHERE id = :invoice_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {
                "invoice_id": payload.invoice_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if invoice is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invoice not found.",
            )

        invoice_amount = Decimal(str(invoice["amount"]))
        paid_amount = Decimal(str(invoice["paid_amount"]))
        outstanding = invoice_amount - paid_amount

        if payload.amount > outstanding:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment exceeds outstanding invoice balance.",
            )

        existing_payment = conn.execute(
            text(
                """
                SELECT id
                FROM finance_payments
                WHERE tenant_id = :tenant_id
                  AND payment_reference = :payment_reference
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "payment_reference": payload.payment_reference,
            },
        ).first()

        if existing_payment is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment reference already exists.",
            )

        new_paid_amount = paid_amount + payload.amount

        if new_paid_amount >= invoice_amount:
            invoice_status = "paid"
        elif new_paid_amount > 0:
            invoice_status = "partially_paid"
        else:
            invoice_status = "open"

        conn.execute(
            text(
                """
                INSERT INTO finance_payments (
                    id,
                    tenant_id,
                    invoice_id,
                    payment_reference,
                    payment_date,
                    amount,
                    payment_method,
                    bank_reference,
                    notes,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :invoice_id,
                    :payment_reference,
                    :payment_date,
                    :amount,
                    :payment_method,
                    :bank_reference,
                    :notes,
                    :created_by,
                    :created_at
                )
                """
            ),
            {
                "id": payment_id,
                "tenant_id": identity.tenant_id,
                "invoice_id": payload.invoice_id,
                "payment_reference": payload.payment_reference,
                "payment_date": payload.payment_date,
                "amount": payload.amount,
                "payment_method": payload.payment_method,
                "bank_reference": payload.bank_reference,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

        conn.execute(
            text(
                """
                UPDATE finance_invoices
                SET paid_amount = :paid_amount,
                    status = :status,
                    updated_at = :updated_at
                WHERE id = :invoice_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "paid_amount": new_paid_amount,
                "status": invoice_status,
                "updated_at": now,
                "invoice_id": payload.invoice_id,
                "tenant_id": identity.tenant_id,
            },
        )

    return {
        "id": str(payment_id),
        "invoice_id": str(payload.invoice_id),
        "payment_reference": payload.payment_reference,
        "amount": float(payload.amount),
        "invoice_paid_amount": float(new_paid_amount),
        "invoice_outstanding": float(invoice_amount - new_paid_amount),
        "invoice_status": invoice_status,
    }


@router.get(
    "/invoices/{invoice_id}/payments",
    summary="List Invoice Payments",
)
def list_invoice_payments(
    invoice_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM finance_payments
                WHERE tenant_id = :tenant_id
                  AND invoice_id = :invoice_id
                ORDER BY payment_date DESC, created_at DESC
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "invoice_id": invoice_id,
            },
        ).mappings().all()

    return [serialize_row(row) for row in rows]

# ============================================================
# VOYAGE ACCOUNTING / VESSEL OPEX
# ============================================================

@router.post(
    "/voyage-costs",
    status_code=status.HTTP_201_CREATED,
    summary="Record Voyage / Vessel Cost",
)
def create_voyage_cost(
    payload: VoyageCostCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()
    now = datetime.now(timezone.utc)
    cost_id = uuid4()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO finance_voyage_costs (
                    id,
                    tenant_id,
                    voyage_id,
                    vessel_id,
                    vessel_name,
                    cost_category,
                    description,
                    currency,
                    amount,
                    transaction_date,
                    reference,
                    notes,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :voyage_id,
                    :vessel_id,
                    :vessel_name,
                    :cost_category,
                    :description,
                    :currency,
                    :amount,
                    :transaction_date,
                    :reference,
                    :notes,
                    :created_by,
                    :created_at
                )
                """
            ),
            {
                "id": cost_id,
                "tenant_id": identity.tenant_id,
                "voyage_id": payload.voyage_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "cost_category": payload.cost_category,
                "description": payload.description,
                "currency": payload.currency.upper(),
                "amount": payload.amount,
                "transaction_date": payload.transaction_date,
                "reference": payload.reference,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

    return {
        "id": str(cost_id),
        "voyage_id": payload.voyage_id,
        "vessel_name": payload.vessel_name,
        "cost_category": payload.cost_category,
        "amount": float(payload.amount),
        "currency": payload.currency.upper(),
    }


@router.get(
    "/voyage-costs",
    summary="List Voyage / Vessel Costs",
)
def list_voyage_costs(
    voyage_id: Optional[str] = None,
    vessel_id: Optional[str] = None,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    sql = """
        SELECT *
        FROM finance_voyage_costs
        WHERE tenant_id = :tenant_id
    """

    params = {"tenant_id": identity.tenant_id}

    if voyage_id:
        sql += " AND voyage_id = :voyage_id"
        params["voyage_id"] = voyage_id

    if vessel_id:
        sql += " AND vessel_id = :vessel_id"
        params["vessel_id"] = vessel_id

    sql += " ORDER BY transaction_date DESC, created_at DESC"

    with engine.begin() as conn:
        rows = conn.execute(
            text(sql),
            params,
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.get(
    "/voyages/{voyage_id}/pnl",
    summary="Voyage Profit and Loss Intelligence",
)
def voyage_profit_and_loss(
    voyage_id: str,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        cost_row = conn.execute(
            text(
                """
                SELECT
                    COALESCE(SUM(amount), 0) AS total_cost
                FROM finance_voyage_costs
                WHERE tenant_id = :tenant_id
                  AND voyage_id = :voyage_id
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "voyage_id": voyage_id,
            },
        ).mappings().first()

        revenue_row = conn.execute(
            text(
                """
                SELECT
                    COALESCE(SUM(amount), 0) AS total_revenue
                FROM finance_invoices
                WHERE tenant_id = :tenant_id
                  AND voyage_id = :voyage_id
                  AND invoice_type IN (
                      'accounts_receivable',
                      'ar'
                  )
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "voyage_id": voyage_id,
            },
        ).mappings().first()

    total_cost = Decimal(str(cost_row["total_cost"]))
    total_revenue = Decimal(str(revenue_row["total_revenue"]))
    gross_profit = total_revenue - total_cost

    if total_revenue > 0:
        profit_margin = (
            gross_profit / total_revenue
        ) * Decimal("100")
    else:
        profit_margin = Decimal("0")

    return {
        "voyage_id": voyage_id,
        "currency_scope": "transaction_currency",
        "total_revenue": float(total_revenue),
        "total_cost": float(total_cost),
        "gross_profit": float(gross_profit),
        "profit_margin_percent": round(float(profit_margin), 2),
    }


@router.get(
    "/vessels/{vessel_id}/opex",
    summary="Vessel OPEX Intelligence",
)
def vessel_opex(
    vessel_id: str,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    cost_category,
                    currency,
                    COUNT(*) AS transaction_count,
                    COALESCE(SUM(amount), 0) AS total_amount
                FROM finance_voyage_costs
                WHERE tenant_id = :tenant_id
                  AND vessel_id = :vessel_id
                GROUP BY cost_category, currency
                ORDER BY total_amount DESC
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "vessel_id": vessel_id,
            },
        ).mappings().all()

    return {
        "vessel_id": vessel_id,
        "cost_breakdown": [
            serialize_row(row) for row in rows
        ],
    }


# ============================================================
# FINANCE INTELLIGENCE DASHBOARD
# ============================================================

@router.get(
    "/dashboard",
    summary="Finance Intelligence Dashboard",
)
def finance_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        invoice_summary = conn.execute(
            text(
                """
                SELECT
                    invoice_type,
                    currency,
                    COUNT(*) AS invoice_count,
                    COALESCE(SUM(amount), 0) AS total_amount,
                    COALESCE(SUM(paid_amount), 0) AS paid_amount,
                    COALESCE(
                        SUM(amount - paid_amount),
                        0
                    ) AS outstanding_amount
                FROM finance_invoices
                WHERE tenant_id = :tenant_id
                GROUP BY invoice_type, currency
                ORDER BY invoice_type, currency
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        budget_summary = conn.execute(
            text(
                """
                SELECT
                    fiscal_year,
                    budget_type,
                    currency,
                    COALESCE(
                        SUM(approved_amount),
                        0
                    ) AS approved_amount,
                    COALESCE(
                        SUM(committed_amount),
                        0
                    ) AS committed_amount,
                    COALESCE(
                        SUM(actual_amount),
                        0
                    ) AS actual_amount
                FROM finance_budgets
                WHERE tenant_id = :tenant_id
                GROUP BY
                    fiscal_year,
                    budget_type,
                    currency
                ORDER BY fiscal_year DESC, budget_type
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        voyage_cost_summary = conn.execute(
            text(
                """
                SELECT
                    currency,
                    COUNT(*) AS transaction_count,
                    COALESCE(SUM(amount), 0) AS total_cost
                FROM finance_voyage_costs
                WHERE tenant_id = :tenant_id
                GROUP BY currency
                ORDER BY currency
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        overdue = conn.execute(
            text(
                """
                SELECT COUNT(*) AS overdue_count
                FROM finance_invoices
                WHERE tenant_id = :tenant_id
                  AND due_date < CURRENT_DATE
                  AND status IN ('open', 'partially_paid')
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

    return {
        "tenant_id": identity.tenant_id,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "overdue_invoice_count": int(
            overdue["overdue_count"]
        ),
        "invoice_intelligence": [
            serialize_row(row)
            for row in invoice_summary
        ],
        "budget_intelligence": [
            serialize_row(row)
            for row in budget_summary
        ],
        "voyage_cost_intelligence": [
            serialize_row(row)
            for row in voyage_cost_summary
        ],
    }

# ============================================================
# FINANCE AUDIT TRAIL
# ============================================================

@router.get(
    "/audit-trail",
    summary="Finance Audit Trail",
)
def finance_audit_trail(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        accounts = conn.execute(
            text(
                """
                SELECT
                    'account_created' AS event_type,
                    id AS entity_id,
                    account_code AS reference,
                    account_name AS description,
                    created_by,
                    created_at
                FROM finance_accounts
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        budgets = conn.execute(
            text(
                """
                SELECT
                    'budget_created' AS event_type,
                    id AS entity_id,
                    budget_code AS reference,
                    department AS description,
                    created_by,
                    created_at
                FROM finance_budgets
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        invoices = conn.execute(
            text(
                """
                SELECT
                    'invoice_created' AS event_type,
                    id AS entity_id,
                    invoice_number AS reference,
                    counterparty_name AS description,
                    created_by,
                    created_at
                FROM finance_invoices
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        payments = conn.execute(
            text(
                """
                SELECT
                    'payment_recorded' AS event_type,
                    id AS entity_id,
                    payment_reference AS reference,
                    payment_method AS description,
                    created_by,
                    created_at
                FROM finance_payments
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    events = (
        list(accounts)
        + list(budgets)
        + list(invoices)
        + list(payments)
    )

    events.sort(
        key=lambda item: item["created_at"],
        reverse=True,
    )

    return [
        serialize_row(event)
        for event in events[:500]
    ]


# ============================================================
# EXECUTIVE FINANCIAL INTELLIGENCE
# ============================================================

@router.get(
    "/intelligence",
    summary="Executive Financial Intelligence",
)
def financial_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        invoice_metrics = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_invoices,
                    COUNT(*) FILTER (
                        WHERE status = 'paid'
                    ) AS paid_invoices,
                    COUNT(*) FILTER (
                        WHERE status IN (
                            'open',
                            'partially_paid'
                        )
                    ) AS outstanding_invoices,
                    COUNT(*) FILTER (
                        WHERE due_date < CURRENT_DATE
                          AND status IN (
                              'open',
                              'partially_paid'
                          )
                    ) AS overdue_invoices
                FROM finance_invoices
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        budget_metrics = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_budgets,
                    COALESCE(
                        SUM(approved_amount),
                        0
                    ) AS approved_budget,
                    COALESCE(
                        SUM(committed_amount),
                        0
                    ) AS committed_budget,
                    COALESCE(
                        SUM(actual_amount),
                        0
                    ) AS actual_budget
                FROM finance_budgets
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        voyage_metrics = conn.execute(
            text(
                """
                SELECT
                    COUNT(DISTINCT voyage_id) AS voyages_tracked,
                    COUNT(*) AS cost_transactions,
                    COALESCE(
                        SUM(amount),
                        0
                    ) AS recorded_voyage_cost
                FROM finance_voyage_costs
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

    approved = Decimal(
        str(budget_metrics["approved_budget"])
    )
    committed = Decimal(
        str(budget_metrics["committed_budget"])
    )
    actual = Decimal(
        str(budget_metrics["actual_budget"])
    )

    available_budget = approved - committed - actual

    if approved > 0:
        utilization = (
            (committed + actual)
            / approved
        ) * Decimal("100")
    else:
        utilization = Decimal("0")

    risk_level = "LOW"

    if int(invoice_metrics["overdue_invoices"]) > 0:
        risk_level = "MEDIUM"

    if available_budget < 0:
        risk_level = "HIGH"

    return {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "financial_risk_level": risk_level,
        "invoice_metrics": {
            "total": int(
                invoice_metrics["total_invoices"]
            ),
            "paid": int(
                invoice_metrics["paid_invoices"]
            ),
            "outstanding": int(
                invoice_metrics["outstanding_invoices"]
            ),
            "overdue": int(
                invoice_metrics["overdue_invoices"]
            ),
        },
        "budget_metrics": {
            "total_budgets": int(
                budget_metrics["total_budgets"]
            ),
            "approved": float(approved),
            "committed": float(committed),
            "actual": float(actual),
            "available": float(available_budget),
            "utilization_percent": round(
                float(utilization),
                2,
            ),
        },
        "voyage_metrics": {
            "voyages_tracked": int(
                voyage_metrics["voyages_tracked"]
            ),
            "cost_transactions": int(
                voyage_metrics["cost_transactions"]
            ),
            "recorded_voyage_cost": float(
                voyage_metrics["recorded_voyage_cost"]
            ),
        },
    }


# Initialize Finance schema when module is loaded.
init_finance_tables()

import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from ..core.database import get_database_url
from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/finance",
    tags=["Finance & Accounting Intelligence"],
)


_engine = None
_engine_url = None
_engine_lock = threading.Lock()

_tables_ready = False
_tables_lock = threading.Lock()


def _normalize_database_url(url):
    # Render/Heroku can supply "postgres://", which SQLAlchemy 1.4+
    # no longer accepts. Normalise it to "postgresql://".
    url = str(url)

    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]

    return url


def get_engine():
    """
    Return a shared SQLAlchemy engine.

    The original code created a brand-new engine (and a brand-new
    connection pool) on every call, which leaks database connections
    under load and eventually exhausts the connection limit.
    """
    global _engine, _engine_url

    url = _normalize_database_url(get_database_url())

    with _engine_lock:
        if _engine is None or _engine_url != url:
            if _engine is not None:
                _engine.dispose()

            _engine = create_engine(
                url,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=5,
                pool_recycle=1800,
            )
            _engine_url = url

        return _engine


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

class AccountCreate(BaseModel):
    account_code: str = Field(min_length=1, max_length=50)
    account_name: str = Field(min_length=1, max_length=255)
    account_type: str = Field(min_length=1, max_length=50)
    parent_account_code: Optional[str] = None
    currency: str = Field(default="USD", max_length=10)
    notes: Optional[str] = None


class CostCenterCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    department: Optional[str] = None
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    notes: Optional[str] = None


class FinancialPeriodCreate(BaseModel):
    period_name: str = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date


class BudgetCreate(BaseModel):
    fiscal_year: int
    cost_center_code: str = Field(min_length=1, max_length=50)
    account_code: str = Field(min_length=1, max_length=50)
    budget_type: str = Field(default="OPEX", max_length=20)
    currency: str = Field(default="USD", max_length=10)
    amount: float = Field(ge=0)
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    notes: Optional[str] = None


class JournalLineCreate(BaseModel):
    account_code: str = Field(min_length=1, max_length=50)
    description: Optional[str] = None
    debit: float = Field(default=0, ge=0)
    credit: float = Field(default=0, ge=0)
    cost_center_code: Optional[str] = None
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    voyage_number: Optional[str] = None


class JournalCreate(BaseModel):
    journal_date: date
    description: str = Field(min_length=1)
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    reference_number: Optional[str] = None
    currency: str = Field(default="USD", max_length=10)
    lines: list[JournalLineCreate]


class VendorInvoiceCreate(BaseModel):
    vendor_name: str = Field(min_length=1, max_length=255)
    vendor_invoice_number: str = Field(min_length=1, max_length=100)
    invoice_date: date
    due_date: Optional[date] = None
    currency: str = Field(default="USD", max_length=10)
    amount: float = Field(gt=0)

    po_id: Optional[str] = None
    po_number: Optional[str] = None
    goods_receipt_id: Optional[str] = None
    gr_number: Optional[str] = None

    expense_account_code: str = Field(min_length=1, max_length=50)
    payable_account_code: str = Field(
        default="2000",
        min_length=1,
        max_length=50,
    )

    cost_center_code: Optional[str] = None
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    voyage_number: Optional[str] = None
    notes: Optional[str] = None


class CustomerInvoiceCreate(BaseModel):
    customer_name: str = Field(min_length=1, max_length=255)
    customer_invoice_number: str = Field(min_length=1, max_length=100)
    invoice_date: date
    due_date: Optional[date] = None
    currency: str = Field(default="USD", max_length=10)
    amount: float = Field(gt=0)

    revenue_account_code: str = Field(min_length=1, max_length=50)
    receivable_account_code: str = Field(
        default="1100",
        min_length=1,
        max_length=50,
    )

    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    voyage_number: Optional[str] = None
    charter_reference: Optional[str] = None
    notes: Optional[str] = None


class PaymentCreate(BaseModel):
    payment_date: date
    payment_type: str = Field(min_length=1, max_length=30)
    amount: float = Field(gt=0)
    currency: str = Field(default="USD", max_length=10)

    counterparty: str = Field(min_length=1, max_length=255)
    bank_account_code: str = Field(min_length=1, max_length=50)
    offset_account_code: str = Field(min_length=1, max_length=50)

    invoice_id: Optional[UUID] = None
    reference_number: Optional[str] = None

    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    voyage_number: Optional[str] = None
    notes: Optional[str] = None


class ApprovalDecision(BaseModel):
    decision: str = Field(min_length=1, max_length=20)
    comments: Optional[str] = None


# ============================================================
# DATABASE FOUNDATION
# ============================================================

def init_finance_tables():
    global _tables_ready

    if _tables_ready:
        return

    with _tables_lock:
        if _tables_ready:
            return

        _create_finance_tables()
        _tables_ready = True


def _create_finance_tables():
    engine = get_engine()

    with engine.begin() as conn:

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS finance_accounts (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                account_code VARCHAR(50) NOT NULL,
                account_name VARCHAR(255) NOT NULL,
                account_type VARCHAR(50) NOT NULL,
                parent_account_code VARCHAR(50),
                currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                notes TEXT,
                status VARCHAR(30) NOT NULL DEFAULT 'active',
                created_by VARCHAR(255),
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE (tenant_id, account_code)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS finance_cost_centers (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                code VARCHAR(50) NOT NULL,
                name VARCHAR(255) NOT NULL,
                department VARCHAR(100),
                vessel_id VARCHAR(255),
                vessel_name VARCHAR(255),
                notes TEXT,
                status VARCHAR(30) NOT NULL DEFAULT 'active',
                created_by VARCHAR(255),
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE (tenant_id, code)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS finance_periods (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                period_name VARCHAR(100) NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'open',
                created_by VARCHAR(255),
                created_at TIMESTAMPTZ NOT NULL,
                UNIQUE (tenant_id, period_name)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS finance_budgets (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                fiscal_year INTEGER NOT NULL,
                cost_center_code VARCHAR(50) NOT NULL,
                account_code VARCHAR(50) NOT NULL,
                budget_type VARCHAR(20) NOT NULL,
                currency VARCHAR(10) NOT NULL,
                amount DOUBLE PRECISION NOT NULL,
                committed_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
                actual_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
                vessel_id VARCHAR(255),
                vessel_name VARCHAR(255),
                notes TEXT,
                status VARCHAR(30) NOT NULL DEFAULT 'active',
                created_by VARCHAR(255),
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE (
                    tenant_id,
                    fiscal_year,
                    cost_center_code,
                    account_code,
                    budget_type
                )
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS finance_journals (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                journal_number VARCHAR(100) NOT NULL,
                journal_date DATE NOT NULL,
                description TEXT NOT NULL,
                reference_type VARCHAR(100),
                reference_id VARCHAR(255),
                reference_number VARCHAR(255),
                currency VARCHAR(10) NOT NULL,
                total_debit DOUBLE PRECISION NOT NULL,
                total_credit DOUBLE PRECISION NOT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'draft',
                created_by VARCHAR(255),
                approved_by VARCHAR(255),
                approved_at TIMESTAMPTZ,
                posted_by VARCHAR(255),
                posted_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE (tenant_id, journal_number)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS finance_journal_lines (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                journal_id UUID NOT NULL,
                line_number INTEGER NOT NULL,
                account_code VARCHAR(50) NOT NULL,
                description TEXT,
                debit DOUBLE PRECISION NOT NULL DEFAULT 0,
                credit DOUBLE PRECISION NOT NULL DEFAULT 0,
                cost_center_code VARCHAR(50),
                vessel_id VARCHAR(255),
                vessel_name VARCHAR(255),
                voyage_number VARCHAR(100),
                created_at TIMESTAMPTZ NOT NULL
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_finance_journal_lines
            ON finance_journal_lines
            (tenant_id, journal_id, line_number)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS finance_vendor_invoices (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                invoice_number VARCHAR(100) NOT NULL,
                vendor_name VARCHAR(255) NOT NULL,
                vendor_invoice_number VARCHAR(100) NOT NULL,
                invoice_date DATE NOT NULL,
                due_date DATE,
                currency VARCHAR(10) NOT NULL,
                amount DOUBLE PRECISION NOT NULL,
                paid_amount DOUBLE PRECISION NOT NULL DEFAULT 0,

                po_id VARCHAR(255),
                po_number VARCHAR(100),
                goods_receipt_id VARCHAR(255),
                gr_number VARCHAR(100),

                expense_account_code VARCHAR(50) NOT NULL,
                payable_account_code VARCHAR(50) NOT NULL,
                cost_center_code VARCHAR(50),

                vessel_id VARCHAR(255),
                vessel_name VARCHAR(255),
                voyage_number VARCHAR(100),

                notes TEXT,
                status VARCHAR(30) NOT NULL DEFAULT 'pending_approval',
                journal_id UUID,

                created_by VARCHAR(255),
                approved_by VARCHAR(255),
                approved_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,

                UNIQUE (
                    tenant_id,
                    vendor_name,
                    vendor_invoice_number
                )
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS finance_customer_invoices (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                invoice_number VARCHAR(100) NOT NULL,
                customer_name VARCHAR(255) NOT NULL,
                customer_invoice_number VARCHAR(100) NOT NULL,
                invoice_date DATE NOT NULL,
                due_date DATE,
                currency VARCHAR(10) NOT NULL,
                amount DOUBLE PRECISION NOT NULL,
                received_amount DOUBLE PRECISION NOT NULL DEFAULT 0,

                revenue_account_code VARCHAR(50) NOT NULL,
                receivable_account_code VARCHAR(50) NOT NULL,

                vessel_id VARCHAR(255),
                vessel_name VARCHAR(255),
                voyage_number VARCHAR(100),
                charter_reference VARCHAR(255),

                notes TEXT,
                status VARCHAR(30) NOT NULL DEFAULT 'pending_approval',
                journal_id UUID,

                created_by VARCHAR(255),
                approved_by VARCHAR(255),
                approved_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,

                UNIQUE (
                    tenant_id,
                    customer_name,
                    customer_invoice_number
                )
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS finance_payments (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                payment_number VARCHAR(100) NOT NULL,
                payment_date DATE NOT NULL,
                payment_type VARCHAR(30) NOT NULL,
                amount DOUBLE PRECISION NOT NULL,
                currency VARCHAR(10) NOT NULL,

                counterparty VARCHAR(255) NOT NULL,
                bank_account_code VARCHAR(50) NOT NULL,
                offset_account_code VARCHAR(50) NOT NULL,

                invoice_id UUID,
                reference_number VARCHAR(255),

                vessel_id VARCHAR(255),
                vessel_name VARCHAR(255),
                voyage_number VARCHAR(100),

                notes TEXT,
                status VARCHAR(30) NOT NULL DEFAULT 'posted',
                journal_id UUID,

                created_by VARCHAR(255),
                created_at TIMESTAMPTZ NOT NULL,

                UNIQUE (tenant_id, payment_number)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS finance_audit_log (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                entity_type VARCHAR(100) NOT NULL,
                entity_id VARCHAR(255) NOT NULL,
                action VARCHAR(100) NOT NULL,
                details TEXT,
                performed_by VARCHAR(255),
                performed_at TIMESTAMPTZ NOT NULL
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_finance_audit
            ON finance_audit_log
            (tenant_id, entity_type, entity_id, performed_at DESC)
        """))


def audit_event(
    conn,
    identity,
    entity_type,
    entity_id,
    action,
    details=None,
):
    conn.execute(
        text("""
            INSERT INTO finance_audit_log (
                id,
                tenant_id,
                entity_type,
                entity_id,
                action,
                details,
                performed_by,
                performed_at
            )
            VALUES (
                :id,
                :tenant_id,
                :entity_type,
                :entity_id,
                :action,
                :details,
                :performed_by,
                :performed_at
            )
        """),
        {
            "id": uuid4(),
            "tenant_id": identity.tenant_id,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "action": action,
            "details": details,
            "performed_by": identity.user_id,
            "performed_at": utc_now(),
        },
    )


def account_exists(conn, tenant_id, account_code):
    return conn.execute(
        text("""
            SELECT id
            FROM finance_accounts
            WHERE tenant_id = :tenant_id
              AND account_code = :account_code
              AND status = 'active'
        """),
        {
            "tenant_id": tenant_id,
            "account_code": account_code,
        },
    ).first() is not None


def create_system_journal(
    conn,
    identity,
    journal_date,
    description,
    currency,
    debit_account,
    credit_account,
    amount,
    reference_type=None,
    reference_id=None,
    reference_number=None,
    cost_center_code=None,
    vessel_id=None,
    vessel_name=None,
    voyage_number=None,
):
    if amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Journal amount must be greater than zero.",
        )

    for account_code in (debit_account, credit_account):
        if not account_exists(
            conn,
            identity.tenant_id,
            account_code,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Account {account_code} does not exist.",
            )

    now = utc_now()
    journal_id = uuid4()
    journal_number = (
        f"JV-{now.strftime('%Y%m%d')}-"
        f"{str(journal_id).split('-')[0].upper()}"
    )

    conn.execute(
        text("""
            INSERT INTO finance_journals (
                id,
                tenant_id,
                journal_number,
                journal_date,
                description,
                reference_type,
                reference_id,
                reference_number,
                currency,
                total_debit,
                total_credit,
                status,
                created_by,
                approved_by,
                approved_at,
                posted_by,
                posted_at,
                created_at,
                updated_at
            )
            VALUES (
                :id,
                :tenant_id,
                :journal_number,
                :journal_date,
                :description,
                :reference_type,
                :reference_id,
                :reference_number,
                :currency,
                :amount,
                :amount,
                'posted',
                :created_by,
                :approved_by,
                :approved_at,
                :posted_by,
                :posted_at,
                :created_at,
                :updated_at
            )
        """),
        {
            "id": journal_id,
            "tenant_id": identity.tenant_id,
            "journal_number": journal_number,
            "journal_date": journal_date,
            "description": description,
            "reference_type": reference_type,
            "reference_id": reference_id,
            "reference_number": reference_number,
            "currency": currency,
            "amount": amount,
            "created_by": identity.user_id,
            "approved_by": identity.user_id,
            "approved_at": now,
            "posted_by": identity.user_id,
            "posted_at": now,
            "created_at": now,
            "updated_at": now,
        },
    )

    for line_number, account_code, debit, credit in (
        (1, debit_account, amount, 0),
        (2, credit_account, 0, amount),
    ):
        conn.execute(
            text("""
                INSERT INTO finance_journal_lines (
                    id,
                    tenant_id,
                    journal_id,
                    line_number,
                    account_code,
                    description,
                    debit,
                    credit,
                    cost_center_code,
                    vessel_id,
                    vessel_name,
                    voyage_number,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :journal_id,
                    :line_number,
                    :account_code,
                    :description,
                    :debit,
                    :credit,
                    :cost_center_code,
                    :vessel_id,
                    :vessel_name,
                    :voyage_number,
                    :created_at
                )
            """),
            {
                "id": uuid4(),
                "tenant_id": identity.tenant_id,
                "journal_id": journal_id,
                "line_number": line_number,
                "account_code": account_code,
                "description": description,
                "debit": debit,
                "credit": credit,
                "cost_center_code": cost_center_code,
                "vessel_id": vessel_id,
                "vessel_name": vessel_name,
                "voyage_number": voyage_number,
                "created_at": now,
            },
        )

    return journal_id, journal_number

# ============================================================
# CHART OF ACCOUNTS
# ============================================================

@router.post(
    "/accounts",
    status_code=status.HTTP_201_CREATED,
    summary="Create Chart of Account",
)
def create_account(
    payload: AccountCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()

    account_type = payload.account_type.strip().lower()
    valid_types = {
        "asset",
        "liability",
        "equity",
        "revenue",
        "expense",
    }

    if account_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid account type.",
        )

    engine = get_engine()
    account_id = uuid4()
    now = utc_now()

    with engine.begin() as conn:
        existing = conn.execute(
            text("""
                SELECT id
                FROM finance_accounts
                WHERE tenant_id = :tenant_id
                  AND account_code = :account_code
            """),
            {
                "tenant_id": identity.tenant_id,
                "account_code": payload.account_code,
            },
        ).first()

        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account code already exists.",
            )

        conn.execute(
            text("""
                INSERT INTO finance_accounts (
                    id,
                    tenant_id,
                    account_code,
                    account_name,
                    account_type,
                    parent_account_code,
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
                    :account_code,
                    :account_name,
                    :account_type,
                    :parent_account_code,
                    :currency,
                    :notes,
                    'active',
                    :created_by,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "id": account_id,
                "tenant_id": identity.tenant_id,
                "account_code": payload.account_code,
                "account_name": payload.account_name,
                "account_type": account_type,
                "parent_account_code": payload.parent_account_code,
                "currency": payload.currency.upper(),
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

        audit_event(
            conn,
            identity,
            "account",
            account_id,
            "created",
            payload.account_code,
        )

    return {
        "id": str(account_id),
        "account_code": payload.account_code,
        "account_name": payload.account_name,
        "account_type": account_type,
        "status": "active",
    }


@router.get(
    "/accounts",
    summary="List Chart of Accounts",
)
def list_accounts(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM finance_accounts
                WHERE tenant_id = :tenant_id
                ORDER BY account_code
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# ============================================================
# COST CENTERS
# ============================================================

@router.post(
    "/cost-centers",
    status_code=status.HTTP_201_CREATED,
    summary="Create Cost Center",
)
def create_cost_center(
    payload: CostCenterCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()

    engine = get_engine()
    item_id = uuid4()
    now = utc_now()

    with engine.begin() as conn:
        existing = conn.execute(
            text("""
                SELECT id
                FROM finance_cost_centers
                WHERE tenant_id = :tenant_id
                  AND code = :code
            """),
            {
                "tenant_id": identity.tenant_id,
                "code": payload.code,
            },
        ).first()

        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cost center already exists.",
            )

        conn.execute(
            text("""
                INSERT INTO finance_cost_centers (
                    id,
                    tenant_id,
                    code,
                    name,
                    department,
                    vessel_id,
                    vessel_name,
                    notes,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :code,
                    :name,
                    :department,
                    :vessel_id,
                    :vessel_name,
                    :notes,
                    'active',
                    :created_by,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "id": item_id,
                "tenant_id": identity.tenant_id,
                "code": payload.code,
                "name": payload.name,
                "department": payload.department,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(item_id),
        "code": payload.code,
        "name": payload.name,
        "status": "active",
    }


@router.get(
    "/cost-centers",
    summary="List Cost Centers",
)
def list_cost_centers(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM finance_cost_centers
                WHERE tenant_id = :tenant_id
                ORDER BY code
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# ============================================================
# FINANCIAL PERIODS
# ============================================================

@router.post(
    "/periods",
    status_code=status.HTTP_201_CREATED,
    summary="Create Financial Period",
)
def create_financial_period(
    payload: FinancialPeriodCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()

    if payload.end_date < payload.start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date cannot be before start date.",
        )

    engine = get_engine()
    period_id = uuid4()
    now = utc_now()

    with engine.begin() as conn:
        existing = conn.execute(
            text("""
                SELECT id
                FROM finance_periods
                WHERE tenant_id = :tenant_id
                  AND period_name = :period_name
            """),
            {
                "tenant_id": identity.tenant_id,
                "period_name": payload.period_name,
            },
        ).first()

        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Financial period already exists.",
            )

        conn.execute(
            text("""
                INSERT INTO finance_periods (
                    id,
                    tenant_id,
                    period_name,
                    start_date,
                    end_date,
                    status,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :period_name,
                    :start_date,
                    :end_date,
                    'open',
                    :created_by,
                    :created_at
                )
            """),
            {
                "id": period_id,
                "tenant_id": identity.tenant_id,
                "period_name": payload.period_name,
                "start_date": payload.start_date,
                "end_date": payload.end_date,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

    return {
        "id": str(period_id),
        "period_name": payload.period_name,
        "status": "open",
    }


# ============================================================
# BUDGET / OPEX / CAPEX
# ============================================================

@router.post(
    "/budgets",
    status_code=status.HTTP_201_CREATED,
    summary="Create OPEX CAPEX Budget",
)
def create_budget(
    payload: BudgetCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()

    budget_type = payload.budget_type.upper()

    if budget_type not in {"OPEX", "CAPEX"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Budget type must be OPEX or CAPEX.",
        )

    engine = get_engine()
    budget_id = uuid4()
    now = utc_now()

    with engine.begin() as conn:
        if not account_exists(
            conn,
            identity.tenant_id,
            payload.account_code,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Budget account does not exist.",
            )

        duplicate = conn.execute(
            text("""
                SELECT id
                FROM finance_budgets
                WHERE tenant_id = :tenant_id
                  AND fiscal_year = :fiscal_year
                  AND cost_center_code = :cost_center_code
                  AND account_code = :account_code
                  AND budget_type = :budget_type
            """),
            {
                "tenant_id": identity.tenant_id,
                "fiscal_year": payload.fiscal_year,
                "cost_center_code": payload.cost_center_code,
                "account_code": payload.account_code,
                "budget_type": budget_type,
            },
        ).first()

        if duplicate:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Budget already exists.",
            )

        conn.execute(
            text("""
                INSERT INTO finance_budgets (
                    id,
                    tenant_id,
                    fiscal_year,
                    cost_center_code,
                    account_code,
                    budget_type,
                    currency,
                    amount,
                    committed_amount,
                    actual_amount,
                    vessel_id,
                    vessel_name,
                    notes,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :fiscal_year,
                    :cost_center_code,
                    :account_code,
                    :budget_type,
                    :currency,
                    :amount,
                    0,
                    0,
                    :vessel_id,
                    :vessel_name,
                    :notes,
                    'active',
                    :created_by,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "id": budget_id,
                "tenant_id": identity.tenant_id,
                "fiscal_year": payload.fiscal_year,
                "cost_center_code": payload.cost_center_code,
                "account_code": payload.account_code,
                "budget_type": budget_type,
                "currency": payload.currency.upper(),
                "amount": payload.amount,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(budget_id),
        "fiscal_year": payload.fiscal_year,
        "budget_type": budget_type,
        "amount": payload.amount,
        "currency": payload.currency.upper(),
        "status": "active",
    }


@router.get(
    "/budgets",
    summary="Budget Control",
)
def list_budgets(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *,
                    amount
                    - committed_amount
                    - actual_amount AS available_amount
                FROM finance_budgets
                WHERE tenant_id = :tenant_id
                ORDER BY fiscal_year DESC,
                         cost_center_code,
                         account_code
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# ============================================================
# MANUAL JOURNAL / DOUBLE ENTRY
# ============================================================

@router.post(
    "/journals",
    status_code=status.HTTP_201_CREATED,
    summary="Create Double Entry Journal",
)
def create_journal(
    payload: JournalCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()

    if len(payload.lines) < 2:
        raise HTTPException(
            status_code=422,
            detail="Journal must contain at least two lines.",
        )

    total_debit = round(
        sum(line.debit for line in payload.lines),
        2,
    )
    total_credit = round(
        sum(line.credit for line in payload.lines),
        2,
    )

    if total_debit <= 0 or total_credit <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Journal must contain debit and credit.",
        )

    if abs(total_debit - total_credit) > 0.01:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Journal is not balanced.",
        )

    for line in payload.lines:
        if line.debit > 0 and line.credit > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "A journal line cannot contain both "
                    "debit and credit."
                ),
            )

        if line.debit == 0 and line.credit == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Journal line amount cannot be zero.",
            )

    engine = get_engine()
    journal_id = uuid4()
    now = utc_now()

    journal_number = (
        f"JV-{now.strftime('%Y%m%d')}-"
        f"{str(journal_id).split('-')[0].upper()}"
    )

    with engine.begin() as conn:
        for line in payload.lines:
            if not account_exists(
                conn,
                identity.tenant_id,
                line.account_code,
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Account {line.account_code} "
                        "does not exist."
                    ),
                )

        conn.execute(
            text("""
                INSERT INTO finance_journals (
                    id,
                    tenant_id,
                    journal_number,
                    journal_date,
                    description,
                    reference_type,
                    reference_id,
                    reference_number,
                    currency,
                    total_debit,
                    total_credit,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :journal_number,
                    :journal_date,
                    :description,
                    :reference_type,
                    :reference_id,
                    :reference_number,
                    :currency,
                    :total_debit,
                    :total_credit,
                    'draft',
                    :created_by,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "id": journal_id,
                "tenant_id": identity.tenant_id,
                "journal_number": journal_number,
                "journal_date": payload.journal_date,
                "description": payload.description,
                "reference_type": payload.reference_type,
                "reference_id": payload.reference_id,
                "reference_number": payload.reference_number,
                "currency": payload.currency.upper(),
                "total_debit": total_debit,
                "total_credit": total_credit,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

        for index, line in enumerate(
            payload.lines,
            start=1,
        ):
            conn.execute(
                text("""
                    INSERT INTO finance_journal_lines (
                        id,
                        tenant_id,
                        journal_id,
                        line_number,
                        account_code,
                        description,
                        debit,
                        credit,
                        cost_center_code,
                        vessel_id,
                        vessel_name,
                        voyage_number,
                        created_at
                    )
                    VALUES (
                        :id,
                        :tenant_id,
                        :journal_id,
                        :line_number,
                        :account_code,
                        :description,
                        :debit,
                        :credit,
                        :cost_center_code,
                        :vessel_id,
                        :vessel_name,
                        :voyage_number,
                        :created_at
                    )
                """),
                {
                    "id": uuid4(),
                    "tenant_id": identity.tenant_id,
                    "journal_id": journal_id,
                    "line_number": index,
                    "account_code": line.account_code,
                    "description": line.description,
                    "debit": line.debit,
                    "credit": line.credit,
                    "cost_center_code": line.cost_center_code,
                    "vessel_id": line.vessel_id,
                    "vessel_name": line.vessel_name,
                    "voyage_number": line.voyage_number,
                    "created_at": now,
                },
            )

        audit_event(
            conn,
            identity,
            "journal",
            journal_id,
            "created",
            journal_number,
        )

    return {
        "id": str(journal_id),
        "journal_number": journal_number,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "status": "draft",
    }


@router.post(
    "/journals/{journal_id}/decision",
    summary="Approve Reject Post Journal",
)
def journal_decision(
    journal_id: UUID,
    payload: ApprovalDecision,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()

    decision = payload.decision.strip().lower()

    if decision not in {"approve", "reject", "post"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Decision must be approve, reject, or post.",
        )

    engine = get_engine()
    now = utc_now()

    with engine.begin() as conn:
        journal = conn.execute(
            text("""
                SELECT *
                FROM finance_journals
                WHERE id = :journal_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "journal_id": journal_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if journal is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Journal not found.",
            )

        if decision == "approve":
            if journal["status"] != "draft":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Only draft journals can be approved.",
                )

            new_status = "approved"

            conn.execute(
                text("""
                    UPDATE finance_journals
                    SET status = :status,
                        approved_by = :user_id,
                        approved_at = :now,
                        updated_at = :now
                    WHERE id = :journal_id
                      AND tenant_id = :tenant_id
                """),
                {
                    "status": new_status,
                    "user_id": identity.user_id,
                    "now": now,
                    "journal_id": journal_id,
                    "tenant_id": identity.tenant_id,
                },
            )

        elif decision == "post":
            if journal["status"] not in {
                "approved",
                "draft",
            }:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Journal cannot be posted.",
                )

            new_status = "posted"

            conn.execute(
                text("""
                    UPDATE finance_journals
                    SET status = 'posted',
                        posted_by = :user_id,
                        posted_at = :now,
                        updated_at = :now
                    WHERE id = :journal_id
                      AND tenant_id = :tenant_id
                """),
                {
                    "user_id": identity.user_id,
                    "now": now,
                    "journal_id": journal_id,
                    "tenant_id": identity.tenant_id,
                },
            )

        else:
            if journal["status"] not in {
                "draft",
                "approved",
            }:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Journal cannot be rejected.",
                )

            new_status = "rejected"

            conn.execute(
                text("""
                    UPDATE finance_journals
                    SET status = 'rejected',
                        updated_at = :now
                    WHERE id = :journal_id
                      AND tenant_id = :tenant_id
                """),
                {
                    "now": now,
                    "journal_id": journal_id,
                    "tenant_id": identity.tenant_id,
                },
            )

        audit_event(
            conn,
            identity,
            "journal",
            journal_id,
            decision,
            payload.comments,
        )

    return {
        "id": str(journal_id),
        "status": new_status,
    }


@router.get(
    "/journals",
    summary="List Journals",
)
def list_journals(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM finance_journals
                WHERE tenant_id = :tenant_id
                ORDER BY journal_date DESC,
                         created_at DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.get(
    "/general-ledger",
    summary="General Ledger",
)
def general_ledger(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    j.journal_number,
                    j.journal_date,
                    j.description AS journal_description,
                    j.reference_type,
                    j.reference_number,
                    l.line_number,
                    l.account_code,
                    a.account_name,
                    a.account_type,
                    l.description,
                    l.debit,
                    l.credit,
                    l.cost_center_code,
                    l.vessel_id,
                    l.vessel_name,
                    l.voyage_number
                FROM finance_journals j
                JOIN finance_journal_lines l
                  ON l.journal_id = j.id
                 AND l.tenant_id = j.tenant_id
                LEFT JOIN finance_accounts a
                  ON a.account_code = l.account_code
                 AND a.tenant_id = l.tenant_id
                WHERE j.tenant_id = :tenant_id
                  AND j.status = 'posted'
                ORDER BY
                    j.journal_date DESC,
                    j.created_at DESC,
                    l.line_number
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]

# ============================================================
# ACCOUNTS PAYABLE / VENDOR INVOICE
# ============================================================

@router.post(
    "/ap/vendor-invoices",
    status_code=status.HTTP_201_CREATED,
    summary="Create Vendor Invoice",
)
def create_vendor_invoice(
    payload: VendorInvoiceCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()

    engine = get_engine()
    invoice_id = uuid4()
    now = utc_now()

    invoice_number = (
        f"AP-{now.strftime('%Y%m%d')}-"
        f"{str(invoice_id).split('-')[0].upper()}"
    )

    with engine.begin() as conn:
        for account_code in (
            payload.expense_account_code,
            payload.payable_account_code,
        ):
            if not account_exists(
                conn,
                identity.tenant_id,
                account_code,
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Account {account_code} does not exist.",
                )

        duplicate = conn.execute(
            text("""
                SELECT id
                FROM finance_vendor_invoices
                WHERE tenant_id = :tenant_id
                  AND vendor_name = :vendor_name
                  AND vendor_invoice_number =
                      :vendor_invoice_number
            """),
            {
                "tenant_id": identity.tenant_id,
                "vendor_name": payload.vendor_name,
                "vendor_invoice_number":
                    payload.vendor_invoice_number,
            },
        ).first()

        if duplicate:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Vendor invoice already exists.",
            )

        conn.execute(
            text("""
                INSERT INTO finance_vendor_invoices (
                    id,
                    tenant_id,
                    invoice_number,
                    vendor_name,
                    vendor_invoice_number,
                    invoice_date,
                    due_date,
                    currency,
                    amount,
                    paid_amount,
                    po_id,
                    po_number,
                    goods_receipt_id,
                    gr_number,
                    expense_account_code,
                    payable_account_code,
                    cost_center_code,
                    vessel_id,
                    vessel_name,
                    voyage_number,
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
                    :vendor_name,
                    :vendor_invoice_number,
                    :invoice_date,
                    :due_date,
                    :currency,
                    :amount,
                    0,
                    :po_id,
                    :po_number,
                    :goods_receipt_id,
                    :gr_number,
                    :expense_account_code,
                    :payable_account_code,
                    :cost_center_code,
                    :vessel_id,
                    :vessel_name,
                    :voyage_number,
                    :notes,
                    'pending_approval',
                    :created_by,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "id": invoice_id,
                "tenant_id": identity.tenant_id,
                "invoice_number": invoice_number,
                "vendor_name": payload.vendor_name,
                "vendor_invoice_number":
                    payload.vendor_invoice_number,
                "invoice_date": payload.invoice_date,
                "due_date": payload.due_date,
                "currency": payload.currency.upper(),
                "amount": payload.amount,
                "po_id": payload.po_id,
                "po_number": payload.po_number,
                "goods_receipt_id": payload.goods_receipt_id,
                "gr_number": payload.gr_number,
                "expense_account_code":
                    payload.expense_account_code,
                "payable_account_code":
                    payload.payable_account_code,
                "cost_center_code": payload.cost_center_code,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "voyage_number": payload.voyage_number,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

        audit_event(
            conn,
            identity,
            "vendor_invoice",
            invoice_id,
            "created",
            invoice_number,
        )

    return {
        "id": str(invoice_id),
        "invoice_number": invoice_number,
        "vendor_name": payload.vendor_name,
        "amount": payload.amount,
        "currency": payload.currency.upper(),
        "status": "pending_approval",
    }


@router.post(
    "/ap/vendor-invoices/{invoice_id}/decision",
    summary="Approve Reject Vendor Invoice",
)
def vendor_invoice_decision(
    invoice_id: UUID,
    payload: ApprovalDecision,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()

    decision = payload.decision.strip().lower()

    if decision not in {"approve", "reject"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Decision must be approve or reject.",
        )

    engine = get_engine()
    now = utc_now()

    with engine.begin() as conn:
        invoice = conn.execute(
            text("""
                SELECT *
                FROM finance_vendor_invoices
                WHERE id = :invoice_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "invoice_id": invoice_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if invoice is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vendor invoice not found.",
            )

        if invoice["status"] != "pending_approval":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Vendor invoice has already been decided.",
            )

        if decision == "approve":
            journal_id, _ = create_system_journal(
                conn=conn,
                identity=identity,
                journal_date=invoice["invoice_date"],
                description=(
                    f"Vendor invoice "
                    f"{invoice['vendor_invoice_number']}"
                ),
                currency=invoice["currency"],
                debit_account=invoice["expense_account_code"],
                credit_account=invoice["payable_account_code"],
                amount=float(invoice["amount"]),
                reference_type="vendor_invoice",
                reference_id=str(invoice_id),
                reference_number=invoice["invoice_number"],
                cost_center_code=invoice["cost_center_code"],
                vessel_id=invoice["vessel_id"],
                vessel_name=invoice["vessel_name"],
                voyage_number=invoice["voyage_number"],
            )

            new_status = "approved"

            conn.execute(
                text("""
                    UPDATE finance_vendor_invoices
                    SET status = 'approved',
                        journal_id = :journal_id,
                        approved_by = :approved_by,
                        approved_at = :approved_at,
                        updated_at = :updated_at
                    WHERE id = :invoice_id
                      AND tenant_id = :tenant_id
                """),
                {
                    "journal_id": journal_id,
                    "approved_by": identity.user_id,
                    "approved_at": now,
                    "updated_at": now,
                    "invoice_id": invoice_id,
                    "tenant_id": identity.tenant_id,
                },
            )

        else:
            new_status = "rejected"

            conn.execute(
                text("""
                    UPDATE finance_vendor_invoices
                    SET status = 'rejected',
                        updated_at = :updated_at
                    WHERE id = :invoice_id
                      AND tenant_id = :tenant_id
                """),
                {
                    "updated_at": now,
                    "invoice_id": invoice_id,
                    "tenant_id": identity.tenant_id,
                },
            )

        audit_event(
            conn,
            identity,
            "vendor_invoice",
            invoice_id,
            decision,
            payload.comments,
        )

    return {
        "id": str(invoice_id),
        "status": new_status,
    }


@router.get(
    "/ap/vendor-invoices",
    summary="Accounts Payable Vendor Invoices",
)
def list_vendor_invoices(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *,
                    amount - paid_amount AS outstanding_amount
                FROM finance_vendor_invoices
                WHERE tenant_id = :tenant_id
                ORDER BY invoice_date DESC,
                         created_at DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# ============================================================
# ACCOUNTS RECEIVABLE
# ============================================================

@router.post(
    "/ar/customer-invoices",
    status_code=status.HTTP_201_CREATED,
    summary="Create Customer Invoice",
)
def create_customer_invoice(
    payload: CustomerInvoiceCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()

    engine = get_engine()
    invoice_id = uuid4()
    now = utc_now()

    invoice_number = (
        f"AR-{now.strftime('%Y%m%d')}-"
        f"{str(invoice_id).split('-')[0].upper()}"
    )

    with engine.begin() as conn:
        for account_code in (
            payload.revenue_account_code,
            payload.receivable_account_code,
        ):
            if not account_exists(
                conn,
                identity.tenant_id,
                account_code,
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Account {account_code} does not exist.",
                )

        duplicate = conn.execute(
            text("""
                SELECT id
                FROM finance_customer_invoices
                WHERE tenant_id = :tenant_id
                  AND customer_name = :customer_name
                  AND customer_invoice_number =
                      :customer_invoice_number
            """),
            {
                "tenant_id": identity.tenant_id,
                "customer_name": payload.customer_name,
                "customer_invoice_number":
                    payload.customer_invoice_number,
            },
        ).first()

        if duplicate:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Customer invoice already exists.",
            )

        conn.execute(
            text("""
                INSERT INTO finance_customer_invoices (
                    id,
                    tenant_id,
                    invoice_number,
                    customer_name,
                    customer_invoice_number,
                    invoice_date,
                    due_date,
                    currency,
                    amount,
                    received_amount,
                    revenue_account_code,
                    receivable_account_code,
                    vessel_id,
                    vessel_name,
                    voyage_number,
                    charter_reference,
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
                    :customer_name,
                    :customer_invoice_number,
                    :invoice_date,
                    :due_date,
                    :currency,
                    :amount,
                    0,
                    :revenue_account_code,
                    :receivable_account_code,
                    :vessel_id,
                    :vessel_name,
                    :voyage_number,
                    :charter_reference,
                    :notes,
                    'pending_approval',
                    :created_by,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "id": invoice_id,
                "tenant_id": identity.tenant_id,
                "invoice_number": invoice_number,
                "customer_name": payload.customer_name,
                "customer_invoice_number":
                    payload.customer_invoice_number,
                "invoice_date": payload.invoice_date,
                "due_date": payload.due_date,
                "currency": payload.currency.upper(),
                "amount": payload.amount,
                "revenue_account_code":
                    payload.revenue_account_code,
                "receivable_account_code":
                    payload.receivable_account_code,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "voyage_number": payload.voyage_number,
                "charter_reference": payload.charter_reference,
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(invoice_id),
        "invoice_number": invoice_number,
        "customer_name": payload.customer_name,
        "amount": payload.amount,
        "status": "pending_approval",
    }


@router.post(
    "/ar/customer-invoices/{invoice_id}/decision",
    summary="Approve Reject Customer Invoice",
)
def customer_invoice_decision(
    invoice_id: UUID,
    payload: ApprovalDecision,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()

    decision = payload.decision.strip().lower()

    if decision not in {"approve", "reject"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Decision must be approve or reject.",
        )

    engine = get_engine()
    now = utc_now()

    with engine.begin() as conn:
        invoice = conn.execute(
            text("""
                SELECT *
                FROM finance_customer_invoices
                WHERE id = :invoice_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "invoice_id": invoice_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if invoice is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Customer invoice not found.",
            )

        if invoice["status"] != "pending_approval":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Customer invoice has already been decided.",
            )

        if decision == "approve":
            journal_id, _ = create_system_journal(
                conn=conn,
                identity=identity,
                journal_date=invoice["invoice_date"],
                description=(
                    f"Customer invoice "
                    f"{invoice['customer_invoice_number']}"
                ),
                currency=invoice["currency"],
                debit_account=invoice["receivable_account_code"],
                credit_account=invoice["revenue_account_code"],
                amount=float(invoice["amount"]),
                reference_type="customer_invoice",
                reference_id=str(invoice_id),
                reference_number=invoice["invoice_number"],
                vessel_id=invoice["vessel_id"],
                vessel_name=invoice["vessel_name"],
                voyage_number=invoice["voyage_number"],
            )

            new_status = "approved"

            conn.execute(
                text("""
                    UPDATE finance_customer_invoices
                    SET status = 'approved',
                        journal_id = :journal_id,
                        approved_by = :approved_by,
                        approved_at = :approved_at,
                        updated_at = :updated_at
                    WHERE id = :invoice_id
                      AND tenant_id = :tenant_id
                """),
                {
                    "journal_id": journal_id,
                    "approved_by": identity.user_id,
                    "approved_at": now,
                    "updated_at": now,
                    "invoice_id": invoice_id,
                    "tenant_id": identity.tenant_id,
                },
            )

        else:
            new_status = "rejected"

            conn.execute(
                text("""
                    UPDATE finance_customer_invoices
                    SET status = 'rejected',
                        updated_at = :updated_at
                    WHERE id = :invoice_id
                      AND tenant_id = :tenant_id
                """),
                {
                    "updated_at": now,
                    "invoice_id": invoice_id,
                    "tenant_id": identity.tenant_id,
                },
            )

    return {
        "id": str(invoice_id),
        "status": new_status,
    }


@router.get(
    "/ar/customer-invoices",
    summary="Accounts Receivable Customer Invoices",
)
def list_customer_invoices(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *,
                    amount - received_amount AS outstanding_amount
                FROM finance_customer_invoices
                WHERE tenant_id = :tenant_id
                ORDER BY invoice_date DESC,
                         created_at DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# ============================================================
# PAYMENT / RECEIPT / CASH & BANK
# ============================================================

@router.post(
    "/payments",
    status_code=status.HTTP_201_CREATED,
    summary="Post Payment or Receipt",
)
def create_payment(
    payload: PaymentCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()

    payment_type = payload.payment_type.strip().lower()

    if payment_type not in {"payment", "receipt"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment type must be payment or receipt.",
        )

    engine = get_engine()
    payment_id = uuid4()
    now = utc_now()

    payment_number = (
        f"PAY-{now.strftime('%Y%m%d')}-"
        f"{str(payment_id).split('-')[0].upper()}"
    )

    with engine.begin() as conn:
        for account_code in (
            payload.bank_account_code,
            payload.offset_account_code,
        ):
            if not account_exists(
                conn,
                identity.tenant_id,
                account_code,
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Account {account_code} does not exist.",
                )

        if payment_type == "payment":
            debit_account = payload.offset_account_code
            credit_account = payload.bank_account_code
        else:
            debit_account = payload.bank_account_code
            credit_account = payload.offset_account_code

        journal_id, journal_number = create_system_journal(
            conn=conn,
            identity=identity,
            journal_date=payload.payment_date,
            description=(
                f"{payment_type.title()} - "
                f"{payload.counterparty}"
            ),
            currency=payload.currency.upper(),
            debit_account=debit_account,
            credit_account=credit_account,
            amount=payload.amount,
            reference_type=payment_type,
            reference_id=str(payment_id),
            reference_number=payload.reference_number,
            vessel_id=payload.vessel_id,
            vessel_name=payload.vessel_name,
            voyage_number=payload.voyage_number,
        )

        conn.execute(
            text("""
                INSERT INTO finance_payments (
                    id,
                    tenant_id,
                    payment_number,
                    payment_date,
                    payment_type,
                    amount,
                    currency,
                    counterparty,
                    bank_account_code,
                    offset_account_code,
                    invoice_id,
                    reference_number,
                    vessel_id,
                    vessel_name,
                    voyage_number,
                    notes,
                    status,
                    journal_id,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :payment_number,
                    :payment_date,
                    :payment_type,
                    :amount,
                    :currency,
                    :counterparty,
                    :bank_account_code,
                    :offset_account_code,
                    :invoice_id,
                    :reference_number,
                    :vessel_id,
                    :vessel_name,
                    :voyage_number,
                    :notes,
                    'posted',
                    :journal_id,
                    :created_by,
                    :created_at
                )
            """),
            {
                "id": payment_id,
                "tenant_id": identity.tenant_id,
                "payment_number": payment_number,
                "payment_date": payload.payment_date,
                "payment_type": payment_type,
                "amount": payload.amount,
                "currency": payload.currency.upper(),
                "counterparty": payload.counterparty,
                "bank_account_code": payload.bank_account_code,
                "offset_account_code": payload.offset_account_code,
                "invoice_id": payload.invoice_id,
                "reference_number": payload.reference_number,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "voyage_number": payload.voyage_number,
                "notes": payload.notes,
                "journal_id": journal_id,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

        if payload.invoice_id:
            if payment_type == "payment":
                invoice = conn.execute(
                    text("""
                        SELECT amount, paid_amount, status
                        FROM finance_vendor_invoices
                        WHERE id = :invoice_id
                          AND tenant_id = :tenant_id
                        FOR UPDATE
                    """),
                    {
                        "invoice_id": payload.invoice_id,
                        "tenant_id": identity.tenant_id,
                    },
                ).mappings().first()

                if invoice is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Vendor invoice not found.",
                    )

                if invoice["status"] not in {
                    "approved",
                    "partially_paid",
                }:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "Vendor invoice is not open for payment."
                        ),
                    )

                if payload.amount > (
                    float(invoice["amount"])
                    - float(invoice["paid_amount"])
                    + 0.01
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "Payment exceeds the outstanding "
                            "invoice amount."
                        ),
                    )

                if invoice:
                    new_paid = (
                        float(invoice["paid_amount"])
                        + payload.amount
                    )

                    new_status = (
                        "paid"
                        if new_paid >= float(invoice["amount"])
                        else "partially_paid"
                    )

                    conn.execute(
                        text("""
                            UPDATE finance_vendor_invoices
                            SET paid_amount = :paid_amount,
                                status = :status,
                                updated_at = :updated_at
                            WHERE id = :invoice_id
                              AND tenant_id = :tenant_id
                        """),
                        {
                            "paid_amount": new_paid,
                            "status": new_status,
                            "updated_at": now,
                            "invoice_id": payload.invoice_id,
                            "tenant_id": identity.tenant_id,
                        },
                    )

            else:
                invoice = conn.execute(
                    text("""
                        SELECT amount, received_amount, status
                        FROM finance_customer_invoices
                        WHERE id = :invoice_id
                          AND tenant_id = :tenant_id
                        FOR UPDATE
                    """),
                    {
                        "invoice_id": payload.invoice_id,
                        "tenant_id": identity.tenant_id,
                    },
                ).mappings().first()

                if invoice is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Customer invoice not found.",
                    )

                if invoice["status"] not in {
                    "approved",
                    "partially_paid",
                }:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "Customer invoice is not open "
                            "for receipt."
                        ),
                    )

                if payload.amount > (
                    float(invoice["amount"])
                    - float(invoice["received_amount"])
                    + 0.01
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "Receipt exceeds the outstanding "
                            "invoice amount."
                        ),
                    )

                if invoice:
                    new_received = (
                        float(invoice["received_amount"])
                        + payload.amount
                    )

                    new_status = (
                        "paid"
                        if new_received >= float(invoice["amount"])
                        else "partially_paid"
                    )

                    conn.execute(
                        text("""
                            UPDATE finance_customer_invoices
                            SET received_amount = :received_amount,
                                status = :status,
                                updated_at = :updated_at
                            WHERE id = :invoice_id
                              AND tenant_id = :tenant_id
                        """),
                        {
                            "received_amount": new_received,
                            "status": new_status,
                            "updated_at": now,
                            "invoice_id": payload.invoice_id,
                            "tenant_id": identity.tenant_id,
                        },
                    )

        audit_event(
            conn,
            identity,
            "payment",
            payment_id,
            "posted",
            journal_number,
        )

    return {
        "id": str(payment_id),
        "payment_number": payment_number,
        "payment_type": payment_type,
        "amount": payload.amount,
        "currency": payload.currency.upper(),
        "journal_number": journal_number,
        "status": "posted",
    }


@router.get(
    "/payments",
    summary="Cash Bank Payments and Receipts",
)
def list_payments(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM finance_payments
                WHERE tenant_id = :tenant_id
                ORDER BY payment_date DESC,
                         created_at DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# ============================================================
# VESSEL OPEX / VOYAGE ACCOUNTING
# ============================================================

@router.get(
    "/vessel-opex",
    summary="Vessel OPEX Intelligence",
)
def vessel_opex(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    l.vessel_id,
                    l.vessel_name,
                    l.account_code,
                    a.account_name,
                    SUM(l.debit - l.credit) AS net_amount
                FROM finance_journal_lines l
                JOIN finance_journals j
                  ON j.id = l.journal_id
                 AND j.tenant_id = l.tenant_id
                LEFT JOIN finance_accounts a
                  ON a.account_code = l.account_code
                 AND a.tenant_id = l.tenant_id
                WHERE l.tenant_id = :tenant_id
                  AND j.status = 'posted'
                  AND l.vessel_name IS NOT NULL
                  AND a.account_type = 'expense'
                GROUP BY
                    l.vessel_id,
                    l.vessel_name,
                    l.account_code,
                    a.account_name
                ORDER BY
                    l.vessel_name,
                    net_amount DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.get(
    "/voyage-accounting",
    summary="Voyage Accounting Intelligence",
)
def voyage_accounting(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    l.voyage_number,
                    l.vessel_name,
                    SUM(
                        CASE
                            WHEN a.account_type = 'revenue'
                            THEN l.credit - l.debit
                            ELSE 0
                        END
                    ) AS revenue,
                    SUM(
                        CASE
                            WHEN a.account_type = 'expense'
                            THEN l.debit - l.credit
                            ELSE 0
                        END
                    ) AS expense
                FROM finance_journal_lines l
                JOIN finance_journals j
                  ON j.id = l.journal_id
                 AND j.tenant_id = l.tenant_id
                LEFT JOIN finance_accounts a
                  ON a.account_code = l.account_code
                 AND a.tenant_id = l.tenant_id
                WHERE l.tenant_id = :tenant_id
                  AND j.status = 'posted'
                  AND l.voyage_number IS NOT NULL
                GROUP BY
                    l.voyage_number,
                    l.vessel_name
                ORDER BY l.voyage_number DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    result = []

    for row in rows:
        data = serialize_row(row)

        revenue = float(row["revenue"] or 0)
        expense = float(row["expense"] or 0)

        data["profit_loss"] = revenue - expense
        result.append(data)

    return result


# ============================================================
# TRIAL BALANCE
# ============================================================

@router.get(
    "/trial-balance",
    summary="Trial Balance",
)
def trial_balance(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    a.account_code,
                    a.account_name,
                    a.account_type,
                    COALESCE(t.debit, 0) AS debit,
                    COALESCE(t.credit, 0) AS credit
                FROM finance_accounts a
                LEFT JOIN (
                    SELECT
                        l.tenant_id,
                        l.account_code,
                        SUM(l.debit) AS debit,
                        SUM(l.credit) AS credit
                    FROM finance_journal_lines l
                    JOIN finance_journals j
                      ON j.id = l.journal_id
                     AND j.tenant_id = l.tenant_id
                    WHERE l.tenant_id = :tenant_id
                      AND j.status = 'posted'
                    GROUP BY
                        l.tenant_id,
                        l.account_code
                ) t
                  ON t.account_code = a.account_code
                 AND t.tenant_id = a.tenant_id
                WHERE a.tenant_id = :tenant_id
                  AND a.status = 'active'
                ORDER BY a.account_code
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# ============================================================
# FINANCE & ACCOUNTING COMMAND CENTER
# ============================================================

@router.get(
    "/dashboard",
    summary="Finance Accounting Command Center",
)
def finance_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_finance_tables()
    engine = get_engine()

    with engine.begin() as conn:
        ap = conn.execute(
            text("""
                SELECT
                    COALESCE(
                        SUM(amount - paid_amount),
                        0
                    ) AS outstanding
                FROM finance_vendor_invoices
                WHERE tenant_id = :tenant_id
                  AND status IN (
                      'approved',
                      'partially_paid'
                  )
            """),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

        ar = conn.execute(
            text("""
                SELECT
                    COALESCE(
                        SUM(amount - received_amount),
                        0
                    ) AS outstanding
                FROM finance_customer_invoices
                WHERE tenant_id = :tenant_id
                  AND status IN (
                      'approved',
                      'partially_paid'
                  )
            """),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

        budget = conn.execute(
            text("""
                SELECT
                    COALESCE(SUM(amount), 0) AS budget,
                    COALESCE(SUM(committed_amount), 0)
                        AS committed,
                    COALESCE(SUM(actual_amount), 0)
                        AS actual
                FROM finance_budgets
                WHERE tenant_id = :tenant_id
                  AND status = 'active'
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        posted_journals = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM finance_journals
                WHERE tenant_id = :tenant_id
                  AND status = 'posted'
            """),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

        pending_ap = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM finance_vendor_invoices
                WHERE tenant_id = :tenant_id
                  AND status = 'pending_approval'
            """),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

        pending_ar = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM finance_customer_invoices
                WHERE tenant_id = :tenant_id
                  AND status = 'pending_approval'
            """),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

    budget_amount = float(budget["budget"] or 0)
    committed = float(budget["committed"] or 0)
    actual = float(budget["actual"] or 0)

    return {
        "system": "MQEIP FINANCE & ACCOUNTING INTELLIGENCE",
        "accounts_payable_outstanding": float(ap or 0),
        "accounts_receivable_outstanding": float(ar or 0),
        "total_budget": budget_amount,
        "committed_budget": committed,
        "actual_budget": actual,
        "available_budget": (
            budget_amount - committed - actual
        ),
        "posted_journals": posted_journals,
        "pending_vendor_invoice_approvals": pending_ap,
        "pending_customer_invoice_approvals": pending_ar,
        "accounting_model": "DOUBLE_ENTRY",
        "tenant_isolation": True,
        "audit_traceability": True,
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
        rows = conn.execute(
            text("""
                SELECT *
                FROM finance_audit_log
                WHERE tenant_id = :tenant_id
                ORDER BY performed_at DESC
                LIMIT 1000
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


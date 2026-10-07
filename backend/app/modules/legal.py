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
    prefix="/api/v1/legal",
    tags=["Legal, Claims & Insurance Intelligence"],
)


# ============================================================
# REQUEST MODELS
# ============================================================

class LegalCaseCreate(BaseModel):
    case_number: str = Field(min_length=1, max_length=100)
    case_type: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    counterparty: Optional[str] = Field(default=None, max_length=255)
    jurisdiction: Optional[str] = Field(default=None, max_length=255)
    opened_date: date
    estimated_exposure: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(default="USD", max_length=10)
    responsible_person: Optional[str] = Field(default=None, max_length=255)


class LegalCaseStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=50)
    resolution: Optional[str] = None


class ClaimCreate(BaseModel):
    claim_number: str = Field(min_length=1, max_length=100)
    claim_type: str = Field(min_length=1, max_length=100)
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    incident_reference: Optional[str] = Field(default=None, max_length=255)
    claimant: Optional[str] = Field(default=None, max_length=255)
    claim_date: date
    claimed_amount: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(default="USD", max_length=10)
    description: Optional[str] = None


class ClaimStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=50)
    approved_amount: Optional[Decimal] = Field(default=None, ge=0)
    settlement_amount: Optional[Decimal] = Field(default=None, ge=0)
    remarks: Optional[str] = None


class InsurancePolicyCreate(BaseModel):
    policy_number: str = Field(min_length=1, max_length=150)
    policy_type: str = Field(min_length=1, max_length=100)
    insurer: str = Field(min_length=1, max_length=255)
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    effective_date: date
    expiry_date: date
    insured_value: Decimal = Field(default=Decimal("0"), ge=0)
    premium: Decimal = Field(default=Decimal("0"), ge=0)
    deductible: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(default="USD", max_length=10)
    coverage_details: Optional[str] = None


class ContractCreate(BaseModel):
    contract_number: str = Field(min_length=1, max_length=150)
    contract_type: str = Field(min_length=1, max_length=100)
    counterparty: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    effective_date: Optional[date] = None
    expiry_date: Optional[date] = None
    contract_value: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(default="USD", max_length=10)
    responsible_person: Optional[str] = Field(default=None, max_length=255)


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

def init_legal_tables():
    engine = get_engine()

    with engine.begin() as conn:

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS legal_cases (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    case_number VARCHAR(100) NOT NULL,
                    case_type VARCHAR(100) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    description TEXT,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    counterparty VARCHAR(255),
                    jurisdiction VARCHAR(255),
                    opened_date DATE NOT NULL,
                    estimated_exposure NUMERIC(18,2) NOT NULL DEFAULT 0,
                    currency VARCHAR(10) NOT NULL,
                    responsible_person VARCHAR(255),
                    status VARCHAR(50) NOT NULL DEFAULT 'open',
                    resolution TEXT,
                    closed_at TIMESTAMPTZ,
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (tenant_id, case_number)
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS legal_claims (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    claim_number VARCHAR(100) NOT NULL,
                    claim_type VARCHAR(100) NOT NULL,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    incident_reference VARCHAR(255),
                    claimant VARCHAR(255),
                    claim_date DATE NOT NULL,
                    claimed_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
                    approved_amount NUMERIC(18,2),
                    settlement_amount NUMERIC(18,2),
                    currency VARCHAR(10) NOT NULL,
                    description TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'reported',
                    remarks TEXT,
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (tenant_id, claim_number)
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS legal_insurance_policies (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    policy_number VARCHAR(150) NOT NULL,
                    policy_type VARCHAR(100) NOT NULL,
                    insurer VARCHAR(255) NOT NULL,
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    effective_date DATE NOT NULL,
                    expiry_date DATE NOT NULL,
                    insured_value NUMERIC(18,2) NOT NULL DEFAULT 0,
                    premium NUMERIC(18,2) NOT NULL DEFAULT 0,
                    deductible NUMERIC(18,2) NOT NULL DEFAULT 0,
                    currency VARCHAR(10) NOT NULL,
                    coverage_details TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'active',
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (tenant_id, policy_number)
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS legal_contracts (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    contract_number VARCHAR(150) NOT NULL,
                    contract_type VARCHAR(100) NOT NULL,
                    counterparty VARCHAR(255) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    effective_date DATE,
                    expiry_date DATE,
                    contract_value NUMERIC(18,2) NOT NULL DEFAULT 0,
                    currency VARCHAR(10) NOT NULL,
                    responsible_person VARCHAR(255),
                    status VARCHAR(50) NOT NULL DEFAULT 'active',
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
                CREATE INDEX IF NOT EXISTS idx_legal_cases_status
                ON legal_cases
                (tenant_id, status, opened_date)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_legal_claims_status
                ON legal_claims
                (tenant_id, status, claim_date)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_legal_policy_expiry
                ON legal_insurance_policies
                (tenant_id, expiry_date, status)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_legal_contract_expiry
                ON legal_contracts
                (tenant_id, expiry_date, status)
                """
            )
        )

  # ============================================================
# LEGAL CASE MANAGEMENT
# ============================================================

@router.get(
    "/cases",
    summary="List Legal Cases",
)
def list_legal_cases(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM legal_cases
                WHERE tenant_id = :tenant_id
                ORDER BY opened_date DESC, created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/cases",
    status_code=status.HTTP_201_CREATED,
    summary="Create Legal Case",
)
def create_legal_case(
    payload: LegalCaseCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()

    engine = get_engine()
    case_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        duplicate = conn.execute(
            text(
                """
                SELECT id
                FROM legal_cases
                WHERE tenant_id = :tenant_id
                  AND case_number = :case_number
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "case_number": payload.case_number,
            },
        ).first()

        if duplicate is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Legal case number already exists.",
            )

        conn.execute(
            text(
                """
                INSERT INTO legal_cases (
                    id,
                    tenant_id,
                    case_number,
                    case_type,
                    title,
                    description,
                    vessel_id,
                    vessel_name,
                    counterparty,
                    jurisdiction,
                    opened_date,
                    estimated_exposure,
                    currency,
                    responsible_person,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :case_number,
                    :case_type,
                    :title,
                    :description,
                    :vessel_id,
                    :vessel_name,
                    :counterparty,
                    :jurisdiction,
                    :opened_date,
                    :estimated_exposure,
                    :currency,
                    :responsible_person,
                    'open',
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": case_id,
                "tenant_id": identity.tenant_id,
                "case_number": payload.case_number,
                "case_type": payload.case_type,
                "title": payload.title,
                "description": payload.description,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "counterparty": payload.counterparty,
                "jurisdiction": payload.jurisdiction,
                "opened_date": payload.opened_date,
                "estimated_exposure":
                    payload.estimated_exposure,
                "currency": payload.currency.upper(),
                "responsible_person":
                    payload.responsible_person,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(case_id),
        "case_number": payload.case_number,
        "status": "open",
        "estimated_exposure": float(
            payload.estimated_exposure
        ),
        "currency": payload.currency.upper(),
    }


@router.get(
    "/cases/{case_id}",
    summary="Get Legal Case",
)
def get_legal_case(
    case_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT *
                FROM legal_cases
                WHERE id = :case_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "case_id": case_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Legal case not found.",
        )

    return serialize_row(row)


@router.post(
    "/cases/{case_id}/status",
    summary="Update Legal Case Status",
)
def update_legal_case_status(
    case_id: UUID,
    payload: LegalCaseStatusUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()

    new_status = payload.status.strip().lower()

    if new_status not in {
        "open",
        "under_review",
        "litigation",
        "settlement",
        "closed",
    }:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid legal case status.",
        )

    if new_status == "closed" and not payload.resolution:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resolution is required to close a legal case.",
        )

    engine = get_engine()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT id
                FROM legal_cases
                WHERE id = :case_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {
                "case_id": case_id,
                "tenant_id": identity.tenant_id,
            },
        ).first()

        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Legal case not found.",
            )

        conn.execute(
            text(
                """
                UPDATE legal_cases
                SET status = :status,
                    resolution = :resolution,
                    closed_at = :closed_at,
                    updated_at = :updated_at
                WHERE id = :case_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "status": new_status,
                "resolution": payload.resolution,
                "closed_at": (
                    now if new_status == "closed" else None
                ),
                "updated_at": now,
                "case_id": case_id,
                "tenant_id": identity.tenant_id,
            },
        )

    return {
        "id": str(case_id),
        "status": new_status,
        "updated_at": now.isoformat(),
    }

# ============================================================
# CLAIMS MANAGEMENT INTELLIGENCE
# ============================================================

@router.get(
    "/claims",
    summary="List Insurance and Legal Claims",
)
def list_claims(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM legal_claims
                WHERE tenant_id = :tenant_id
                ORDER BY claim_date DESC, created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/claims",
    status_code=status.HTTP_201_CREATED,
    summary="Register Insurance or Legal Claim",
)
def create_claim(
    payload: ClaimCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()

    engine = get_engine()
    claim_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        duplicate = conn.execute(
            text(
                """
                SELECT id
                FROM legal_claims
                WHERE tenant_id = :tenant_id
                  AND claim_number = :claim_number
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "claim_number": payload.claim_number,
            },
        ).first()

        if duplicate is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Claim number already exists.",
            )

        conn.execute(
            text(
                """
                INSERT INTO legal_claims (
                    id,
                    tenant_id,
                    claim_number,
                    claim_type,
                    vessel_id,
                    vessel_name,
                    incident_reference,
                    claimant,
                    claim_date,
                    claimed_amount,
                    currency,
                    description,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :claim_number,
                    :claim_type,
                    :vessel_id,
                    :vessel_name,
                    :incident_reference,
                    :claimant,
                    :claim_date,
                    :claimed_amount,
                    :currency,
                    :description,
                    'reported',
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": claim_id,
                "tenant_id": identity.tenant_id,
                "claim_number": payload.claim_number,
                "claim_type": payload.claim_type,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "incident_reference":
                    payload.incident_reference,
                "claimant": payload.claimant,
                "claim_date": payload.claim_date,
                "claimed_amount": payload.claimed_amount,
                "currency": payload.currency.upper(),
                "description": payload.description,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(claim_id),
        "claim_number": payload.claim_number,
        "claimed_amount": float(payload.claimed_amount),
        "currency": payload.currency.upper(),
        "status": "reported",
    }


@router.get(
    "/claims/{claim_id}",
    summary="Get Claim",
)
def get_claim(
    claim_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT *
                FROM legal_claims
                WHERE id = :claim_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "claim_id": claim_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Claim not found.",
        )

    return serialize_row(row)


@router.post(
    "/claims/{claim_id}/status",
    summary="Update Claim Status and Settlement",
)
def update_claim_status(
    claim_id: UUID,
    payload: ClaimStatusUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()

    new_status = payload.status.strip().lower()

    if new_status not in {
        "reported",
        "under_review",
        "approved",
        "rejected",
        "settled",
        "closed",
    }:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid claim status.",
        )

    if (
        payload.approved_amount is not None
        and payload.settlement_amount is not None
        and payload.settlement_amount > payload.approved_amount
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Settlement amount cannot exceed approved amount.",
        )

    engine = get_engine()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT id
                FROM legal_claims
                WHERE id = :claim_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {
                "claim_id": claim_id,
                "tenant_id": identity.tenant_id,
            },
        ).first()

        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Claim not found.",
            )

        conn.execute(
            text(
                """
                UPDATE legal_claims
                SET status = :status,
                    approved_amount = :approved_amount,
                    settlement_amount = :settlement_amount,
                    remarks = :remarks,
                    updated_at = :updated_at
                WHERE id = :claim_id
                  AND tenant_id = :tenant_id
                """
            ),
            {
                "status": new_status,
                "approved_amount": payload.approved_amount,
                "settlement_amount": payload.settlement_amount,
                "remarks": payload.remarks,
                "updated_at": now,
                "claim_id": claim_id,
                "tenant_id": identity.tenant_id,
            },
        )

    return {
        "id": str(claim_id),
        "status": new_status,
        "approved_amount": (
            float(payload.approved_amount)
            if payload.approved_amount is not None
            else None
        ),
        "settlement_amount": (
            float(payload.settlement_amount)
            if payload.settlement_amount is not None
            else None
        ),
    }

# ============================================================
# INSURANCE POLICY & COVERAGE INTELLIGENCE
# ============================================================

@router.get(
    "/insurance-policies",
    summary="List Insurance Policies",
)
def list_insurance_policies(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *,
                    CASE
                        WHEN expiry_date < CURRENT_DATE
                            THEN 'EXPIRED'
                        WHEN expiry_date <= CURRENT_DATE + 30
                            THEN 'EXPIRING_30_DAYS'
                        WHEN expiry_date <= CURRENT_DATE + 90
                            THEN 'EXPIRING_90_DAYS'
                        ELSE 'ACTIVE'
                    END AS expiry_intelligence
                FROM legal_insurance_policies
                WHERE tenant_id = :tenant_id
                ORDER BY expiry_date ASC, created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/insurance-policies",
    status_code=status.HTTP_201_CREATED,
    summary="Create Insurance Policy",
)
def create_insurance_policy(
    payload: InsurancePolicyCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()

    if payload.expiry_date < payload.effective_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Policy expiry date cannot be before effective date.",
        )

    policy_status = (
        "expired"
        if payload.expiry_date < date.today()
        else "active"
    )

    engine = get_engine()
    policy_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        duplicate = conn.execute(
            text(
                """
                SELECT id
                FROM legal_insurance_policies
                WHERE tenant_id = :tenant_id
                  AND policy_number = :policy_number
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "policy_number": payload.policy_number,
            },
        ).first()

        if duplicate is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Insurance policy number already exists.",
            )

        conn.execute(
            text(
                """
                INSERT INTO legal_insurance_policies (
                    id,
                    tenant_id,
                    policy_number,
                    policy_type,
                    insurer,
                    vessel_id,
                    vessel_name,
                    effective_date,
                    expiry_date,
                    insured_value,
                    premium,
                    deductible,
                    currency,
                    coverage_details,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :policy_number,
                    :policy_type,
                    :insurer,
                    :vessel_id,
                    :vessel_name,
                    :effective_date,
                    :expiry_date,
                    :insured_value,
                    :premium,
                    :deductible,
                    :currency,
                    :coverage_details,
                    :status,
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": policy_id,
                "tenant_id": identity.tenant_id,
                "policy_number": payload.policy_number,
                "policy_type": payload.policy_type,
                "insurer": payload.insurer,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "effective_date": payload.effective_date,
                "expiry_date": payload.expiry_date,
                "insured_value": payload.insured_value,
                "premium": payload.premium,
                "deductible": payload.deductible,
                "currency": payload.currency.upper(),
                "coverage_details": payload.coverage_details,
                "status": policy_status,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(policy_id),
        "policy_number": payload.policy_number,
        "insurer": payload.insurer,
        "expiry_date": payload.expiry_date.isoformat(),
        "status": policy_status,
    }


@router.get(
    "/insurance/intelligence",
    summary="Insurance Coverage and Expiry Intelligence",
)
def insurance_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()
    engine = get_engine()

    with engine.begin() as conn:
        metrics = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_policies,

                    COUNT(*) FILTER (
                        WHERE expiry_date < CURRENT_DATE
                    ) AS expired_policies,

                    COUNT(*) FILTER (
                        WHERE expiry_date >= CURRENT_DATE
                          AND expiry_date <= CURRENT_DATE + 30
                    ) AS expiring_30_days,

                    COUNT(*) FILTER (
                        WHERE expiry_date > CURRENT_DATE + 30
                          AND expiry_date <= CURRENT_DATE + 90
                    ) AS expiring_90_days

                FROM legal_insurance_policies
                WHERE tenant_id = :tenant_id
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        priority = conn.execute(
            text(
                """
                SELECT *
                FROM legal_insurance_policies
                WHERE tenant_id = :tenant_id
                  AND expiry_date <= CURRENT_DATE + 90
                ORDER BY expiry_date ASC
                LIMIT 100
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "total_policies": int(metrics["total_policies"]),
        "expired_policies": int(metrics["expired_policies"]),
        "expiring_30_days": int(metrics["expiring_30_days"]),
        "expiring_90_days": int(metrics["expiring_90_days"]),
        "priority_policies": [
            serialize_row(row)
            for row in priority
        ],
    }

# ============================================================
# CONTRACT & CORPORATE LEGAL INTELLIGENCE
# ============================================================

@router.get(
    "/contracts",
    summary="List Legal and Corporate Contracts",
)
def list_contracts(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *,
                    CASE
                        WHEN expiry_date IS NULL
                            THEN 'NO_EXPIRY'
                        WHEN expiry_date < CURRENT_DATE
                            THEN 'EXPIRED'
                        WHEN expiry_date <= CURRENT_DATE + 30
                            THEN 'EXPIRING_30_DAYS'
                        WHEN expiry_date <= CURRENT_DATE + 90
                            THEN 'EXPIRING_90_DAYS'
                        ELSE 'ACTIVE'
                    END AS expiry_intelligence
                FROM legal_contracts
                WHERE tenant_id = :tenant_id
                ORDER BY expiry_date ASC NULLS LAST,
                         created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/contracts",
    status_code=status.HTTP_201_CREATED,
    summary="Create Legal or Corporate Contract",
)
def create_contract(
    payload: ContractCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()

    if (
        payload.effective_date
        and payload.expiry_date
        and payload.expiry_date < payload.effective_date
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Contract expiry date cannot be before effective date.",
        )

    contract_status = "active"

    if (
        payload.expiry_date
        and payload.expiry_date < date.today()
    ):
        contract_status = "expired"

    engine = get_engine()
    contract_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        duplicate = conn.execute(
            text(
                """
                SELECT id
                FROM legal_contracts
                WHERE tenant_id = :tenant_id
                  AND contract_number = :contract_number
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "contract_number": payload.contract_number,
            },
        ).first()

        if duplicate is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Contract number already exists.",
            )

        conn.execute(
            text(
                """
                INSERT INTO legal_contracts (
                    id,
                    tenant_id,
                    contract_number,
                    contract_type,
                    counterparty,
                    title,
                    effective_date,
                    expiry_date,
                    contract_value,
                    currency,
                    responsible_person,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :contract_number,
                    :contract_type,
                    :counterparty,
                    :title,
                    :effective_date,
                    :expiry_date,
                    :contract_value,
                    :currency,
                    :responsible_person,
                    :status,
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": contract_id,
                "tenant_id": identity.tenant_id,
                "contract_number": payload.contract_number,
                "contract_type": payload.contract_type,
                "counterparty": payload.counterparty,
                "title": payload.title,
                "effective_date": payload.effective_date,
                "expiry_date": payload.expiry_date,
                "contract_value": payload.contract_value,
                "currency": payload.currency.upper(),
                "responsible_person":
                    payload.responsible_person,
                "status": contract_status,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(contract_id),
        "contract_number": payload.contract_number,
        "counterparty": payload.counterparty,
        "contract_value": float(payload.contract_value),
        "currency": payload.currency.upper(),
        "status": contract_status,
    }


@router.get(
    "/contracts/intelligence/expiry",
    summary="Contract Expiry Intelligence",
)
def contract_expiry_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM legal_contracts
                WHERE tenant_id = :tenant_id
                  AND expiry_date IS NOT NULL
                  AND expiry_date <= CURRENT_DATE + 90
                ORDER BY expiry_date ASC
                LIMIT 100
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    expired = 0
    expiring = 0

    for row in rows:
        if row["expiry_date"] < date.today():
            expired += 1
        else:
            expiring += 1

    return {
        "expired_contracts": expired,
        "expiring_within_90_days": expiring,
        "priority_contracts": [
            serialize_row(row)
            for row in rows
        ],
    }

# ============================================================
# LEGAL / CLAIMS / INSURANCE EXECUTIVE DASHBOARD
# ============================================================

@router.get(
    "/dashboard",
    summary="Legal Claims and Insurance Executive Dashboard",
)
def legal_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()
    engine = get_engine()

    with engine.begin() as conn:
        metrics = conn.execute(
            text(
                """
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM legal_cases
                        WHERE tenant_id = :tenant_id
                          AND status <> 'closed'
                    ) AS open_cases,

                    (
                        SELECT COUNT(*)
                        FROM legal_claims
                        WHERE tenant_id = :tenant_id
                          AND status NOT IN ('closed', 'rejected')
                    ) AS active_claims,

                    (
                        SELECT COALESCE(
                            SUM(estimated_exposure), 0
                        )
                        FROM legal_cases
                        WHERE tenant_id = :tenant_id
                          AND status <> 'closed'
                    ) AS legal_exposure,

                    (
                        SELECT COALESCE(
                            SUM(claimed_amount), 0
                        )
                        FROM legal_claims
                        WHERE tenant_id = :tenant_id
                          AND status NOT IN ('closed', 'rejected')
                    ) AS claims_exposure,

                    (
                        SELECT COUNT(*)
                        FROM legal_insurance_policies
                        WHERE tenant_id = :tenant_id
                          AND expiry_date < CURRENT_DATE
                    ) AS expired_policies,

                    (
                        SELECT COUNT(*)
                        FROM legal_insurance_policies
                        WHERE tenant_id = :tenant_id
                          AND expiry_date >= CURRENT_DATE
                          AND expiry_date <= CURRENT_DATE + 30
                    ) AS policies_expiring_30_days,

                    (
                        SELECT COUNT(*)
                        FROM legal_contracts
                        WHERE tenant_id = :tenant_id
                          AND expiry_date IS NOT NULL
                          AND expiry_date >= CURRENT_DATE
                          AND expiry_date <= CURRENT_DATE + 30
                    ) AS contracts_expiring_30_days
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

    risk_score = (
        int(metrics["open_cases"]) * 2
        + int(metrics["active_claims"]) * 2
        + int(metrics["expired_policies"]) * 6
        + int(metrics["policies_expiring_30_days"]) * 3
        + int(metrics["contracts_expiring_30_days"]) * 2
    )

    if risk_score >= 40:
        risk_level = "CRITICAL"
    elif risk_score >= 20:
        risk_level = "HIGH"
    elif risk_score >= 5:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_risk_score": risk_score,
        "legal_risk_level": risk_level,
        "indicators": {
            "open_cases": int(metrics["open_cases"]),
            "active_claims": int(metrics["active_claims"]),
            "legal_exposure": float(metrics["legal_exposure"]),
            "claims_exposure": float(metrics["claims_exposure"]),
            "expired_policies": int(metrics["expired_policies"]),
            "policies_expiring_30_days": int(
                metrics["policies_expiring_30_days"]
            ),
            "contracts_expiring_30_days": int(
                metrics["contracts_expiring_30_days"]
            ),
        },
        "currency_note": (
            "Exposure values remain in transaction currency "
            "until enterprise FX normalization is applied."
        ),
    }


# ============================================================
# LEGAL & INSURANCE RISK INTELLIGENCE
# ============================================================

@router.get(
    "/risk-intelligence",
    summary="Legal Claims and Insurance Risk Intelligence",
)
def legal_risk_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()
    engine = get_engine()

    with engine.begin() as conn:
        policies = conn.execute(
            text(
                """
                SELECT *
                FROM legal_insurance_policies
                WHERE tenant_id = :tenant_id
                  AND expiry_date <= CURRENT_DATE + 90
                ORDER BY expiry_date ASC
                LIMIT 100
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        claims = conn.execute(
            text(
                """
                SELECT *
                FROM legal_claims
                WHERE tenant_id = :tenant_id
                  AND status NOT IN ('closed', 'rejected')
                ORDER BY claimed_amount DESC, claim_date ASC
                LIMIT 100
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        cases = conn.execute(
            text(
                """
                SELECT *
                FROM legal_cases
                WHERE tenant_id = :tenant_id
                  AND status <> 'closed'
                ORDER BY estimated_exposure DESC,
                         opened_date ASC
                LIMIT 100
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "insurance_priority": [
            serialize_row(row) for row in policies
        ],
        "active_claim_exposure": [
            serialize_row(row) for row in claims
        ],
        "legal_case_exposure": [
            serialize_row(row) for row in cases
        ],
    }


# ============================================================
# LEGAL AUDIT TRAIL
# ============================================================

@router.get(
    "/audit-trail",
    summary="Legal Claims and Insurance Audit Trail",
)
def legal_audit_trail(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_legal_tables()
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM (
                    SELECT
                        'legal_case_created' AS event_type,
                        id AS entity_id,
                        case_number AS reference,
                        status AS description,
                        created_by,
                        created_at
                    FROM legal_cases
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'claim_registered',
                        id,
                        claim_number,
                        status,
                        created_by,
                        created_at
                    FROM legal_claims
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'insurance_policy_created',
                        id,
                        policy_number,
                        status,
                        created_by,
                        created_at
                    FROM legal_insurance_policies
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'contract_created',
                        id,
                        contract_number,
                        status,
                        created_by,
                        created_at
                    FROM legal_contracts
                    WHERE tenant_id = :tenant_id
                ) AS events
                ORDER BY created_at DESC
                LIMIT 500
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# Initialize Legal, Claims & Insurance schema.
init_legal_tables()

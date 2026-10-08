# ============================================================
# MQEIP - QUANTUM-INSPIRED OPTIMIZATION ENGINE
# BLOK 1 - CORE, DATA MODELS & POSTGRESQL FOUNDATION
# ============================================================

import json
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import text

from ..core.database import get_engine
from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/quantum",
    tags=["Quantum-Inspired Optimization Engine"],
)


# ============================================================
# CONSTANTS
# ============================================================

OPTIMIZATION_TYPES = (
    "route",
    "fuel",
    "fleet",
    "cost",
)

OPTIMIZATION_STATUSES = (
    "draft",
    "evaluated",
    "pending_approval",
    "approved",
    "rejected",
)

MAX_CANDIDATES = 100
MAX_VARIABLES = 30

UTC = timezone.utc


# ============================================================
# SHARED VALIDATION MODEL
# ============================================================

class QuantumBaseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )


# ============================================================
# OPTIMIZATION OBJECTIVE
# ============================================================

class OptimizationWeights(QuantumBaseModel):
    fuel: float = Field(default=0.35, ge=0, le=1)
    time: float = Field(default=0.25, ge=0, le=1)
    cost: float = Field(default=0.30, ge=0, le=1)
    risk: float = Field(default=0.10, ge=0, le=1)


# ============================================================
# OPTIMIZATION CANDIDATE
# ============================================================

class OptimizationCandidate(QuantumBaseModel):
    candidate_id: str = Field(
        min_length=1,
        max_length=100,
    )

    description: Optional[str] = Field(
        default=None,
        max_length=1000,
    )

    fuel_liters: float = Field(ge=0)
    voyage_hours: float = Field(ge=0)
    operating_cost: float = Field(ge=0)

    risk_score: float = Field(
        default=0,
        ge=0,
        le=100,
    )

    feasible: bool = True

    metadata: dict[str, Any] = Field(
        default_factory=dict,
    )


# ============================================================
# OPTIMIZATION REQUEST
# ============================================================

class OptimizationRequest(QuantumBaseModel):
    optimization_name: str = Field(
        min_length=3,
        max_length=200,
    )

    optimization_type: Literal[
        "route",
        "fuel",
        "fleet",
        "cost",
    ]

    department: str = Field(
        min_length=2,
        max_length=100,
    )

    vessel_id: Optional[str] = Field(
        default=None,
        max_length=150,
    )

    voyage_reference: Optional[str] = Field(
        default=None,
        max_length=150,
    )

    objective: str = Field(
        default="minimize_weighted_score",
        max_length=100,
    )

    weights: OptimizationWeights = Field(
        default_factory=OptimizationWeights,
    )

    candidates: list[OptimizationCandidate] = Field(
        min_length=1,
        max_length=MAX_CANDIDATES,
    )

    source_module: Optional[str] = Field(
        default=None,
        max_length=100,
    )

    source_record_id: Optional[str] = Field(
        default=None,
        max_length=150,
    )


# ============================================================
# HUMAN APPROVAL MODEL
# ============================================================

class OptimizationDecision(QuantumBaseModel):
    decision: Literal["approved", "rejected"]

    remarks: Optional[str] = Field(
        default=None,
        max_length=2000,
    )


# ============================================================
# SERIALIZATION
# ============================================================

def quantum_serialize_value(value):
    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {
            str(key): quantum_serialize_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            quantum_serialize_value(item)
            for item in value
        ]

    return value


def quantum_serialize_row(row):
    return {
        key: quantum_serialize_value(value)
        for key, value in dict(row).items()
    }


def quantum_json(data):
    return json.dumps(
        data,
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


# ============================================================
# OPTIMIZATION VALIDATION
# ============================================================

def validate_optimization_request(payload):
    weights = payload.weights

    total_weight = (
        weights.fuel
        + weights.time
        + weights.cost
        + weights.risk
    )

    if not math.isclose(
        total_weight,
        1.0,
        rel_tol=1e-9,
        abs_tol=1e-9,
    ):
        raise HTTPException(
            status_code=422,
            detail="Optimization weights must total 1.0.",
        )

    candidate_ids = [
        item.candidate_id
        for item in payload.candidates
    ]

    if len(candidate_ids) != len(set(candidate_ids)):
        raise HTTPException(
            status_code=422,
            detail="Duplicate candidate_id is not allowed.",
        )

    if not any(
        item.feasible
        for item in payload.candidates
    ):
        raise HTTPException(
            status_code=422,
            detail="At least one feasible candidate is required.",
        )

    return True


# ============================================================
# QUANTUM AUDIT TRAIL
# ============================================================

def record_quantum_audit(
    conn,
    identity,
    entity_type,
    entity_id,
    action,
    details=None,
):
    conn.execute(
        text("""
            INSERT INTO quantum_audit_log (
                id,
                tenant_id,
                entity_type,
                entity_id,
                action,
                details,
                actor_id,
                created_at
            )
            VALUES (
                :id,
                :tenant_id,
                :entity_type,
                :entity_id,
                :action,
                :details,
                :actor_id,
                :created_at
            )
        """),
        {
            "id": uuid4(),
            "tenant_id": str(identity.tenant_id),
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "action": action,
            "details": details,
            "actor_id": str(identity.user_id),
            "created_at": datetime.now(UTC),
        },
    )


# ============================================================
# POSTGRESQL TABLE INITIALIZATION
# ============================================================

def init_quantum_tables():
    engine = get_engine()

    statements = [

        """
        CREATE TABLE IF NOT EXISTS quantum_optimizations (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            optimization_name VARCHAR(200) NOT NULL,
            optimization_type VARCHAR(30) NOT NULL,
            department VARCHAR(100) NOT NULL,
            vessel_id TEXT,
            voyage_reference TEXT,
            objective VARCHAR(100) NOT NULL,
            weights_json TEXT NOT NULL,
            candidates_json TEXT NOT NULL,
            source_module TEXT,
            source_record_id TEXT,
            optimization_status VARCHAR(30)
                NOT NULL DEFAULT 'draft',
            best_candidate_id TEXT,
            best_score DOUBLE PRECISION,
            result_json TEXT,
            created_by TEXT NOT NULL,
            reviewed_by TEXT,
            reviewed_at TIMESTAMPTZ,
            review_remarks TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS quantum_audit_log (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            entity_type VARCHAR(100) NOT NULL,
            entity_id TEXT NOT NULL,
            action VARCHAR(100) NOT NULL,
            details TEXT,
            actor_id TEXT,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_quantum_tenant
        ON quantum_optimizations (
            tenant_id,
            optimization_type,
            optimization_status
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_quantum_created
        ON quantum_optimizations (
            tenant_id,
            created_at
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_quantum_audit
        ON quantum_audit_log (
            tenant_id,
            created_at
        )
        """,
    ]

    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


# ============================================================
# END OF QUANTUM BLOK 1
# ============================================================

# ============================================================
# MQEIP - QUANTUM-INSPIRED OPTIMIZATION ENGINE
# BLOK 2 - OPTIMIZATION ALGORITHMS & CANDIDATE EVALUATION
# ============================================================

import random


# ============================================================
# NORMALIZATION HELPERS
# ============================================================

def quantum_normalize(values):
    """
    Normalize non-negative numerical values to 0..1.
    A lower value represents a better minimization result.
    """
    if not values:
        return []

    minimum = min(values)
    maximum = max(values)

    if math.isclose(minimum, maximum):
        return [0.0 for _ in values]

    difference = maximum - minimum

    return [
        (value - minimum) / difference
        for value in values
    ]


def quantum_score_candidates(candidates, weights):
    """
    Calculate weighted scores for feasible candidates.
    Scores are relative to the candidate set.
    """
    feasible = [
        candidate
        for candidate in candidates
        if candidate.feasible
    ]

    if not feasible:
        raise HTTPException(
            status_code=422,
            detail="No feasible optimization candidates.",
        )

    fuel_scores = quantum_normalize([
        item.fuel_liters
        for item in feasible
    ])

    time_scores = quantum_normalize([
        item.voyage_hours
        for item in feasible
    ])

    cost_scores = quantum_normalize([
        item.operating_cost
        for item in feasible
    ])

    risk_scores = [
        item.risk_score / 100.0
        for item in feasible
    ]

    results = []

    for index, candidate in enumerate(feasible):
        weighted_score = (
            weights.fuel * fuel_scores[index]
            + weights.time * time_scores[index]
            + weights.cost * cost_scores[index]
            + weights.risk * risk_scores[index]
        )

        results.append({
            "candidate_id": candidate.candidate_id,
            "description": candidate.description,
            "fuel_liters": candidate.fuel_liters,
            "voyage_hours": candidate.voyage_hours,
            "operating_cost": candidate.operating_cost,
            "risk_score": candidate.risk_score,
            "weighted_score": round(
                weighted_score,
                10,
            ),
            "feasible": True,
            "metadata": candidate.metadata,
        })

    return results


# ============================================================
# PROBABILISTIC CANDIDATE SEARCH
# ============================================================

def quantum_probabilistic_search(
    scored_candidates,
    iterations=300,
    seed=42,
):
    """
    Quantum-inspired stochastic search over discrete
    candidates using probability amplitudes.

    This is a classical simulation of an amplitude-inspired
    search strategy. It does not use quantum hardware.

    Final selection is verified against all candidates,
    ensuring that the returned minimum is exact for
    the supplied finite candidate set.
    """
    if not scored_candidates:
        raise HTTPException(
            status_code=422,
            detail="Candidate scores cannot be empty.",
        )

    count = len(scored_candidates)

    if count == 1:
        return {
            "best_candidate": scored_candidates[0],
            "iterations": 0,
            "search_method": "single_candidate",
            "probabilities": [1.0],
        }

    rng = random.Random(seed)

    probabilities = [
        1.0 / count
        for _ in range(count)
    ]

    best_index = min(
        range(count),
        key=lambda index: (
            scored_candidates[index]["weighted_score"],
            scored_candidates[index]["candidate_id"],
        ),
    )

    learning_rate = 0.08

    for _ in range(iterations):
        selected_index = rng.choices(
            range(count),
            weights=probabilities,
            k=1,
        )[0]

        selected_score = scored_candidates[
            selected_index
        ]["weighted_score"]

        best_score = scored_candidates[
            best_index
        ]["weighted_score"]

        if selected_score < best_score:
            best_index = selected_index

        for index in range(count):
            probabilities[index] *= (
                1.0 - learning_rate
            )

        probabilities[best_index] += learning_rate

        total = sum(probabilities)

        probabilities = [
            value / total
            for value in probabilities
        ]

    # Exhaustive verification guarantees the minimum
    # for this explicitly enumerated candidate set.
    best_index = min(
        range(count),
        key=lambda index: (
            scored_candidates[index]["weighted_score"],
            scored_candidates[index]["candidate_id"],
        ),
    )

    return {
        "best_candidate": scored_candidates[best_index],
        "iterations": iterations,
        "search_method": (
            "quantum_inspired_probabilistic_search"
            "_with_exhaustive_verification"
        ),
        "probabilities": [
            round(value, 8)
            for value in probabilities
        ],
    }


# ============================================================
# EVALUATE OPTIMIZATION REQUEST
# ============================================================

def quantum_evaluate_request(payload):
    """
    Evaluate candidates and return a transparent,
    auditable optimization result.
    """
    validate_optimization_request(payload)

    scored_candidates = quantum_score_candidates(
        payload.candidates,
        payload.weights,
    )

    search = quantum_probabilistic_search(
        scored_candidates,
        iterations=300,
    )

    ranked_candidates = sorted(
        scored_candidates,
        key=lambda item: (
            item["weighted_score"],
            item["candidate_id"],
        ),
    )

    best = search["best_candidate"]

    baseline = next(
        (
            candidate
            for candidate in payload.candidates
            if candidate.feasible
        ),
        None,
    )

    baseline_fuel = baseline.fuel_liters
    baseline_cost = baseline.operating_cost
    baseline_time = baseline.voyage_hours

    def savings_percentage(original, optimized):
        if original <= 0:
            return None

        return round(
            ((original - optimized) / original) * 100,
            2,
        )

    return {
        "optimization_name": payload.optimization_name,
        "optimization_type": payload.optimization_type,
        "objective": payload.objective,
        "best_candidate_id": best["candidate_id"],
        "best_score": best["weighted_score"],
        "best_candidate": best,
        "ranked_candidates": ranked_candidates,
        "total_candidates": len(payload.candidates),
        "feasible_candidates": len(scored_candidates),
        "baseline_candidate_id": baseline.candidate_id,
        "estimated_savings": {
            "fuel_percent": savings_percentage(
                baseline_fuel,
                best["fuel_liters"],
            ),
            "time_percent": savings_percentage(
                baseline_time,
                best["voyage_hours"],
            ),
            "cost_percent": savings_percentage(
                baseline_cost,
                best["operating_cost"],
            ),
        },
        "algorithm": {
            "name": search["search_method"],
            "iterations": search["iterations"],
            "hardware": "classical",
            "exact_for_supplied_candidates": True,
            "weights": payload.weights.model_dump(),
        },
        "requires_human_approval": True,
        "operational_action_executed": False,
        "evaluated_at": datetime.now(
            UTC
        ).isoformat(),
    }


# ============================================================
# END OF QUANTUM BLOK 2
# ============================================================

# ============================================================
# MQEIP - QUANTUM-INSPIRED OPTIMIZATION ENGINE
# BLOK 3 - OPTIMIZATION API & POSTGRESQL PERSISTENCE
# ============================================================


# ============================================================
# CREATE OPTIMIZATION REQUEST
# ============================================================

@router.post(
    "/optimizations",
    status_code=status.HTTP_201_CREATED,
    summary="Create Quantum-Inspired Optimization",
)
def create_quantum_optimization(
    payload: OptimizationRequest,
    identity: TokenIdentity = Depends(verify_access_token),
):
    validate_optimization_request(payload)

    if payload.objective != "minimize_weighted_score":
        raise HTTPException(
            status_code=422,
            detail=(
                "Only minimize_weighted_score "
                "is currently supported."
            ),
        )

    optimization_id = uuid4()
    now = datetime.now(UTC)
    tenant_id = str(identity.tenant_id)

    with get_engine().begin() as conn:
        conn.execute(
            text("""
                INSERT INTO quantum_optimizations (
                    id,
                    tenant_id,
                    optimization_name,
                    optimization_type,
                    department,
                    vessel_id,
                    voyage_reference,
                    objective,
                    weights_json,
                    candidates_json,
                    source_module,
                    source_record_id,
                    optimization_status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :optimization_name,
                    :optimization_type,
                    :department,
                    :vessel_id,
                    :voyage_reference,
                    :objective,
                    :weights_json,
                    :candidates_json,
                    :source_module,
                    :source_record_id,
                    'draft',
                    :created_by,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "id": optimization_id,
                "tenant_id": tenant_id,
                "optimization_name": payload.optimization_name,
                "optimization_type": payload.optimization_type,
                "department": payload.department,
                "vessel_id": payload.vessel_id,
                "voyage_reference": payload.voyage_reference,
                "objective": payload.objective,
                "weights_json": quantum_json(
                    payload.weights.model_dump()
                ),
                "candidates_json": quantum_json([
                    candidate.model_dump()
                    for candidate in payload.candidates
                ]),
                "source_module": payload.source_module,
                "source_record_id": payload.source_record_id,
                "created_by": str(identity.user_id),
                "created_at": now,
                "updated_at": now,
            },
        )

        record_quantum_audit(
            conn,
            identity,
            "optimization",
            optimization_id,
            "created",
            quantum_json({
                "optimization_type": payload.optimization_type,
                "candidate_count": len(payload.candidates),
            }),
        )

    return {
        "id": str(optimization_id),
        "optimization_name": payload.optimization_name,
        "optimization_type": payload.optimization_type,
        "optimization_status": "draft",
        "candidate_count": len(payload.candidates),
        "created_at": now.isoformat(),
    }


# ============================================================
# LIST OPTIMIZATIONS
# ============================================================

@router.get(
    "/optimizations",
    summary="List Quantum-Inspired Optimizations",
)
def list_quantum_optimizations(
    optimization_type: Optional[str] = None,
    optimization_status: Optional[str] = None,
    vessel_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    id,
                    tenant_id,
                    optimization_name,
                    optimization_type,
                    department,
                    vessel_id,
                    voyage_reference,
                    objective,
                    optimization_status,
                    best_candidate_id,
                    best_score,
                    created_by,
                    reviewed_by,
                    reviewed_at,
                    created_at,
                    updated_at
                FROM quantum_optimizations
                WHERE tenant_id = :tenant_id
                  AND (
                      :optimization_type IS NULL
                      OR optimization_type = :optimization_type
                  )
                  AND (
                      :optimization_status IS NULL
                      OR optimization_status = :optimization_status
                  )
                  AND (
                      :vessel_id IS NULL
                      OR vessel_id = :vessel_id
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
            """),
            {
                "tenant_id": str(identity.tenant_id),
                "optimization_type": optimization_type,
                "optimization_status": optimization_status,
                "vessel_id": vessel_id,
                "limit": limit,
            },
        ).mappings().all()

    return {
        "module": "Quantum-Inspired Optimization",
        "count": len(rows),
        "optimizations": [
            quantum_serialize_row(row)
            for row in rows
        ],
    }


# ============================================================
# GET OPTIMIZATION DETAILS
# ============================================================

@router.get(
    "/optimizations/{optimization_id}",
    summary="Get Quantum Optimization Details",
)
def get_quantum_optimization(
    optimization_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        row = conn.execute(
            text("""
                SELECT *
                FROM quantum_optimizations
                WHERE id = :optimization_id
                  AND tenant_id = :tenant_id
            """),
            {
                "optimization_id": optimization_id,
                "tenant_id": str(identity.tenant_id),
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Optimization not found.",
        )

    item = quantum_serialize_row(row)

    for source_key, target_key in (
        ("weights_json", "weights"),
        ("candidates_json", "candidates"),
        ("result_json", "result"),
    ):
        raw = item.pop(source_key, None)

        try:
            item[target_key] = (
                json.loads(raw)
                if raw is not None
                else None
            )
        except (TypeError, ValueError):
            item[target_key] = None

    return item


# ============================================================
# EVALUATE OPTIMIZATION
# ============================================================

@router.post(
    "/optimizations/{optimization_id}/evaluate",
    summary="Evaluate Quantum-Inspired Optimization",
)
def evaluate_quantum_optimization(
    optimization_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)
    now = datetime.now(UTC)

    with get_engine().begin() as conn:
        existing = conn.execute(
            text("""
                SELECT *
                FROM quantum_optimizations
                WHERE id = :optimization_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "optimization_id": optimization_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="Optimization not found.",
            )

        if existing["optimization_status"] != "draft":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Only draft optimizations "
                    "can be evaluated."
                ),
            )

        try:
            payload = OptimizationRequest(
                optimization_name=existing[
                    "optimization_name"
                ],
                optimization_type=existing[
                    "optimization_type"
                ],
                department=existing["department"],
                vessel_id=existing["vessel_id"],
                voyage_reference=existing[
                    "voyage_reference"
                ],
                objective=existing["objective"],
                weights=json.loads(
                    existing["weights_json"]
                ),
                candidates=json.loads(
                    existing["candidates_json"]
                ),
                source_module=existing["source_module"],
                source_record_id=existing[
                    "source_record_id"
                ],
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(
                status_code=422,
                detail="Stored optimization data is invalid.",
            ) from exc

        if payload.objective != "minimize_weighted_score":
            raise HTTPException(
                status_code=422,
                detail="Unsupported optimization objective.",
            )

        result = quantum_evaluate_request(payload)

        conn.execute(
            text("""
                UPDATE quantum_optimizations
                SET optimization_status = 'evaluated',
                    best_candidate_id = :best_candidate_id,
                    best_score = :best_score,
                    result_json = :result_json,
                    updated_at = :updated_at
                WHERE id = :optimization_id
                  AND tenant_id = :tenant_id
            """),
            {
                "best_candidate_id": result[
                    "best_candidate_id"
                ],
                "best_score": result["best_score"],
                "result_json": quantum_json(result),
                "updated_at": now,
                "optimization_id": optimization_id,
                "tenant_id": tenant_id,
            },
        )

        record_quantum_audit(
            conn,
            identity,
            "optimization",
            optimization_id,
            "evaluated",
            quantum_json({
                "best_candidate_id": result[
                    "best_candidate_id"
                ],
                "best_score": result["best_score"],
                "algorithm": result["algorithm"]["name"],
            }),
        )

    return {
        "optimization_id": str(optimization_id),
        "optimization_status": "evaluated",
        "result": result,
        "requires_human_approval": True,
        "operational_action_executed": False,
    }


# ============================================================
# SUBMIT OPTIMIZATION FOR HUMAN APPROVAL
# ============================================================

@router.post(
    "/optimizations/{optimization_id}/submit",
    summary="Submit Optimization for Human Approval",
)
def submit_quantum_optimization(
    optimization_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)
    now = datetime.now(UTC)

    with get_engine().begin() as conn:
        existing = conn.execute(
            text("""
                SELECT
                    id,
                    optimization_status,
                    best_candidate_id
                FROM quantum_optimizations
                WHERE id = :optimization_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "optimization_id": optimization_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="Optimization not found.",
            )

        if existing["optimization_status"] != "evaluated":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Only evaluated optimizations "
                    "can be submitted for approval."
                ),
            )

        conn.execute(
            text("""
                UPDATE quantum_optimizations
                SET optimization_status = 'pending_approval',
                    updated_at = :updated_at
                WHERE id = :optimization_id
                  AND tenant_id = :tenant_id
            """),
            {
                "updated_at": now,
                "optimization_id": optimization_id,
                "tenant_id": tenant_id,
            },
        )

        record_quantum_audit(
            conn,
            identity,
            "optimization",
            optimization_id,
            "submitted_for_approval",
            quantum_json({
                "best_candidate_id": existing[
                    "best_candidate_id"
                ],
            }),
        )

    return {
        "optimization_id": str(optimization_id),
        "optimization_status": "pending_approval",
        "best_candidate_id": existing[
            "best_candidate_id"
        ],
        "operational_action_executed": False,
        "note": (
            "Optimization submitted for human review. "
            "No vessel or ERP operation was changed."
        ),
    }


# ============================================================
# END OF QUANTUM BLOK 3
# ============================================================

# ============================================================
# MQEIP - QUANTUM-INSPIRED OPTIMIZATION ENGINE
# BLOK 4 - HUMAN APPROVAL & OPTIMIZATION INTELLIGENCE
# ============================================================


# ============================================================
# HUMAN APPROVAL - AUTHORIZATION HELPER
# ============================================================

def quantum_require_reviewer(identity):
    """
    Temporary role gate for human approval.
    Central IAM/RBAC integration is required before
    production deployment.
    """
    allowed_roles = {
        "admin",
        "super_admin",
        "tenant_admin",
        "manager",
        "dpa",
    }

    role = str(
        getattr(identity, "role", "")
    ).strip().lower()

    if role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail=(
                "Your role is not authorized "
                "to approve optimizations."
            ),
        )


# ============================================================
# APPROVE OR REJECT OPTIMIZATION
# ============================================================

@router.post(
    "/optimizations/{optimization_id}/decision",
    summary="Human Approval of Quantum Optimization",
)
def review_quantum_optimization(
    optimization_id: UUID,
    payload: OptimizationDecision,
    identity: TokenIdentity = Depends(verify_access_token),
):
    quantum_require_reviewer(identity)

    tenant_id = str(identity.tenant_id)
    reviewer_id = str(identity.user_id)
    now = datetime.now(UTC)

    with get_engine().begin() as conn:
        existing = conn.execute(
            text("""
                SELECT
                    id,
                    optimization_name,
                    optimization_type,
                    optimization_status,
                    best_candidate_id,
                    best_score,
                    created_by
                FROM quantum_optimizations
                WHERE id = :optimization_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "optimization_id": optimization_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="Optimization not found.",
            )

        if existing["optimization_status"] != "pending_approval":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Only optimizations awaiting approval "
                    "can be reviewed."
                ),
            )

        if str(existing["created_by"]) == reviewer_id:
            raise HTTPException(
                status_code=403,
                detail=(
                    "The optimization creator cannot "
                    "approve or reject their own request."
                ),
            )

        if (
            payload.decision == "approved"
            and existing["best_candidate_id"] is None
        ):
            raise HTTPException(
                status_code=409,
                detail="No evaluated candidate is available.",
            )

        conn.execute(
            text("""
                UPDATE quantum_optimizations
                SET optimization_status = :decision,
                    reviewed_by = :reviewed_by,
                    reviewed_at = :reviewed_at,
                    review_remarks = :review_remarks,
                    updated_at = :updated_at
                WHERE id = :optimization_id
                  AND tenant_id = :tenant_id
            """),
            {
                "decision": payload.decision,
                "reviewed_by": reviewer_id,
                "reviewed_at": now,
                "review_remarks": payload.remarks,
                "updated_at": now,
                "optimization_id": optimization_id,
                "tenant_id": tenant_id,
            },
        )

        record_quantum_audit(
            conn,
            identity,
            "optimization",
            optimization_id,
            payload.decision,
            quantum_json({
                "best_candidate_id": existing[
                    "best_candidate_id"
                ],
                "best_score": existing["best_score"],
                "remarks": payload.remarks,
            }),
        )

    return {
        "optimization_id": str(optimization_id),
        "optimization_name": existing[
            "optimization_name"
        ],
        "optimization_status": payload.decision,
        "best_candidate_id": existing[
            "best_candidate_id"
        ],
        "reviewed_by": reviewer_id,
        "reviewed_at": now.isoformat(),
        "operational_action_executed": False,
        "note": (
            "Human decision recorded. Approval does not "
            "automatically change vessel operations, "
            "routes, machinery settings or ERP records."
        ),
    }


# ============================================================
# OPTIMIZATION INTELLIGENCE DASHBOARD
# ============================================================

@router.get(
    "/intelligence/dashboard",
    summary="Quantum Optimization Intelligence Dashboard",
)
def quantum_intelligence_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:
        overview = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_optimizations,
                    COUNT(*) FILTER (
                        WHERE optimization_status = 'draft'
                    ) AS draft_count,
                    COUNT(*) FILTER (
                        WHERE optimization_status = 'evaluated'
                    ) AS evaluated_count,
                    COUNT(*) FILTER (
                        WHERE optimization_status = 'pending_approval'
                    ) AS pending_approval_count,
                    COUNT(*) FILTER (
                        WHERE optimization_status = 'approved'
                    ) AS approved_count,
                    COUNT(*) FILTER (
                        WHERE optimization_status = 'rejected'
                    ) AS rejected_count
                FROM quantum_optimizations
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": tenant_id},
        ).mappings().one()

        by_type = conn.execute(
            text("""
                SELECT
                    optimization_type,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (
                        WHERE optimization_status = 'approved'
                    ) AS approved,
                    COUNT(*) FILTER (
                        WHERE optimization_status = 'pending_approval'
                    ) AS pending
                FROM quantum_optimizations
                WHERE tenant_id = :tenant_id
                GROUP BY optimization_type
                ORDER BY optimization_type
            """),
            {"tenant_id": tenant_id},
        ).mappings().all()

        by_department = conn.execute(
            text("""
                SELECT
                    department,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (
                        WHERE optimization_status = 'approved'
                    ) AS approved
                FROM quantum_optimizations
                WHERE tenant_id = :tenant_id
                GROUP BY department
                ORDER BY department
            """),
            {"tenant_id": tenant_id},
        ).mappings().all()

    return {
        "platform": "MQEIP",
        "module": "Quantum Optimization Intelligence",
        "overview": quantum_serialize_row(overview),
        "optimization_types": [
            quantum_serialize_row(row)
            for row in by_type
        ],
        "departments": [
            quantum_serialize_row(row)
            for row in by_department
        ],
        "generated_at": datetime.now(UTC).isoformat(),
    }


# ============================================================
# APPROVAL QUEUE
# ============================================================

@router.get(
    "/intelligence/approval-queue",
    summary="Quantum Optimization Approval Queue",
)
def quantum_approval_queue(
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    quantum_require_reviewer(identity)

    with get_engine().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    id,
                    optimization_name,
                    optimization_type,
                    department,
                    vessel_id,
                    voyage_reference,
                    best_candidate_id,
                    best_score,
                    created_by,
                    created_at,
                    updated_at
                FROM quantum_optimizations
                WHERE tenant_id = :tenant_id
                  AND optimization_status = 'pending_approval'
                ORDER BY created_at ASC, id ASC
                LIMIT :limit
            """),
            {
                "tenant_id": str(identity.tenant_id),
                "limit": limit,
            },
        ).mappings().all()

    return {
        "module": "Quantum Optimization Approval Queue",
        "count": len(rows),
        "pending_optimizations": [
            quantum_serialize_row(row)
            for row in rows
        ],
    }


# ============================================================
# OPTIMIZATION RESULTS BY TYPE
# ============================================================

@router.get(
    "/intelligence/results",
    summary="Quantum Optimization Results Intelligence",
)
def quantum_results_intelligence(
    optimization_type: Optional[
        Literal["route", "fuel", "fleet", "cost"]
    ] = None,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    id,
                    optimization_name,
                    optimization_type,
                    department,
                    vessel_id,
                    voyage_reference,
                    optimization_status,
                    best_candidate_id,
                    best_score,
                    result_json,
                    created_at
                FROM quantum_optimizations
                WHERE tenant_id = :tenant_id
                  AND result_json IS NOT NULL
                  AND (
                      :optimization_type IS NULL
                      OR optimization_type = :optimization_type
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
            """),
            {
                "tenant_id": str(identity.tenant_id),
                "optimization_type": optimization_type,
                "limit": limit,
            },
        ).mappings().all()

    results = []

    for row in rows:
        item = quantum_serialize_row(row)
        raw = item.pop("result_json", None)

        try:
            item["result"] = (
                json.loads(raw)
                if raw is not None
                else None
            )
        except (TypeError, ValueError):
            item["result"] = None

        results.append(item)

    return {
        "module": "Quantum Optimization Results",
        "count": len(results),
        "optimizations": results,
        "note": (
            "Estimated savings are relative to "
            "the supplied baseline candidate, "
            "not verified real-world savings."
        ),
    }


# ============================================================
# END OF QUANTUM BLOK 4
# ============================================================

# ============================================================
# MQEIP - QUANTUM-INSPIRED OPTIMIZATION ENGINE
# BLOK 5 - DATA QUALITY, AUDIT & SYSTEM INITIALIZATION
# ============================================================


# ============================================================
# QUANTUM DATA QUALITY INTELLIGENCE
# ============================================================

@router.get(
    "/intelligence/data-quality",
    summary="Quantum Optimization Data Quality",
)
def quantum_data_quality(
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:

        quality = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_optimizations,

                    COUNT(*) FILTER (
                        WHERE result_json IS NULL
                          AND optimization_status != 'draft'
                    ) AS missing_evaluation_results,

                    COUNT(*) FILTER (
                        WHERE best_candidate_id IS NULL
                          AND optimization_status IN (
                              'evaluated',
                              'pending_approval',
                              'approved'
                          )
                    ) AS missing_best_candidates,

                    COUNT(*) FILTER (
                        WHERE optimization_status = 'approved'
                          AND reviewed_by IS NULL
                    ) AS approvals_without_reviewer,

                    COUNT(*) FILTER (
                        WHERE optimization_status = 'approved'
                          AND reviewed_at IS NULL
                    ) AS approvals_without_timestamp,

                    COUNT(*) FILTER (
                        WHERE vessel_id IS NULL
                    ) AS records_without_vessel,

                    COUNT(*) FILTER (
                        WHERE source_module IS NULL
                    ) AS records_without_source_module

                FROM quantum_optimizations
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": tenant_id},
        ).mappings().one()

    return {
        "platform": "MQEIP",
        "module": "Quantum Optimization Data Quality",
        "metrics": quantum_serialize_row(quality),
        "generated_at": datetime.now(UTC).isoformat(),
        "note": (
            "Missing vessel or source references may "
            "be valid for enterprise-wide optimizations. "
            "These metrics are indicators, not proof "
            "of incorrect operational data."
        ),
    }


# ============================================================
# QUANTUM AUDIT TRAIL
# ============================================================

@router.get(
    "/audit-trail",
    summary="Quantum Optimization Audit Trail",
)
def get_quantum_audit_trail(
    entity_type: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=1000),
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:

        rows = conn.execute(
            text("""
                SELECT
                    id,
                    tenant_id,
                    entity_type,
                    entity_id,
                    action,
                    details,
                    actor_id,
                    created_at
                FROM quantum_audit_log
                WHERE tenant_id = :tenant_id
                  AND (
                      :entity_type IS NULL
                      OR entity_type = :entity_type
                  )
                  AND (
                      :action IS NULL
                      OR action = :action
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
            """),
            {
                "tenant_id": str(identity.tenant_id),
                "entity_type": entity_type,
                "action": action,
                "limit": limit,
            },
        ).mappings().all()

    return {
        "module": "Quantum Optimization Audit Trail",
        "count": len(rows),
        "records": [
            quantum_serialize_row(row)
            for row in rows
        ],
    }


# ============================================================
# QUANTUM SYSTEM STATUS
# ============================================================

@router.get(
    "/system/status",
    summary="Quantum Optimization System Status",
)
def quantum_system_status(
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))

    return {
        "platform": "MQEIP",
        "module": "Quantum-Inspired Optimization Engine",
        "database": "connected",
        "authentication": "required",
        "tenant_id": str(identity.tenant_id),
        "optimization_types": list(OPTIMIZATION_TYPES),
        "algorithm": (
            "Quantum-inspired probabilistic search "
            "with exhaustive candidate verification"
        ),
        "computing_hardware": "classical",
        "quantum_hardware_required": False,
        "human_approval": "required",
        "automatic_operational_execution": False,
        "system_status": "available",
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ============================================================
# QUANTUM MODULE INFORMATION
# ============================================================

@router.get(
    "/system/capabilities",
    summary="Quantum Optimization Capabilities",
)
def quantum_system_capabilities(
    identity: TokenIdentity = Depends(verify_access_token),
):
    return {
        "platform": "MQEIP",
        "module": "Quantum-Inspired Optimization Engine",
        "supported_optimization_types": [
            {
                "type": "route",
                "description": (
                    "Compare voyage route candidates "
                    "using fuel, time, cost and risk."
                ),
            },
            {
                "type": "fuel",
                "description": (
                    "Compare fuel consumption "
                    "and operational alternatives."
                ),
            },
            {
                "type": "fleet",
                "description": (
                    "Compare vessel allocation "
                    "candidates using supplied metrics."
                ),
            },
            {
                "type": "cost",
                "description": (
                    "Compare operational cost "
                    "and efficiency alternatives."
                ),
            },
        ],
        "optimization_method": (
            "Weighted multi-criteria candidate scoring"
        ),
        "search_method": (
            "Classical probabilistic search "
            "with exhaustive verification"
        ),
        "maximum_candidates": MAX_CANDIDATES,
        "human_approval_required": True,
        "automatic_execution": False,
        "limitations": [
            "Only supplied candidates are compared.",
            "Navigation safety is not independently verified.",
            "Weather and sea-state data are not yet integrated.",
            "Results depend on input data quality.",
            "Actual fuel savings require operational validation.",
        ],
    }


# ============================================================
# QUANTUM DATABASE INITIALIZATION
# ============================================================

init_quantum_tables()


# ============================================================
# END OF QUANTUM BLOK 5
# END OF QUANTUM-INSPIRED OPTIMIZATION MODULE
# ============================================================

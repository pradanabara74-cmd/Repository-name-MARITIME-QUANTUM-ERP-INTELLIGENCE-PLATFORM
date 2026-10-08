# ============================================================
# MQEIP - AI INTELLIGENCE CENTER
# BLOK 1 - CORE, MODELS & DATABASE
# ============================================================

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..core.database import get_engine
from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/ai",
    tags=["AI Intelligence Center & Autonomous AI Agents"],
)


# ============================================================
# AI AGENT MODELS
# ============================================================

class AIAgentCreate(BaseModel):
    agent_name: str = Field(min_length=3, max_length=150)
    department: str = Field(min_length=2, max_length=100)
    description: Optional[str] = None
    capabilities: list[str] = Field(default_factory=list)
    model_provider: str = Field(default="not_configured")
    model_name: str = Field(default="not_configured")
    requires_approval: bool = True


class AIAgentStatusUpdate(BaseModel):
    is_active: bool


# ============================================================
# AI TASK MODELS
# ============================================================

class AITaskCreate(BaseModel):
    agent_id: UUID
    task_title: str = Field(min_length=3, max_length=200)
    task_type: str = Field(min_length=2, max_length=100)
    department: str = Field(min_length=2, max_length=100)
    vessel_id: Optional[str] = None
    source_module: Optional[str] = None
    source_record_id: Optional[str] = None
    input_data: dict[str, Any] = Field(default_factory=dict)
    priority: str = Field(default="medium")


class AITaskResultSubmit(BaseModel):
    result_data: dict[str, Any] = Field(default_factory=dict)
    confidence_score: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
    )
    explanation: Optional[str] = None


# ============================================================
# AI RECOMMENDATION MODELS
# ============================================================

class AIRecommendationCreate(BaseModel):
    task_id: UUID
    recommendation_title: str = Field(
        min_length=3,
        max_length=200,
    )
    recommendation_text: str = Field(min_length=5)
    risk_level: str = Field(default="medium")
    proposed_action: Optional[str] = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class AIApprovalDecision(BaseModel):
    decision: str
    remarks: Optional[str] = None


# ============================================================
# SERIALIZATION HELPERS
# ============================================================

def serialize_ai_value(value):
    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {
            key: serialize_ai_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            serialize_ai_value(item)
            for item in value
        ]

    return value


def serialize_ai_row(row):
    return {
        key: serialize_ai_value(value)
        for key, value in dict(row).items()
    }


def ai_json(data):
    return json.dumps(
        data,
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


# ============================================================
# AI AUDIT HELPER
# ============================================================

def record_ai_audit(
    conn,
    identity,
    entity_type,
    entity_id,
    action,
    details=None,
):
    conn.execute(
        text("""
            INSERT INTO ai_audit_log (
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
            "tenant_id": identity.tenant_id,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "action": action,
            "details": details,
            "actor_id": str(identity.user_id),
            "created_at": datetime.now(timezone.utc),
        },
    )


# ============================================================
# INITIALIZE AI DATABASE TABLES
# ============================================================

def init_ai_tables():
    engine = get_engine()

    statements = [

        """
        CREATE TABLE IF NOT EXISTS ai_agents (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            agent_name VARCHAR(150) NOT NULL,
            department VARCHAR(100) NOT NULL,
            description TEXT,
            capabilities_json TEXT NOT NULL DEFAULT '[]',
            model_provider VARCHAR(100) NOT NULL,
            model_name VARCHAR(150) NOT NULL,
            requires_approval BOOLEAN NOT NULL DEFAULT TRUE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS ai_tasks (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            agent_id UUID NOT NULL,
            task_title VARCHAR(200) NOT NULL,
            task_type VARCHAR(100) NOT NULL,
            department VARCHAR(100) NOT NULL,
            vessel_id TEXT,
            source_module TEXT,
            source_record_id TEXT,
            input_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT,
            confidence_score DOUBLE PRECISION,
            explanation TEXT,
            priority VARCHAR(30) NOT NULL DEFAULT 'medium',
            task_status VARCHAR(30) NOT NULL DEFAULT 'pending',
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS ai_recommendations (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            task_id UUID NOT NULL,
            recommendation_title VARCHAR(200) NOT NULL,
            recommendation_text TEXT NOT NULL,
            risk_level VARCHAR(30) NOT NULL,
            proposed_action TEXT,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            approval_status VARCHAR(30)
                NOT NULL DEFAULT 'pending',
            reviewed_by TEXT,
            reviewed_at TIMESTAMPTZ,
            review_remarks TEXT,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS ai_audit_log (
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
        CREATE INDEX IF NOT EXISTS idx_ai_agents_tenant
        ON ai_agents (tenant_id, department)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_ai_tasks_tenant
        ON ai_tasks (tenant_id, task_status, created_at)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_ai_recommendations_tenant
        ON ai_recommendations (tenant_id, approval_status)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_ai_audit_tenant
        ON ai_audit_log (tenant_id, created_at)
        """,
    ]

    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


# ============================================================
# END OF AI BLOK 1
# ============================================================

# ============================================================
# AI BLOK 2 - AGENT REGISTRY & LIFECYCLE MANAGEMENT
# ============================================================


# ============================================================
# REGISTER AI AGENT
# ============================================================

@router.post(
    "/agents",
    status_code=status.HTTP_201_CREATED,
    summary="Register Enterprise AI Agent",
)
def create_ai_agent(
    payload: AIAgentCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    agent_id = uuid4()
    now = datetime.now(timezone.utc)

    with get_engine().begin() as conn:
        conn.execute(
            text("""
                INSERT INTO ai_agents (
                    id, tenant_id, agent_name, department,
                    description, capabilities_json,
                    model_provider, model_name,
                    requires_approval, is_active,
                    created_by, created_at, updated_at
                )
                VALUES (
                    :id, :tenant_id, :agent_name, :department,
                    :description, :capabilities_json,
                    :model_provider, :model_name,
                    :requires_approval, TRUE,
                    :created_by, :created_at, :updated_at
                )
            """),
            {
                "id": agent_id,
                "tenant_id": str(identity.tenant_id),
                "agent_name": payload.agent_name,
                "department": payload.department,
                "description": payload.description,
                "capabilities_json": ai_json(
                    payload.capabilities
                ),
                "model_provider": payload.model_provider,
                "model_name": payload.model_name,
                "requires_approval": payload.requires_approval,
                "created_by": str(identity.user_id),
                "created_at": now,
                "updated_at": now,
            },
        )

        record_ai_audit(
            conn,
            identity,
            "agent",
            agent_id,
            "created",
            f"Registered AI agent: {payload.agent_name}",
        )

    return {
        "id": str(agent_id),
        "agent_name": payload.agent_name,
        "department": payload.department,
        "is_active": True,
        "status": "registered",
        "note": (
            "Agent registered. Model execution is not "
            "automatically enabled."
        ),
    }


# ============================================================
# LIST REGISTERED AI AGENTS
# ============================================================

@router.get(
    "/agents",
    summary="List Enterprise AI Agents",
)
def list_ai_agents(
    department: Optional[str] = None,
    active_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM ai_agents
                WHERE tenant_id = :tenant_id
                  AND (
                      :department IS NULL
                      OR department = :department
                  )
                  AND (
                      :active_only = FALSE
                      OR is_active = TRUE
                  )
                ORDER BY department, agent_name, created_at
                LIMIT :limit
            """),
            {
                "tenant_id": str(identity.tenant_id),
                "department": department,
                "active_only": active_only,
                "limit": limit,
            },
        ).mappings().all()

    agents = []

    for row in rows:
        agent = serialize_ai_row(row)

        try:
            agent["capabilities"] = json.loads(
                agent.pop("capabilities_json")
            )
        except (TypeError, ValueError):
            agent.pop("capabilities_json", None)
            agent["capabilities"] = []

        agents.append(agent)

    return {
        "module": "Enterprise AI Agent Registry",
        "count": len(agents),
        "agents": agents,
    }


# ============================================================
# AI AGENT DETAILS
# ============================================================

@router.get(
    "/agents/{agent_id}",
    summary="Get AI Agent Details",
)
def get_ai_agent(
    agent_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        row = conn.execute(
            text("""
                SELECT *
                FROM ai_agents
                WHERE id = :agent_id
                  AND tenant_id = :tenant_id
            """),
            {
                "agent_id": agent_id,
                "tenant_id": str(identity.tenant_id),
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="AI agent not found.",
        )

    agent = serialize_ai_row(row)

    try:
        agent["capabilities"] = json.loads(
            agent.pop("capabilities_json")
        )
    except (TypeError, ValueError):
        agent.pop("capabilities_json", None)
        agent["capabilities"] = []

    return agent


# ============================================================
# ACTIVATE OR DEACTIVATE AI AGENT
# ============================================================

@router.patch(
    "/agents/{agent_id}/status",
    summary="Activate or Deactivate AI Agent",
)
def update_ai_agent_status(
    agent_id: UUID,
    payload: AIAgentStatusUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    now = datetime.now(timezone.utc)

    with get_engine().begin() as conn:
        existing = conn.execute(
            text("""
                SELECT id, agent_name, is_active
                FROM ai_agents
                WHERE id = :agent_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "agent_id": agent_id,
                "tenant_id": str(identity.tenant_id),
            },
        ).mappings().first()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="AI agent not found.",
            )

        conn.execute(
            text("""
                UPDATE ai_agents
                SET is_active = :is_active,
                    updated_at = :updated_at
                WHERE id = :agent_id
                  AND tenant_id = :tenant_id
            """),
            {
                "is_active": payload.is_active,
                "updated_at": now,
                "agent_id": agent_id,
                "tenant_id": str(identity.tenant_id),
            },
        )

        record_ai_audit(
            conn,
            identity,
            "agent",
            agent_id,
            (
                "activated"
                if payload.is_active
                else "deactivated"
            ),
            ai_json({
                "previous_status": existing["is_active"],
                "new_status": payload.is_active,
            }),
        )

    return {
        "id": str(agent_id),
        "agent_name": existing["agent_name"],
        "is_active": payload.is_active,
        "status": "updated",
        "updated_at": now.isoformat(),
    }


# ============================================================
# AI AGENT REGISTRY INTELLIGENCE
# ============================================================

@router.get(
    "/intelligence/agents",
    summary="Enterprise AI Agent Registry Intelligence",
)
def ai_agent_registry_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    department,
                    COUNT(*) AS total_agents,
                    COUNT(*) FILTER (
                        WHERE is_active = TRUE
                    ) AS active_agents,
                    COUNT(*) FILTER (
                        WHERE requires_approval = TRUE
                    ) AS approval_required_agents,
                    COUNT(*) FILTER (
                        WHERE model_provider = 'not_configured'
                           OR model_name = 'not_configured'
                    ) AS unconfigured_models
                FROM ai_agents
                WHERE tenant_id = :tenant_id
                GROUP BY department
                ORDER BY department
            """),
            {
                "tenant_id": str(identity.tenant_id),
            },
        ).mappings().all()

    departments = [
        serialize_ai_row(row)
        for row in rows
    ]

    return {
        "module": "AI Agent Registry Intelligence",
        "department_count": len(departments),
        "total_agents": sum(
            item["total_agents"]
            for item in departments
        ),
        "active_agents": sum(
            item["active_agents"]
            for item in departments
        ),
        "departments": departments,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "note": (
            "Active status means the agent is enabled "
            "in the registry, not that an AI model "
            "is running autonomously."
        ),
    }


# ============================================================
# END OF AI BLOK 2
# ============================================================

# ============================================================
# AI BLOK 3 - TASK ORCHESTRATION & EXECUTION RECORDS
# ============================================================


# ============================================================
# CREATE AI TASK
# ============================================================

@router.post(
    "/tasks",
    status_code=status.HTTP_201_CREATED,
    summary="Create Enterprise AI Task",
)
def create_ai_task(
    payload: AITaskCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    if payload.priority not in {
        "low", "medium", "high", "critical"
    }:
        raise HTTPException(
            status_code=422,
            detail="Invalid task priority.",
        )

    task_id = uuid4()
    now = datetime.now(timezone.utc)
    tenant_id = str(identity.tenant_id)

    with get_engine().begin() as conn:
        agent = conn.execute(
            text("""
                SELECT id, department, is_active
                FROM ai_agents
                WHERE id = :agent_id
                  AND tenant_id = :tenant_id
            """),
            {
                "agent_id": payload.agent_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if agent is None:
            raise HTTPException(
                status_code=404,
                detail="AI agent not found.",
            )

        if not agent["is_active"]:
            raise HTTPException(
                status_code=409,
                detail="AI agent is inactive.",
            )

        if agent["department"] != payload.department:
            raise HTTPException(
                status_code=422,
                detail="Agent department does not match task.",
            )

        conn.execute(
            text("""
                INSERT INTO ai_tasks (
                    id, tenant_id, agent_id,
                    task_title, task_type, department,
                    vessel_id, source_module,
                    source_record_id, input_json,
                    priority, task_status,
                    created_by, created_at, updated_at
                )
                VALUES (
                    :id, :tenant_id, :agent_id,
                    :task_title, :task_type, :department,
                    :vessel_id, :source_module,
                    :source_record_id, :input_json,
                    :priority, 'pending',
                    :created_by, :created_at, :updated_at
                )
            """),
            {
                "id": task_id,
                "tenant_id": tenant_id,
                "agent_id": payload.agent_id,
                "task_title": payload.task_title,
                "task_type": payload.task_type,
                "department": payload.department,
                "vessel_id": payload.vessel_id,
                "source_module": payload.source_module,
                "source_record_id": payload.source_record_id,
                "input_json": ai_json(payload.input_data),
                "priority": payload.priority,
                "created_by": str(identity.user_id),
                "created_at": now,
                "updated_at": now,
            },
        )

        record_ai_audit(
            conn,
            identity,
            "task",
            task_id,
            "created",
            f"AI task created: {payload.task_title}",
        )

    return {
        "id": str(task_id),
        "agent_id": str(payload.agent_id),
        "task_title": payload.task_title,
        "task_status": "pending",
        "priority": payload.priority,
        "created_at": now.isoformat(),
        "note": (
            "Task registered. AI model execution "
            "has not been started."
        ),
    }


# ============================================================
# LIST AI TASKS
# ============================================================

@router.get(
    "/tasks",
    summary="List Enterprise AI Tasks",
)
def list_ai_tasks(
    department: Optional[str] = None,
    task_status: Optional[str] = None,
    vessel_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM ai_tasks
                WHERE tenant_id = :tenant_id
                  AND (
                      :department IS NULL
                      OR department = :department
                  )
                  AND (
                      :task_status IS NULL
                      OR task_status = :task_status
                  )
                  AND (
                      :vessel_id IS NULL
                      OR vessel_id = :vessel_id
                  )
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {
                "tenant_id": str(identity.tenant_id),
                "department": department,
                "task_status": task_status,
                "vessel_id": vessel_id,
                "limit": limit,
            },
        ).mappings().all()

    tasks = []

    for row in rows:
        item = serialize_ai_row(row)

        for source_key, target_key in (
            ("input_json", "input_data"),
            ("result_json", "result_data"),
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

        tasks.append(item)

    return {
        "count": len(tasks),
        "tasks": tasks,
    }


# ============================================================
# GET AI TASK DETAILS
# ============================================================

@router.get(
    "/tasks/{task_id}",
    summary="Get Enterprise AI Task Details",
)
def get_ai_task(
    task_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        row = conn.execute(
            text("""
                SELECT *
                FROM ai_tasks
                WHERE id = :task_id
                  AND tenant_id = :tenant_id
            """),
            {
                "task_id": task_id,
                "tenant_id": str(identity.tenant_id),
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="AI task not found.",
        )

    item = serialize_ai_row(row)

    for source_key, target_key in (
        ("input_json", "input_data"),
        ("result_json", "result_data"),
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
# START AI TASK PROCESSING
# ============================================================

@router.post(
    "/tasks/{task_id}/start",
    summary="Start AI Task Processing",
)
def start_ai_task(
    task_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    now = datetime.now(timezone.utc)

    with get_engine().begin() as conn:
        row = conn.execute(
            text("""
                SELECT
                    t.id,
                    t.task_status,
                    a.is_active
                FROM ai_tasks t
                JOIN ai_agents a
                  ON a.id = t.agent_id
                 AND a.tenant_id = t.tenant_id
                WHERE t.id = :task_id
                  AND t.tenant_id = :tenant_id
                FOR UPDATE OF t
            """),
            {
                "task_id": task_id,
                "tenant_id": str(identity.tenant_id),
            },
        ).mappings().first()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="AI task not found.",
            )

        if not row["is_active"]:
            raise HTTPException(
                status_code=409,
                detail="Assigned AI agent is inactive.",
            )

        if row["task_status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail="Only pending tasks can be started.",
            )

        conn.execute(
            text("""
                UPDATE ai_tasks
                SET task_status = 'processing',
                    updated_at = :updated_at
                WHERE id = :task_id
                  AND tenant_id = :tenant_id
            """),
            {
                "updated_at": now,
                "task_id": task_id,
                "tenant_id": str(identity.tenant_id),
            },
        )

        record_ai_audit(
            conn,
            identity,
            "task",
            task_id,
            "processing_started",
            "Task status changed to processing.",
        )

    return {
        "task_id": str(task_id),
        "task_status": "processing",
        "updated_at": now.isoformat(),
        "note": (
            "Processing status recorded. "
            "This endpoint does not invoke an AI model."
        ),
    }


# ============================================================
# SUBMIT AI TASK RESULT
# ============================================================

@router.post(
    "/tasks/{task_id}/result",
    summary="Submit AI Task Analysis Result",
)
def submit_ai_task_result(
    task_id: UUID,
    payload: AITaskResultSubmit,
    identity: TokenIdentity = Depends(verify_access_token),
):
    now = datetime.now(timezone.utc)

    with get_engine().begin() as conn:
        existing = conn.execute(
            text("""
                SELECT id, task_status
                FROM ai_tasks
                WHERE id = :task_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "task_id": task_id,
                "tenant_id": str(identity.tenant_id),
            },
        ).mappings().first()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="AI task not found.",
            )

        if existing["task_status"] != "processing":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Only processing tasks can "
                    "receive analysis results."
                ),
            )

        conn.execute(
            text("""
                UPDATE ai_tasks
                SET result_json = :result_json,
                    confidence_score = :confidence_score,
                    explanation = :explanation,
                    task_status = 'completed',
                    updated_at = :updated_at
                WHERE id = :task_id
                  AND tenant_id = :tenant_id
            """),
            {
                "result_json": ai_json(payload.result_data),
                "confidence_score": payload.confidence_score,
                "explanation": payload.explanation,
                "updated_at": now,
                "task_id": task_id,
                "tenant_id": str(identity.tenant_id),
            },
        )

        record_ai_audit(
            conn,
            identity,
            "task",
            task_id,
            "result_submitted",
            "Analysis result submitted and task completed.",
        )

    return {
        "task_id": str(task_id),
        "task_status": "completed",
        "confidence_score": payload.confidence_score,
        "completed_at": now.isoformat(),
        "note": (
            "Result stored as a submitted record. "
            "AI model generation is not verified "
            "by this endpoint."
        ),
    }


# ============================================================
# AI TASK MONITORING
# ============================================================

@router.get(
    "/intelligence/tasks",
    summary="Enterprise AI Task Monitoring",
)
def ai_task_monitoring(
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    department,
                    task_status,
                    priority,
                    COUNT(*) AS task_count
                FROM ai_tasks
                WHERE tenant_id = :tenant_id
                GROUP BY
                    department,
                    task_status,
                    priority
                ORDER BY
                    department,
                    task_status,
                    priority
            """),
            {
                "tenant_id": str(identity.tenant_id),
            },
        ).mappings().all()

    results = [
        serialize_ai_row(row)
        for row in rows
    ]

    return {
        "module": "AI Task Monitoring",
        "total_tasks": sum(
            item["task_count"]
            for item in results
        ),
        "groups": results,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }


# ============================================================
# END OF AI BLOK 3
# ============================================================

# ============================================================
# AI BLOK 4 - RECOMMENDATIONS & HUMAN APPROVAL
# ============================================================


# ============================================================
# CREATE AI RECOMMENDATION
# ============================================================

@router.post(
    "/recommendations",
    status_code=status.HTTP_201_CREATED,
    summary="Create AI Decision Recommendation",
)
def create_ai_recommendation(
    payload: AIRecommendationCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    allowed_risks = {"low", "medium", "high", "critical"}

    if payload.risk_level not in allowed_risks:
        raise HTTPException(
            status_code=422,
            detail="Invalid recommendation risk level.",
        )

    recommendation_id = uuid4()
    now = datetime.now(timezone.utc)
    tenant_id = str(identity.tenant_id)

    with get_engine().begin() as conn:
        task = conn.execute(
            text("""
                SELECT t.id, t.task_status,
                       a.requires_approval
                FROM ai_tasks t
                JOIN ai_agents a
                  ON a.id = t.agent_id
                 AND a.tenant_id = t.tenant_id
                WHERE t.id = :task_id
                  AND t.tenant_id = :tenant_id
            """),
            {
                "task_id": payload.task_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if task is None:
            raise HTTPException(
                status_code=404,
                detail="AI task not found.",
            )

        if task["task_status"] != "completed":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Recommendation requires "
                    "a completed AI task."
                ),
            )

        conn.execute(
            text("""
                INSERT INTO ai_recommendations (
                    id, tenant_id, task_id,
                    recommendation_title,
                    recommendation_text,
                    risk_level, proposed_action,
                    evidence_json, approval_status,
                    created_at
                )
                VALUES (
                    :id, :tenant_id, :task_id,
                    :recommendation_title,
                    :recommendation_text,
                    :risk_level, :proposed_action,
                    :evidence_json, 'pending',
                    :created_at
                )
            """),
            {
                "id": recommendation_id,
                "tenant_id": tenant_id,
                "task_id": payload.task_id,
                "recommendation_title":
                    payload.recommendation_title,
                "recommendation_text":
                    payload.recommendation_text,
                "risk_level": payload.risk_level,
                "proposed_action": payload.proposed_action,
                "evidence_json": ai_json(payload.evidence),
                "created_at": now,
            },
        )

        record_ai_audit(
            conn,
            identity,
            "recommendation",
            recommendation_id,
            "created",
            payload.recommendation_title,
        )

    return {
        "id": str(recommendation_id),
        "task_id": str(payload.task_id),
        "recommendation_title":
            payload.recommendation_title,
        "risk_level": payload.risk_level,
        "approval_status": "pending",
        "requires_human_review": True,
        "created_at": now.isoformat(),
        "note": (
            "Recommendation is awaiting human review. "
            "No operational action has been executed."
        ),
    }


# ============================================================
# LIST AI RECOMMENDATIONS
# ============================================================

@router.get(
    "/recommendations",
    summary="List AI Decision Recommendations",
)
def list_ai_recommendations(
    approval_status: Optional[str] = None,
    risk_level: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    r.*,
                    t.department,
                    t.vessel_id,
                    t.task_title
                FROM ai_recommendations r
                JOIN ai_tasks t
                  ON t.id = r.task_id
                 AND t.tenant_id = r.tenant_id
                WHERE r.tenant_id = :tenant_id
                  AND (
                      :approval_status IS NULL
                      OR r.approval_status = :approval_status
                  )
                  AND (
                      :risk_level IS NULL
                      OR r.risk_level = :risk_level
                  )
                  AND (
                      :department IS NULL
                      OR t.department = :department
                  )
                ORDER BY r.created_at DESC
                LIMIT :limit
            """),
            {
                "tenant_id": str(identity.tenant_id),
                "approval_status": approval_status,
                "risk_level": risk_level,
                "department": department,
                "limit": limit,
            },
        ).mappings().all()

    recommendations = []

    for row in rows:
        item = serialize_ai_row(row)
        raw = item.pop("evidence_json", None)

        try:
            item["evidence"] = (
                json.loads(raw) if raw else {}
            )
        except (TypeError, ValueError):
            item["evidence"] = {}

        recommendations.append(item)

    return {
        "count": len(recommendations),
        "recommendations": recommendations,
    }


# ============================================================
# GET AI RECOMMENDATION DETAILS
# ============================================================

@router.get(
    "/recommendations/{recommendation_id}",
    summary="Get AI Recommendation Details",
)
def get_ai_recommendation(
    recommendation_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        row = conn.execute(
            text("""
                SELECT
                    r.*,
                    t.department,
                    t.vessel_id,
                    t.task_title
                FROM ai_recommendations r
                JOIN ai_tasks t
                  ON t.id = r.task_id
                 AND t.tenant_id = r.tenant_id
                WHERE r.id = :recommendation_id
                  AND r.tenant_id = :tenant_id
            """),
            {
                "recommendation_id": recommendation_id,
                "tenant_id": str(identity.tenant_id),
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="AI recommendation not found.",
        )

    item = serialize_ai_row(row)
    raw = item.pop("evidence_json", None)

    try:
        item["evidence"] = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        item["evidence"] = {}

    return item


# ============================================================
# HUMAN APPROVAL DECISION
# ============================================================

@router.post(
    "/recommendations/{recommendation_id}/decision",
    summary="Human Review of AI Recommendation",
)
def review_ai_recommendation(
    recommendation_id: UUID,
    payload: AIApprovalDecision,
    identity: TokenIdentity = Depends(verify_access_token),
):
    decision = payload.decision.strip().lower()

    if decision not in {"approved", "rejected"}:
        raise HTTPException(
            status_code=422,
            detail="Decision must be approved or rejected.",
        )

    # Temporary conservative authorization gate.
    # Must be aligned with centralized RBAC before production.
    reviewer_roles = {
        "admin",
        "super_admin",
        "tenant_admin",
        "manager",
        "dpa",
    }

    role = str(identity.role).strip().lower()

    if role not in reviewer_roles:
        raise HTTPException(
            status_code=403,
            detail="Role is not authorized for AI approval.",
        )

    now = datetime.now(timezone.utc)
    tenant_id = str(identity.tenant_id)

    with get_engine().begin() as conn:
        existing = conn.execute(
            text("""
                SELECT
                    r.id,
                    r.approval_status,
                    r.risk_level,
                    t.created_by AS task_creator
                FROM ai_recommendations r
                JOIN ai_tasks t
                  ON t.id = r.task_id
                 AND t.tenant_id = r.tenant_id
                WHERE r.id = :recommendation_id
                  AND r.tenant_id = :tenant_id
                FOR UPDATE OF r
            """),
            {
                "recommendation_id": recommendation_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="AI recommendation not found.",
            )

        if existing["approval_status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail="Recommendation already reviewed.",
            )

        if (
            existing["task_creator"] is not None
            and str(existing["task_creator"])
            == str(identity.user_id)
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Task creator cannot approve "
                    "their own recommendation."
                ),
            )

        conn.execute(
            text("""
                UPDATE ai_recommendations
                SET approval_status = :decision,
                    reviewed_by = :reviewed_by,
                    reviewed_at = :reviewed_at,
                    review_remarks = :review_remarks
                WHERE id = :recommendation_id
                  AND tenant_id = :tenant_id
            """),
            {
                "decision": decision,
                "reviewed_by": str(identity.user_id),
                "reviewed_at": now,
                "review_remarks": payload.remarks,
                "recommendation_id": recommendation_id,
                "tenant_id": tenant_id,
            },
        )

        record_ai_audit(
            conn,
            identity,
            "recommendation",
            recommendation_id,
            decision,
            ai_json({
                "risk_level": existing["risk_level"],
                "remarks": payload.remarks,
            }),
        )

    return {
        "recommendation_id": str(recommendation_id),
        "approval_status": decision,
        "reviewed_by": str(identity.user_id),
        "reviewed_at": now.isoformat(),
        "operational_action_executed": False,
        "note": (
            "Human decision recorded. "
            "Approval does not execute "
            "an operational command."
        ),
    }


# ============================================================
# AI DECISION INTELLIGENCE
# ============================================================

@router.get(
    "/intelligence/decisions",
    summary="Enterprise AI Decision Intelligence",
)
def ai_decision_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    t.department,
                    r.risk_level,
                    r.approval_status,
                    COUNT(*) AS recommendation_count
                FROM ai_recommendations r
                JOIN ai_tasks t
                  ON t.id = r.task_id
                 AND t.tenant_id = r.tenant_id
                WHERE r.tenant_id = :tenant_id
                GROUP BY
                    t.department,
                    r.risk_level,
                    r.approval_status
                ORDER BY
                    t.department,
                    r.risk_level,
                    r.approval_status
            """),
            {
                "tenant_id": str(identity.tenant_id),
            },
        ).mappings().all()

    groups = [
        serialize_ai_row(row)
        for row in rows
    ]

    return {
        "module": "AI Decision Intelligence",
        "total_recommendations": sum(
            item["recommendation_count"]
            for item in groups
        ),
        "pending_review": sum(
            item["recommendation_count"]
            for item in groups
            if item["approval_status"] == "pending"
        ),
        "approved": sum(
            item["recommendation_count"]
            for item in groups
            if item["approval_status"] == "approved"
        ),
        "rejected": sum(
            item["recommendation_count"]
            for item in groups
            if item["approval_status"] == "rejected"
        ),
        "groups": groups,
        "generated_at":
            datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# END OF AI BLOK 4
# ============================================================

# ============================================================
# MQEIP - AI INTELLIGENCE CENTER
# BLOK 5 - EXECUTIVE DASHBOARD & FINAL INITIALIZATION
# ============================================================


# ============================================================
# EXECUTIVE AI COMMAND DASHBOARD
# ============================================================

@router.get(
    "/dashboard",
    summary="Executive AI Intelligence Dashboard",
)
def ai_executive_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:

        agents = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_agents,
                    COUNT(*) FILTER (
                        WHERE is_active = TRUE
                    ) AS active_agents,
                    COUNT(*) FILTER (
                        WHERE requires_approval = TRUE
                    ) AS approval_required_agents,
                    COUNT(*) FILTER (
                        WHERE model_provider = 'not_configured'
                           OR model_name = 'not_configured'
                    ) AS unconfigured_models
                FROM ai_agents
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": tenant_id},
        ).mappings().one()

        tasks = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_tasks,
                    COUNT(*) FILTER (
                        WHERE task_status = 'pending'
                    ) AS pending_tasks,
                    COUNT(*) FILTER (
                        WHERE task_status = 'processing'
                    ) AS processing_tasks,
                    COUNT(*) FILTER (
                        WHERE task_status = 'completed'
                    ) AS completed_tasks
                FROM ai_tasks
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": tenant_id},
        ).mappings().one()

        decisions = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_recommendations,
                    COUNT(*) FILTER (
                        WHERE approval_status = 'pending'
                    ) AS pending_approvals,
                    COUNT(*) FILTER (
                        WHERE approval_status = 'approved'
                    ) AS approved_recommendations,
                    COUNT(*) FILTER (
                        WHERE approval_status = 'rejected'
                    ) AS rejected_recommendations,
                    COUNT(*) FILTER (
                        WHERE risk_level = 'critical'
                          AND approval_status = 'pending'
                    ) AS critical_pending_reviews
                FROM ai_recommendations
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": tenant_id},
        ).mappings().one()

        departments = conn.execute(
            text("""
                SELECT
                    department,
                    COUNT(*) AS agent_count,
                    COUNT(*) FILTER (
                        WHERE is_active = TRUE
                    ) AS active_agent_count
                FROM ai_agents
                WHERE tenant_id = :tenant_id
                GROUP BY department
                ORDER BY department
            """),
            {"tenant_id": tenant_id},
        ).mappings().all()

    return {
        "platform": "MQEIP",
        "module": "Executive AI Intelligence Dashboard",
        "agents": serialize_ai_row(agents),
        "tasks": serialize_ai_row(tasks),
        "decisions": serialize_ai_row(decisions),
        "departments": [
            serialize_ai_row(row)
            for row in departments
        ],
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "note": (
            "Dashboard reports registered AI agents, "
            "tasks and human decisions. It does not "
            "establish that AI inference is operational."
        ),
    }


# ============================================================
# AI AUDIT TRAIL
# ============================================================

@router.get(
    "/audit-trail",
    summary="Enterprise AI Audit Trail",
)
def get_ai_audit_trail(
    entity_type: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=1000),
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM ai_audit_log
                WHERE tenant_id = :tenant_id
                  AND (
                      :entity_type IS NULL
                      OR entity_type = :entity_type
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
            """),
            {
                "tenant_id": str(identity.tenant_id),
                "entity_type": entity_type,
                "limit": limit,
            },
        ).mappings().all()

    return {
        "module": "AI Audit Trail",
        "record_count": len(rows),
        "records": [
            serialize_ai_row(row)
            for row in rows
        ],
    }


# ============================================================
# AI DATA QUALITY INTELLIGENCE
# ============================================================

@router.get(
    "/data-quality",
    summary="AI Intelligence Data Quality",
)
def ai_data_quality(
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:

        agent_quality = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_agents,
                    COUNT(*) FILTER (
                        WHERE model_provider = 'not_configured'
                           OR model_name = 'not_configured'
                    ) AS unconfigured_models,
                    COUNT(*) FILTER (
                        WHERE capabilities_json = '[]'
                    ) AS agents_without_capabilities
                FROM ai_agents
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": tenant_id},
        ).mappings().one()

        task_quality = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_tasks,
                    COUNT(*) FILTER (
                        WHERE task_status = 'completed'
                          AND result_json IS NULL
                    ) AS completed_without_result,
                    COUNT(*) FILTER (
                        WHERE task_status = 'completed'
                          AND confidence_score IS NULL
                    ) AS completed_without_confidence,
                    COUNT(*) FILTER (
                        WHERE vessel_id IS NULL
                    ) AS tasks_without_vessel
                FROM ai_tasks
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": tenant_id},
        ).mappings().one()

    return {
        "module": "AI Data Quality Intelligence",
        "agent_quality": serialize_ai_row(agent_quality),
        "task_quality": serialize_ai_row(task_quality),
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "note": (
            "Vessel reference and confidence score "
            "may legitimately be absent for some tasks."
        ),
    }


# ============================================================
# AI SYSTEM STATUS
# ============================================================

@router.get(
    "/system/status",
    summary="AI Intelligence System Status",
)
def ai_system_status(
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))

    return {
        "platform": "MQEIP",
        "module": (
            "AI Intelligence Center & Autonomous AI Agents"
        ),
        "database": "connected",
        "authentication": "required",
        "tenant_id": str(identity.tenant_id),
        "registry": "available",
        "task_management": "available",
        "human_approval": "available",
        "model_inference": "not_configured",
        "autonomous_execution": "disabled",
        "status": "operational",
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),
    }


# ============================================================
# AI DATABASE INITIALIZATION
# ============================================================

init_ai_tables()


# ============================================================
# END OF AI INTELLIGENCE MODULE
# ============================================================

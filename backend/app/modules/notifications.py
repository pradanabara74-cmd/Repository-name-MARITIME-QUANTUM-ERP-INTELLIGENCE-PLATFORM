# ============================================================
# MQEIP - NOTIFICATIONS & ACTION TRACKER INTELLIGENCE
# BLOK 1 - DATABASE, MODELS & SECURITY
# ============================================================

from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import RowMapping

from ..core.database import get_engine
from ..core.security import (
    TokenIdentity,
    verify_access_token,
)

router = APIRouter(
    prefix="/api/v1/notifications",
    tags=["Notifications & Action Tracker Intelligence"],
)


# ============================================================
# REQUEST MODELS
# ============================================================

class NotificationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1)
    notification_type: str = Field(
        default="general",
        max_length=100,
    )
    priority: str = Field(default="medium")
    recipient_user_id: Optional[str] = None
    department: Optional[str] = None
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None


class NotificationStatusUpdate(BaseModel):
    status: str


class ActionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    department: Optional[str] = None
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    assigned_to: Optional[str] = None
    priority: str = Field(default="medium")
    due_date: Optional[date] = None
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None


class ActionStatusUpdate(BaseModel):
    status: str
    remarks: Optional[str] = None
    closure_evidence: Optional[str] = None


# ============================================================
# HELPERS
# ============================================================

def serialize_row(row):
    if row is None:
        return None

    result = dict(row)

    for key, value in result.items():
        if isinstance(value, UUID):
            result[key] = str(value)

        elif isinstance(value, (datetime, date)):
            result[key] = value.isoformat()

    return result


def validate_priority(priority: str) -> str:
    value = priority.strip().lower()

    if value not in {
        "low",
        "medium",
        "high",
        "critical",
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid priority.",
        )

    return value


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_notification_tables():
    engine = get_engine()

    with engine.begin() as conn:

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mq_notifications (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    message TEXT NOT NULL,
                    notification_type VARCHAR(100)
                        NOT NULL DEFAULT 'general',
                    priority VARCHAR(30)
                        NOT NULL DEFAULT 'medium',
                    recipient_user_id VARCHAR(255),
                    department VARCHAR(255),
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    reference_type VARCHAR(100),
                    reference_id VARCHAR(255),
                    status VARCHAR(30)
                        NOT NULL DEFAULT 'unread',
                    created_by VARCHAR(255) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    read_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mq_action_tracker (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    description TEXT,
                    department VARCHAR(255),
                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),
                    assigned_to VARCHAR(255),
                    priority VARCHAR(30)
                        NOT NULL DEFAULT 'medium',
                    due_date DATE,
                    reference_type VARCHAR(100),
                    reference_id VARCHAR(255),
                    status VARCHAR(30)
                        NOT NULL DEFAULT 'open',
                    remarks TEXT,
                    closure_evidence TEXT,
                    created_by VARCHAR(255) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    completed_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_mq_notifications_tenant_status
                ON mq_notifications (
                    tenant_id,
                    status,
                    created_at
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_mq_notifications_recipient
                ON mq_notifications (
                    tenant_id,
                    recipient_user_id
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_mq_actions_tenant_status
                ON mq_action_tracker (
                    tenant_id,
                    status,
                    due_date
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_mq_actions_assigned
                ON mq_action_tracker (
                    tenant_id,
                    assigned_to
                )
                """
            )
        )

# ============================================================
# BLOK 2 - NOTIFICATION MANAGEMENT
# ============================================================

@router.get(
    "",
    summary="List Notifications",
)
def list_notifications(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM mq_notifications
                WHERE tenant_id = :tenant_id
                  AND (
                      recipient_user_id IS NULL
                      OR recipient_user_id = :user_id
                  )
                ORDER BY created_at DESC
                LIMIT 500
            """),
            {
                "tenant_id": identity.tenant_id,
                "user_id": identity.user_id,
            },
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create Notification",
)
def create_notification(
    payload: NotificationCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    priority = validate_priority(payload.priority)

    notification_id = uuid4()
    now = datetime.now(timezone.utc)
    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO mq_notifications (
                    id,
                    tenant_id,
                    title,
                    message,
                    notification_type,
                    priority,
                    recipient_user_id,
                    department,
                    vessel_id,
                    vessel_name,
                    reference_type,
                    reference_id,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :title,
                    :message,
                    :notification_type,
                    :priority,
                    :recipient_user_id,
                    :department,
                    :vessel_id,
                    :vessel_name,
                    :reference_type,
                    :reference_id,
                    'unread',
                    :created_by,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "id": notification_id,
                "tenant_id": identity.tenant_id,
                "title": payload.title,
                "message": payload.message,
                "notification_type": payload.notification_type,
                "priority": priority,
                "recipient_user_id": payload.recipient_user_id,
                "department": payload.department,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "reference_type": payload.reference_type,
                "reference_id": payload.reference_id,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(notification_id),
        "title": payload.title,
        "priority": priority,
        "status": "unread",
        "created_at": now.isoformat(),
    }


@router.get(
    "/{notification_id}",
    summary="Get Notification Details",
)
def get_notification(
    notification_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text("""
                SELECT *
                FROM mq_notifications
                WHERE id = :notification_id
                  AND tenant_id = :tenant_id
                  AND (
                      recipient_user_id IS NULL
                      OR recipient_user_id = :user_id
                  )
            """),
            {
                "notification_id": notification_id,
                "tenant_id": identity.tenant_id,
                "user_id": identity.user_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Notification not found.",
        )

    return serialize_row(row)


@router.post(
    "/{notification_id}/status",
    summary="Update Notification Status",
)
def update_notification_status(
    notification_id: UUID,
    payload: NotificationStatusUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    new_status = payload.status.strip().lower()

    if new_status not in {"read", "unread", "archived"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid notification status.",
        )

    now = datetime.now(timezone.utc)
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text("""
                UPDATE mq_notifications
                SET status = :status,
                    read_at = CASE
                        WHEN :status = 'read'
                        THEN COALESCE(read_at, :now)
                        WHEN :status = 'unread'
                        THEN NULL
                        ELSE read_at
                    END,
                    updated_at = :now
                WHERE id = :notification_id
                  AND tenant_id = :tenant_id
                  AND (
                      recipient_user_id IS NULL
                      OR recipient_user_id = :user_id
                  )
                RETURNING id
            """),
            {
                "status": new_status,
                "now": now,
                "notification_id": notification_id,
                "tenant_id": identity.tenant_id,
                "user_id": identity.user_id,
            },
        ).first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Notification not found.",
        )

    return {
        "id": str(notification_id),
        "status": new_status,
        "updated_at": now.isoformat(),
    }


@router.get(
    "/intelligence/unread",
    summary="Unread Notifications Intelligence",
)
def unread_notifications_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_notifications,
                    COUNT(*) FILTER (
                        WHERE status = 'unread'
                    ) AS unread_notifications,
                    COUNT(*) FILTER (
                        WHERE status = 'unread'
                          AND priority = 'critical'
                    ) AS critical_unread,
                    COUNT(*) FILTER (
                        WHERE status = 'unread'
                          AND priority = 'high'
                    ) AS high_unread
                FROM mq_notifications
                WHERE tenant_id = :tenant_id
                  AND (
                      recipient_user_id IS NULL
                      OR recipient_user_id = :user_id
                  )
            """),
            {
                "tenant_id": identity.tenant_id,
                "user_id": identity.user_id,
            },
        ).mappings().first()

    return serialize_row(row)

# ============================================================
# BLOK 3 - ACTION TRACKER & CORRECTIVE ACTION MANAGEMENT
# ============================================================

@router.get(
    "/actions",
    summary="List Action Tracker",
)
def list_actions(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM mq_action_tracker
                WHERE tenant_id = :tenant_id
                ORDER BY
                    CASE
                        WHEN status = 'closed' THEN 1
                        ELSE 0
                    END,
                    due_date ASC NULLS LAST,
                    created_at DESC
                LIMIT 500
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post(
    "/actions",
    status_code=status.HTTP_201_CREATED,
    summary="Create Action Tracker Item",
)
def create_action(
    payload: ActionCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    priority = validate_priority(payload.priority)

    action_id = uuid4()
    now = datetime.now(timezone.utc)
    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO mq_action_tracker (
                    id,
                    tenant_id,
                    title,
                    description,
                    department,
                    vessel_id,
                    vessel_name,
                    assigned_to,
                    priority,
                    due_date,
                    reference_type,
                    reference_id,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :title,
                    :description,
                    :department,
                    :vessel_id,
                    :vessel_name,
                    :assigned_to,
                    :priority,
                    :due_date,
                    :reference_type,
                    :reference_id,
                    'open',
                    :created_by,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "id": action_id,
                "tenant_id": identity.tenant_id,
                "title": payload.title,
                "description": payload.description,
                "department": payload.department,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "assigned_to": payload.assigned_to,
                "priority": priority,
                "due_date": payload.due_date,
                "reference_type": payload.reference_type,
                "reference_id": payload.reference_id,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(action_id),
        "title": payload.title,
        "status": "open",
        "priority": priority,
        "assigned_to": payload.assigned_to,
        "due_date": (
            payload.due_date.isoformat()
            if payload.due_date else None
        ),
        "created_at": now.isoformat(),
    }


@router.get(
    "/actions/{action_id}",
    summary="Get Action Tracker Details",
)
def get_action(
    action_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text("""
                SELECT *
                FROM mq_action_tracker
                WHERE id = :action_id
                  AND tenant_id = :tenant_id
            """),
            {
                "action_id": action_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Action tracker item not found.",
        )

    return serialize_row(row)


@router.post(
    "/actions/{action_id}/status",
    summary="Update Action Tracker Status",
)
def update_action_status(
    action_id: UUID,
    payload: ActionStatusUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    new_status = payload.status.strip().lower()

    valid_statuses = {
        "open",
        "in_progress",
        "on_hold",
        "closed",
    }

    if new_status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail="Invalid action status.",
        )

    if (
        new_status == "closed"
        and not (payload.closure_evidence or "").strip()
    ):
        raise HTTPException(
            status_code=400,
            detail="Closure evidence is required.",
        )

    now = datetime.now(timezone.utc)
    engine = get_engine()

    with engine.begin() as conn:
        updated = conn.execute(
            text("""
                UPDATE mq_action_tracker
                SET status = :status,
                    remarks = COALESCE(
                        :remarks, remarks
                    ),
                    closure_evidence = COALESCE(
                        :closure_evidence,
                        closure_evidence
                    ),
                    completed_at = CASE
                        WHEN :status = 'closed'
                        THEN :now
                        ELSE NULL
                    END,
                    updated_at = :now
                WHERE id = :action_id
                  AND tenant_id = :tenant_id
                RETURNING id
            """),
            {
                "status": new_status,
                "remarks": payload.remarks,
                "closure_evidence":
                    payload.closure_evidence,
                "now": now,
                "action_id": action_id,
                "tenant_id": identity.tenant_id,
            },
        ).first()

    if updated is None:
        raise HTTPException(
            status_code=404,
            detail="Action tracker item not found.",
        )

    return {
        "id": str(action_id),
        "status": new_status,
        "updated_at": now.isoformat(),
    }


@router.get(
    "/intelligence/actions",
    summary="Action Tracker Intelligence",
)
def action_tracker_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        summary = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_actions,
                    COUNT(*) FILTER (
                        WHERE status = 'open'
                    ) AS open_actions,
                    COUNT(*) FILTER (
                        WHERE status = 'in_progress'
                    ) AS in_progress_actions,
                    COUNT(*) FILTER (
                        WHERE status = 'on_hold'
                    ) AS on_hold_actions,
                    COUNT(*) FILTER (
                        WHERE status = 'closed'
                    ) AS closed_actions,
                    COUNT(*) FILTER (
                        WHERE status <> 'closed'
                          AND due_date < CURRENT_DATE
                    ) AS overdue_actions,
                    COUNT(*) FILTER (
                        WHERE status <> 'closed'
                          AND priority = 'critical'
                    ) AS critical_open_actions,
                    COUNT(*) FILTER (
                        WHERE status <> 'closed'
                          AND due_date BETWEEN
                              CURRENT_DATE
                              AND CURRENT_DATE + 7
                    ) AS due_next_7_days
                FROM mq_action_tracker
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        overdue = conn.execute(
            text("""
                SELECT *
                FROM mq_action_tracker
                WHERE tenant_id = :tenant_id
                  AND status <> 'closed'
                  AND due_date < CURRENT_DATE
                ORDER BY due_date ASC
                LIMIT 100
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "summary": serialize_row(summary),
        "overdue_actions": [
            serialize_row(row) for row in overdue
        ],
    }

# ============================================================
# BLOK 4 - DASHBOARD, RISK INTELLIGENCE & AUDIT TRAIL
# ============================================================

@router.get(
    "/dashboard",
    summary="Notifications and Action Tracker Dashboard",
)
def notifications_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        notifications = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_notifications,
                    COUNT(*) FILTER (
                        WHERE is_read = FALSE
                    ) AS unread_notifications,
                    COUNT(*) FILTER (
                        WHERE priority = 'critical'
                    ) AS critical_notifications
                FROM mq_notifications
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

        actions = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_actions,
                    COUNT(*) FILTER (
                        WHERE status <> 'closed'
                    ) AS pending_actions,
                    COUNT(*) FILTER (
                        WHERE status = 'closed'
                    ) AS completed_actions,
                    COUNT(*) FILTER (
                        WHERE status <> 'closed'
                          AND due_date < CURRENT_DATE
                    ) AS overdue_actions
                FROM mq_action_tracker
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

    return {
        "module": "Notifications & Action Tracker Intelligence",
        "notifications": serialize_row(notifications),
        "actions": serialize_row(actions),
    }


@router.get(
    "/risk-intelligence",
    summary="Notifications and Action Risk Intelligence",
)
def notifications_risk_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        result = conn.execute(
            text("""
                SELECT
                    COUNT(*) FILTER (
                        WHERE status <> 'closed'
                          AND priority = 'critical'
                    ) AS critical_actions,
                    COUNT(*) FILTER (
                        WHERE status <> 'closed'
                          AND priority = 'high'
                    ) AS high_priority_actions,
                    COUNT(*) FILTER (
                        WHERE status <> 'closed'
                          AND due_date < CURRENT_DATE
                    ) AS overdue_actions,
                    COUNT(*) FILTER (
                        WHERE status = 'on_hold'
                    ) AS on_hold_actions
                FROM mq_action_tracker
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().first()

    data = serialize_row(result)

    critical = int(data["critical_actions"])
    high = int(data["high_priority_actions"])
    overdue = int(data["overdue_actions"])
    on_hold = int(data["on_hold_actions"])

    risk_score = (
        critical * 5
        + high * 3
        + overdue * 4
        + on_hold * 2
    )

    if risk_score >= 30:
        risk_level = "CRITICAL"
    elif risk_score >= 15:
        risk_level = "HIGH"
    elif risk_score >= 5:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        "risk_level": risk_level,
        "risk_score": risk_score,
        "indicators": data,
        "scoring_method": "Operational priority heuristic",
    }


@router.get(
    "/audit-trail",
    summary="Notifications and Action Activity Audit Trail",
)
def notifications_audit_trail(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM (
                    SELECT
                        'notification_created' AS event_type,
                        id AS record_id,
                        title AS description,
                        created_by AS performed_by,
                        created_at AS event_time
                    FROM mq_notifications
                    WHERE tenant_id = :tenant_id

                    UNION ALL

                    SELECT
                        'action_created' AS event_type,
                        id AS record_id,
                        title AS description,
                        created_by AS performed_by,
                        created_at AS event_time
                    FROM mq_action_tracker
                    WHERE tenant_id = :tenant_id
                ) AS events
                ORDER BY event_time DESC
                LIMIT 500
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [
        serialize_row(row)
        for row in rows
    ]


# ============================================================
# INITIALIZE NOTIFICATIONS DATABASE
# ============================================================

init_notification_tables()

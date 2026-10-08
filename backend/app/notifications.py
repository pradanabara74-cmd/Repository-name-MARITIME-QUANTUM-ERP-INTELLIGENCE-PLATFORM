from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..core.database import get_engine
from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/notifications",
    tags=["Notifications & Action Tracker Intelligence"],
)


# ============================================================
# DATA MODELS
# ============================================================

class NotificationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1)
    category: str = Field(default="general", max_length=100)
    priority: str = Field(default="normal", max_length=30)
    recipient_user_id: Optional[str] = None
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    source_module: Optional[str] = None
    reference_id: Optional[str] = None


class ActionCreate(BaseModel):
    action_number: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    department: str = Field(min_length=1, max_length=100)
    assigned_to: str = Field(min_length=1, max_length=255)
    due_date: date
    priority: str = Field(default="medium", max_length=30)
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None
    source_module: Optional[str] = None
    reference_id: Optional[str] = None


class ActionStatusUpdate(BaseModel):
    status: str
    remarks: Optional[str] = None
    completion_evidence: Optional[str] = None


def serialize_row(row):
    result = dict(row)

    for key, value in result.items():
        if isinstance(value, UUID):
            result[key] = str(value)
        elif isinstance(value, (datetime, date)):
            result[key] = value.isoformat()

    return result


# ============================================================
# POSTGRESQL DATABASE
# ============================================================

def init_notification_tables():
    engine = get_engine()

    with engine.begin() as conn:

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mq_notifications (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                title VARCHAR(255) NOT NULL,
                message TEXT NOT NULL,
                category VARCHAR(100) NOT NULL,
                priority VARCHAR(30) NOT NULL,
                recipient_user_id VARCHAR(255),
                vessel_id VARCHAR(255),
                vessel_name VARCHAR(255),
                source_module VARCHAR(100),
                reference_id VARCHAR(255),
                is_read BOOLEAN NOT NULL DEFAULT FALSE,
                created_by VARCHAR(255) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                read_at TIMESTAMPTZ
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mq_action_tracker (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                action_number VARCHAR(100) NOT NULL,
                title VARCHAR(255) NOT NULL,
                description TEXT,
                department VARCHAR(100) NOT NULL,
                assigned_to VARCHAR(255) NOT NULL,
                due_date DATE NOT NULL,
                priority VARCHAR(30) NOT NULL,
                vessel_id VARCHAR(255),
                vessel_name VARCHAR(255),
                source_module VARCHAR(100),
                reference_id VARCHAR(255),
                status VARCHAR(30) NOT NULL DEFAULT 'open',
                remarks TEXT,
                completion_evidence TEXT,
                completed_at TIMESTAMPTZ,
                created_by VARCHAR(255) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE (tenant_id, action_number)
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS
                idx_mq_notifications_recipient
            ON mq_notifications (
                tenant_id, recipient_user_id, is_read
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS
                idx_mq_action_tracker_status
            ON mq_action_tracker (
                tenant_id, status, due_date
            )
        """))

  # ============================================================
# BLOK 2 - NOTIFICATION MANAGEMENT
# ============================================================

@router.get(
    "/messages",
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
    "/messages",
    status_code=status.HTTP_201_CREATED,
    summary="Create Notification",
)
def create_notification(
    payload: NotificationCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    allowed_priorities = {
        "low", "normal", "high", "critical"
    }

    priority = payload.priority.strip().lower()

    if priority not in allowed_priorities:
        raise HTTPException(
            status_code=400,
            detail="Invalid notification priority.",
        )

    notification_id = uuid4()
    now = datetime.now(timezone.utc)
    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO mq_notifications (
                    id, tenant_id, title, message,
                    category, priority, recipient_user_id,
                    vessel_id, vessel_name, source_module,
                    reference_id, is_read, created_by,
                    created_at
                )
                VALUES (
                    :id, :tenant_id, :title, :message,
                    :category, :priority, :recipient_user_id,
                    :vessel_id, :vessel_name, :source_module,
                    :reference_id, FALSE, :created_by,
                    :created_at
                )
            """),
            {
                "id": notification_id,
                "tenant_id": identity.tenant_id,
                "title": payload.title,
                "message": payload.message,
                "category": payload.category,
                "priority": priority,
                "recipient_user_id": payload.recipient_user_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "source_module": payload.source_module,
                "reference_id": payload.reference_id,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

    return {
        "id": str(notification_id),
        "title": payload.title,
        "priority": priority,
        "is_read": False,
        "created_at": now.isoformat(),
    }


@router.get(
    "/messages/unread",
    summary="Unread Notification Summary",
)
def unread_notifications(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        count = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM mq_notifications
                WHERE tenant_id = :tenant_id
                  AND is_read = FALSE
                  AND (
                      recipient_user_id IS NULL
                      OR recipient_user_id = :user_id
                  )
            """),
            {
                "tenant_id": identity.tenant_id,
                "user_id": identity.user_id,
            },
        ).scalar_one()

    return {
        "unread_count": count,
        "user_id": identity.user_id,
    }


@router.post(
    "/messages/{notification_id}/read",
    summary="Mark Notification as Read",
)
def mark_notification_read(
    notification_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        result = conn.execute(
            text("""
                UPDATE mq_notifications
                SET is_read = TRUE,
                    read_at = :read_at
                WHERE id = :notification_id
                  AND tenant_id = :tenant_id
                  AND (
                      recipient_user_id IS NULL
                      OR recipient_user_id = :user_id
                  )
                RETURNING id
            """),
            {
                "notification_id": notification_id,
                "tenant_id": identity.tenant_id,
                "user_id": identity.user_id,
                "read_at": now,
            },
        ).first()

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Notification not found.",
        )

    return {
        "id": str(notification_id),
        "is_read": True,
        "read_at": now.isoformat(),
    }

# ============================================================
# BLOK 3 - ACTION TRACKER MANAGEMENT
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
                ORDER BY due_date ASC, created_at DESC
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
    priority = payload.priority.strip().lower()

    if priority not in {
        "low", "medium", "high", "critical"
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid action priority.",
        )

    engine = get_engine()
    action_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        duplicate = conn.execute(
            text("""
                SELECT id
                FROM mq_action_tracker
                WHERE tenant_id = :tenant_id
                  AND action_number = :action_number
            """),
            {
                "tenant_id": identity.tenant_id,
                "action_number": payload.action_number,
            },
        ).first()

        if duplicate is not None:
            raise HTTPException(
                status_code=409,
                detail="Action number already exists.",
            )

        conn.execute(
            text("""
                INSERT INTO mq_action_tracker (
                    id, tenant_id, action_number,
                    title, description, department,
                    assigned_to, due_date, priority,
                    vessel_id, vessel_name,
                    source_module, reference_id,
                    status, created_by,
                    created_at, updated_at
                )
                VALUES (
                    :id, :tenant_id, :action_number,
                    :title, :description, :department,
                    :assigned_to, :due_date, :priority,
                    :vessel_id, :vessel_name,
                    :source_module, :reference_id,
                    'open', :created_by,
                    :created_at, :updated_at
                )
            """),
            {
                "id": action_id,
                "tenant_id": identity.tenant_id,
                "action_number": payload.action_number,
                "title": payload.title,
                "description": payload.description,
                "department": payload.department,
                "assigned_to": payload.assigned_to,
                "due_date": payload.due_date,
                "priority": priority,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "source_module": payload.source_module,
                "reference_id": payload.reference_id,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(action_id),
        "action_number": payload.action_number,
        "status": "open",
        "priority": priority,
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

    allowed_statuses = {
        "open",
        "in_progress",
        "on_hold",
        "completed",
        "cancelled",
    }

    if new_status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail="Invalid action status.",
        )

    if (
        new_status == "completed"
        and not (
            payload.completion_evidence
            and payload.completion_evidence.strip()
        )
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Completion evidence is required "
                "to complete an action."
            ),
        )

    engine = get_engine()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        existing = conn.execute(
            text("""
                SELECT id, status, completion_evidence
                FROM mq_action_tracker
                WHERE id = :action_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "action_id": action_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="Action tracker item not found.",
            )

        if (
            existing["status"] == "completed"
            and new_status != "completed"
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Completed actions cannot be reopened "
                    "through this endpoint."
                ),
            )

        completed_at = (
            now if new_status == "completed" else None
        )

        conn.execute(
            text("""
                UPDATE mq_action_tracker
                SET status = :status,
                    remarks = COALESCE(
                        :remarks, remarks
                    ),
                    completion_evidence = COALESCE(
                        :completion_evidence,
                        completion_evidence
                    ),
                    completed_at = :completed_at,
                    updated_at = :updated_at
                WHERE id = :action_id
                  AND tenant_id = :tenant_id
            """),
            {
                "status": new_status,
                "remarks": payload.remarks,
                "completion_evidence":
                    payload.completion_evidence,
                "completed_at": completed_at,
                "updated_at": now,
                "action_id": action_id,
                "tenant_id": identity.tenant_id,
            },
        )

    return {
        "id": str(action_id),
        "status": new_status,
        "completed_at": (
            completed_at.isoformat()
            if completed_at else None
        ),
        "updated_at": now.isoformat(),
    }

# ============================================================
# BLOK 4 - DASHBOARD & INTELLIGENCE
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
                    COUNT(*) AS total,
                    COUNT(*) FILTER (
                        WHERE is_read = FALSE
                    ) AS unread,
                    COUNT(*) FILTER (
                        WHERE priority = 'critical'
                          AND is_read = FALSE
                    ) AS critical_unread
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
        ).mappings().one()

        actions = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (
                        WHERE status = 'open'
                    ) AS open_count,
                    COUNT(*) FILTER (
                        WHERE status = 'in_progress'
                    ) AS in_progress,
                    COUNT(*) FILTER (
                        WHERE status = 'completed'
                    ) AS completed,
                    COUNT(*) FILTER (
                        WHERE due_date < CURRENT_DATE
                          AND status NOT IN (
                              'completed', 'cancelled'
                          )
                    ) AS overdue,
                    COUNT(*) FILTER (
                        WHERE priority = 'critical'
                          AND status NOT IN (
                              'completed', 'cancelled'
                          )
                    ) AS critical_active
                FROM mq_action_tracker
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().one()

    return {
        "notifications": dict(notifications),
        "action_tracker": dict(actions),
        "tenant_id": identity.tenant_id,
    }


@router.get(
    "/actions/intelligence/overdue",
    summary="Overdue Action Intelligence",
)
def overdue_action_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    id,
                    action_number,
                    title,
                    department,
                    assigned_to,
                    vessel_name,
                    priority,
                    status,
                    due_date,
                    CURRENT_DATE - due_date AS days_overdue
                FROM mq_action_tracker
                WHERE tenant_id = :tenant_id
                  AND due_date < CURRENT_DATE
                  AND status NOT IN (
                      'completed', 'cancelled'
                  )
                ORDER BY
                    due_date ASC,
                    created_at ASC
                LIMIT 500
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return {
        "total_returned": len(rows),
        "overdue_actions": [
            serialize_row(row) for row in rows
        ],
    }


@router.get(
    "/actions/intelligence/risk",
    summary="Action Tracker Risk Intelligence",
)
def action_risk_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    department,
                    COUNT(*) AS total_active,
                    COUNT(*) FILTER (
                        WHERE priority = 'critical'
                    ) AS critical_actions,
                    COUNT(*) FILTER (
                        WHERE priority = 'high'
                    ) AS high_actions,
                    COUNT(*) FILTER (
                        WHERE due_date < CURRENT_DATE
                    ) AS overdue_actions
                FROM mq_action_tracker
                WHERE tenant_id = :tenant_id
                  AND status NOT IN (
                      'completed', 'cancelled'
                  )
                GROUP BY department
                ORDER BY department
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    results = []

    for row in rows:
        item = dict(row)

        score = (
            int(item["critical_actions"]) * 5
            + int(item["high_actions"]) * 3
            + int(item["overdue_actions"]) * 4
        )

        if score >= 20:
            risk_level = "CRITICAL"
        elif score >= 12:
            risk_level = "HIGH"
        elif score >= 5:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        item["risk_score"] = score
        item["risk_level"] = risk_level
        results.append(item)

    results.sort(
        key=lambda item: item["risk_score"],
        reverse=True,
    )

    return {
        "departments": results,
        "total_departments": len(results),
    }


@router.get(
    "/audit-trail",
    summary="Notifications and Action Tracker Audit Trail",
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

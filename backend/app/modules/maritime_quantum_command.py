# ============================================================
# MQEIP - MARITIME QUANTUM COMMAND CENTER
# BLOK 1 - ENTERPRISE COMMAND FOUNDATION
# ============================================================

import json
import logging
import re
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from sqlalchemy import text

from ..core.database import get_engine
from ..core.security import (
    TokenIdentity,
    verify_access_token,
)


# ============================================================
# API ROUTER
# ============================================================

logger = logging.getLogger("mqeip.quantum_command")

_COMMAND_TABLES_READY = False
_COMMAND_INIT_LOCK = threading.Lock()


def command_ensure_ready():
    """ Memastikan tabel Command Center tersedia (sekali saja). Jika database belum siap, endpoint modul ini menjawab 503, tetapi aplikasi (termasuk login) tetap bisa berjalan. """
    global _COMMAND_TABLES_READY

    if _COMMAND_TABLES_READY:
        return

    with _COMMAND_INIT_LOCK:

        if _COMMAND_TABLES_READY:
            return

        try:
            init_quantum_command_tables()

        except Exception:
            logger.exception(
                "Quantum Command table initialization failed."
            )
            raise HTTPException(
                status_code=503,
                detail="Command Center database is not ready.",
            )

        _COMMAND_TABLES_READY = True


router = APIRouter(
    prefix="/api/v1/quantum-command",
    tags=["Maritime Quantum Command Center"],
    dependencies=[Depends(command_ensure_ready)],
)


# ============================================================
# SECURITY CONFIGURATION
# ============================================================

COMMAND_ADMIN_ROLES = {
    "platform_admin",
    "admin",
    "administrator",
    "superadmin",
    "tenant_admin",
}

COMMAND_EXECUTIVE_ROLES = {
    "platform_admin",
    "admin",
    "administrator",
    "superadmin",
    "tenant_admin",
    "executive",
    "ceo",
    "coo",
    "director",
    "manager",
}

COMMAND_PRIORITY_LEVELS = {
    "low",
    "medium",
    "high",
    "critical",
}

COMMAND_STATUSES = {
    "open",
    "in_progress",
    "resolved",
    "cancelled",
}

COMMAND_DOMAINS = {
    "fleet",
    "marine_operations",
    "technical",
    "crew",
    "finance",
    "supply_chain",
    "inventory",
    "commercial",
    "hsse",
    "compliance",
    "legal",
    "audit",
    "esg",
    "ai_intelligence",
    "quantum_optimization",
    "digital_twin",
    "enterprise",
}

COMMAND_MAX_PAGE_SIZE = 200


# ============================================================
# PYDANTIC BASE MODEL
# ============================================================

class CommandBaseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )


# ============================================================
# COMMAND EVENT MODEL
# ============================================================

class CommandEventCreate(CommandBaseModel):

    title: str = Field(
        min_length=5,
        max_length=200,
    )

    description: str = Field(
        min_length=5,
        max_length=4000,
    )

    domain: Literal[
        "fleet",
        "marine_operations",
        "technical",
        "crew",
        "finance",
        "supply_chain",
        "inventory",
        "commercial",
        "hsse",
        "compliance",
        "legal",
        "audit",
        "esg",
        "ai_intelligence",
        "quantum_optimization",
        "digital_twin",
        "enterprise",
    ]

    priority: Literal[
        "low",
        "medium",
        "high",
        "critical",
    ] = "medium"

    vessel_id: Optional[str] = Field(
        default=None,
        max_length=150,
    )

    reference_id: Optional[str] = Field(
        default=None,
        max_length=150,
    )

    assigned_to: Optional[str] = Field(
        default=None,
        max_length=150,
    )


# ============================================================
# COMMAND EVENT STATUS MODEL
# ============================================================

class CommandEventStatusUpdate(CommandBaseModel):

    command_status: Literal[
        "open",
        "in_progress",
        "resolved",
        "cancelled",
    ]

    resolution_note: str = Field(
        min_length=5,
        max_length=2000,
    )


# ============================================================
# COMMAND PRIORITY UPDATE MODEL
# ============================================================

class CommandPriorityUpdate(CommandBaseModel):

    priority: Literal[
        "low",
        "medium",
        "high",
        "critical",
    ]

    reason: str = Field(
        min_length=5,
        max_length=1000,
    )


# ============================================================
# COMMAND EXECUTIVE NOTE MODEL
# ============================================================

class CommandExecutiveNoteCreate(CommandBaseModel):

    title: str = Field(
        min_length=5,
        max_length=200,
    )

    content: str = Field(
        min_length=5,
        max_length=5000,
    )

    category: Literal[
        "strategic",
        "operational",
        "financial",
        "safety",
        "performance",
        "risk",
    ] = "operational"


# ============================================================
# COMMON UTILITIES
# ============================================================

def command_now():
    return datetime.now(timezone.utc)


def command_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


def command_serialize_row(row):
    result = {}

    for key, value in dict(row).items():

        if isinstance(value, UUID):
            result[key] = str(value)

        elif isinstance(value, datetime):
            result[key] = value.isoformat()

        else:
            result[key] = value

    return result


# ============================================================
# AUTHENTICATION HELPERS
# ============================================================

def command_identity_value(identity, *names):

    for name in names:

        if isinstance(identity, dict):
            value = identity.get(name)
        else:
            value = getattr(identity, name, None)

        if value is not None and str(value).strip():
            return str(value).strip()

    return ""


def command_get_tenant(identity):

    tenant_id = command_identity_value(
        identity,
        "tenant_id",
        "tenant",
    )

    if not tenant_id:
        raise HTTPException(
            status_code=403,
            detail="Tenant identity is required.",
        )

    return tenant_id


def command_get_actor(identity):

    actor_id = command_identity_value(
        identity,
        "user_id",
        "id",
        "sub",
        "username",
        "email",
    )

    if not actor_id:
        raise HTTPException(
            status_code=403,
            detail="Authenticated user identity required.",
        )

    return actor_id


def command_claim(identity, *names):
    """Read a claim from dict/Pydantic/object identities safely."""
    for name in names:
        if isinstance(identity, dict):
            value = identity.get(name)
        else:
            value = getattr(identity, name, None)
        if isinstance(value, Enum):
            value = value.value
        if value is not None and value != "":
            return value
    return None


def command_normalize_role(value):
    if value is None:
        return ""
    # Accommodate common role formats without granting new privileges.
    return str(value).strip().lower().replace(" ", "_").replace("-", "_")


def command_get_roles(identity):
    """Collect recognized role claims and permission claims."""
    values = []
    for name in ("role", "user_role", "roles", "groups"):
        claim = command_claim(identity, name)
        if isinstance(claim, str):
            values.extend(part for part in claim.replace(",", " ").split() if part)
        elif isinstance(claim, (list, tuple, set)):
            values.extend(claim)

    roles = {command_normalize_role(value) for value in values}
    roles.discard("")
    return roles


def command_get_permissions(identity):
    values = []
    for name in ("permissions", "permission", "scope", "scopes"):
        claim = command_claim(identity, name)
        if isinstance(claim, str):
            values.extend(part for part in claim.replace(",", " ").split() if part)
        elif isinstance(claim, (list, tuple, set)):
            values.extend(claim)
    return {str(value).strip().lower() for value in values if str(value).strip()}


def command_get_role(identity):
    roles = command_get_roles(identity)
    for role in sorted(roles):
        if role in COMMAND_EXECUTIVE_ROLES:
            return role
    return next(iter(sorted(roles)), "")


def command_require_executive(identity):
    roles = command_get_roles(identity)
    permissions = command_get_permissions(identity)
    role_allowed = bool(roles.intersection(COMMAND_EXECUTIVE_ROLES))
    permission_allowed = bool(permissions.intersection({
        "executive_command_center.read",
        "executive:read",
        "command_center:read",
        "command_center.read",
    }))

    if not (role_allowed or permission_allowed):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "EXECUTIVE_ACCESS_REQUIRED",
                "message": "Executive Command Center access required.",
            },
        )


def command_require_admin(identity):
    roles = command_get_roles(identity)
    if not roles.intersection(COMMAND_ADMIN_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Command Center administrator required.",
        )


# ============================================================
# DATABASE AUDIT TRAIL
# ============================================================

def command_record_audit( conn, identity, entity_type, entity_id, action, details=None, ):

    conn.execute(
        text(""" INSERT INTO quantum_command_audit ( id, tenant_id, entity_type, entity_id, action, details_json, actor_id, created_at ) VALUES ( :id, :tenant_id, :entity_type, :entity_id, :action, :details_json, :actor_id, :created_at ) """),
        {
            "id": str(uuid4()),
            "tenant_id": command_get_tenant(identity),
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "action": action,
            "details_json": command_json(details or {}),
            "actor_id": command_get_actor(identity),
            "created_at": command_now(),
        },
    )


# ============================================================
# POSTGRESQL DATABASE INITIALIZATION
# ============================================================

def init_quantum_command_tables():

    statements = [

        # ----------------------------------------------------
        # ENTERPRISE COMMAND EVENTS
        # ----------------------------------------------------

        """ CREATE TABLE IF NOT EXISTS quantum_command_events ( id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, title VARCHAR(200) NOT NULL, description TEXT NOT NULL, domain VARCHAR(100) NOT NULL, priority VARCHAR(30) NOT NULL, command_status VARCHAR(30) NOT NULL DEFAULT 'open', vessel_id TEXT, reference_id TEXT, assigned_to TEXT, resolution_note TEXT, created_by TEXT NOT NULL, updated_by TEXT, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL, resolved_at TIMESTAMPTZ, UNIQUE (tenant_id, id) ) """,

        # ----------------------------------------------------
        # EXECUTIVE STRATEGIC NOTES
        # ----------------------------------------------------

        """ CREATE TABLE IF NOT EXISTS quantum_command_notes ( id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, title VARCHAR(200) NOT NULL, content TEXT NOT NULL, category VARCHAR(50) NOT NULL, created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL, UNIQUE (tenant_id, id) ) """,

        # ----------------------------------------------------
        # COMMAND SNAPSHOTS
        # ----------------------------------------------------

        """ CREATE TABLE IF NOT EXISTS quantum_command_snapshots ( id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, snapshot_type VARCHAR(100) NOT NULL, snapshot_data TEXT NOT NULL, generated_by TEXT NOT NULL, generated_at TIMESTAMPTZ NOT NULL, UNIQUE (tenant_id, id) ) """,

        # ----------------------------------------------------
        # COMMAND AUDIT TRAIL
        # ----------------------------------------------------

        """ CREATE TABLE IF NOT EXISTS quantum_command_audit ( id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, entity_type VARCHAR(100) NOT NULL, entity_id TEXT NOT NULL, action VARCHAR(100) NOT NULL, details_json TEXT NOT NULL DEFAULT '{}', actor_id TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL ) """,

        # ----------------------------------------------------
        # PERFORMANCE INDEXES
        # ----------------------------------------------------

        """ CREATE INDEX IF NOT EXISTS idx_quantum_command_events ON quantum_command_events ( tenant_id, command_status, priority ) """,

        """ CREATE INDEX IF NOT EXISTS idx_quantum_command_domain ON quantum_command_events ( tenant_id, domain, created_at DESC ) """,

        """ CREATE INDEX IF NOT EXISTS idx_quantum_command_vessel ON quantum_command_events ( tenant_id, vessel_id ) """,

        """ CREATE INDEX IF NOT EXISTS idx_quantum_command_notes ON quantum_command_notes ( tenant_id, created_at DESC ) """,

        """ CREATE INDEX IF NOT EXISTS idx_quantum_command_snapshots ON quantum_command_snapshots ( tenant_id, generated_at DESC ) """,

        """ CREATE INDEX IF NOT EXISTS idx_quantum_command_audit ON quantum_command_audit ( tenant_id, created_at DESC ) """,
    ]

    with get_engine().begin() as conn:

        # Mencegah tabrakan DDL jika beberapa worker start bersamaan.
        conn.execute(text("SELECT pg_advisory_xact_lock(734100231)"))

        for statement in statements:
            conn.execute(text(statement))


# ============================================================
# END OF MARITIME QUANTUM COMMAND BLOK 1
# ============================================================

# ============================================================
# MQEIP - MARITIME QUANTUM COMMAND CENTER
# BLOK 2 - COMMAND EVENTS & OPERATIONAL PRIORITY CONTROL
# ============================================================


# ============================================================
# CREATE ENTERPRISE COMMAND EVENT
# ============================================================

@router.post( "/events", status_code=status.HTTP_201_CREATED, summary="Create Enterprise Command Event", )
def command_create_event( payload: CommandEventCreate, identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)

    tenant_id = command_get_tenant(identity)
    actor_id = command_get_actor(identity)

    event_id = uuid4()
    now = command_now()

    with get_engine().begin() as conn:

        conn.execute(
            text(""" INSERT INTO quantum_command_events ( id, tenant_id, title, description, domain, priority, command_status, vessel_id, reference_id, assigned_to, created_by, created_at, updated_at ) VALUES ( :id, :tenant_id, :title, :description, :domain, :priority, 'open', :vessel_id, :reference_id, :assigned_to, :created_by, :created_at, :updated_at ) """),
            {
                "id": str(event_id),
                "tenant_id": tenant_id,
                "title": payload.title,
                "description": payload.description,
                "domain": payload.domain,
                "priority": payload.priority,
                "vessel_id": payload.vessel_id,
                "reference_id": payload.reference_id,
                "assigned_to": payload.assigned_to,
                "created_by": actor_id,
                "created_at": now,
                "updated_at": now,
            },
        )

        command_record_audit(
            conn,
            identity,
            "command_event",
            event_id,
            "created",
            {
                "domain": payload.domain,
                "priority": payload.priority,
                "command_status": "open",
            },
        )

    return {
        "event_id": str(event_id),
        "title": payload.title,
        "domain": payload.domain,
        "priority": payload.priority,
        "command_status": "open",
        "created_at": now.isoformat(),
    }


# ============================================================
# LIST ENTERPRISE COMMAND EVENTS
# ============================================================

@router.get( "/events", summary="List Enterprise Command Events", )
def command_list_events( domain: Optional[str] = Query( default=None, max_length=100, ), priority: Optional[ Literal["low", "medium", "high", "critical"] ] = None, command_status: Optional[ Literal[ "open", "in_progress", "resolved", "cancelled", ] ] = None, vessel_id: Optional[str] = Query( default=None, max_length=150, ), limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0), identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)

    tenant_id = command_get_tenant(identity)

    if domain is not None and domain not in COMMAND_DOMAINS:
        raise HTTPException(
            status_code=422,
            detail="Invalid command domain.",
        )

    with get_engine().connect() as conn:

        rows = conn.execute(
            text(""" SELECT id, title, description, domain, priority, command_status, vessel_id, reference_id, assigned_to, resolution_note, created_by, updated_by, created_at, updated_at, resolved_at FROM quantum_command_events WHERE tenant_id = :tenant_id AND ( CAST(:domain AS TEXT) IS NULL OR domain = :domain ) AND ( CAST(:priority AS TEXT) IS NULL OR priority = :priority ) AND ( CAST(:command_status AS TEXT) IS NULL OR command_status = :command_status ) AND ( CAST(:vessel_id AS TEXT) IS NULL OR vessel_id = :vessel_id ) ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, created_at DESC, id DESC LIMIT :limit OFFSET :offset """),
            {
                "tenant_id": tenant_id,
                "domain": domain,
                "priority": priority,
                "command_status": command_status,
                "vessel_id": vessel_id,
                "limit": limit,
                "offset": offset,
            },
        ).mappings().all()

    return {
        "module": "Maritime Quantum Command Center",
        "count": len(rows),
        "limit": limit,
        "offset": offset,
        "events": [
            command_serialize_row(row)
            for row in rows
        ],
    }


# ============================================================
# GET COMMAND EVENT DETAILS
# ============================================================

@router.get( "/events/{event_id}", summary="Get Enterprise Command Event Details", )
def command_get_event( event_id: UUID, identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)

    with get_engine().connect() as conn:

        event = conn.execute(
            text(""" SELECT * FROM quantum_command_events WHERE id = :event_id AND tenant_id = :tenant_id """),
            {
                "event_id": str(event_id),
                "tenant_id": command_get_tenant(identity),
            },
        ).mappings().first()

    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Command event not found.",
        )

    return {
        "event": command_serialize_row(event),
    }


# ============================================================
# UPDATE COMMAND EVENT STATUS
# ============================================================

@router.patch( "/events/{event_id}/status", summary="Update Command Event Status", )
def command_update_event_status( event_id: UUID, payload: CommandEventStatusUpdate, identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)

    tenant_id = command_get_tenant(identity)
    actor_id = command_get_actor(identity)
    now = command_now()

    with get_engine().begin() as conn:

        event = conn.execute(
            text(""" SELECT id, title, command_status FROM quantum_command_events WHERE id = :event_id AND tenant_id = :tenant_id FOR UPDATE """),
            {
                "event_id": str(event_id),
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if event is None:
            raise HTTPException(
                status_code=404,
                detail="Command event not found.",
            )

        old_status = event["command_status"]
        new_status = payload.command_status

        if old_status == new_status:
            raise HTTPException(
                status_code=409,
                detail="Event already has this status.",
            )

        # Closed events cannot be reopened through
        # this endpoint.
        if old_status in {"resolved", "cancelled"}:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Closed command events cannot be "
                    "reopened through this endpoint."
                ),
            )

        allowed_transitions = {
            "open": {
                "in_progress",
                "resolved",
                "cancelled",
            },
            "in_progress": {
                "resolved",
                "cancelled",
            },
        }

        if new_status not in allowed_transitions.get(
            old_status,
            set(),
        ):
            raise HTTPException(
                status_code=409,
                detail="Invalid command status transition.",
            )

        resolved_at = (
            now
            if new_status == "resolved"
            else None
        )

        conn.execute(
            text(""" UPDATE quantum_command_events SET command_status = :command_status, resolution_note = :resolution_note, updated_by = :updated_by, updated_at = :updated_at, resolved_at = :resolved_at WHERE id = :event_id AND tenant_id = :tenant_id """),
            {
                "command_status": new_status,
                "resolution_note": payload.resolution_note,
                "updated_by": actor_id,
                "updated_at": now,
                "resolved_at": resolved_at,
                "event_id": str(event_id),
                "tenant_id": tenant_id,
            },
        )

        command_record_audit(
            conn,
            identity,
            "command_event",
            event_id,
            "status_updated",
            {
                "previous_status": old_status,
                "new_status": new_status,
            },
        )

    return {
        "event_id": str(event_id),
        "previous_status": old_status,
        "command_status": new_status,
        "updated_at": now.isoformat(),
        "resolved_at": (
            resolved_at.isoformat()
            if resolved_at
            else None
        ),
    }


# ============================================================
# UPDATE COMMAND EVENT PRIORITY
# ============================================================

@router.patch( "/events/{event_id}/priority", summary="Update Command Event Priority", )
def command_update_event_priority( event_id: UUID, payload: CommandPriorityUpdate, identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)

    tenant_id = command_get_tenant(identity)
    now = command_now()

    with get_engine().begin() as conn:

        event = conn.execute(
            text(""" SELECT id, priority, command_status FROM quantum_command_events WHERE id = :event_id AND tenant_id = :tenant_id FOR UPDATE """),
            {
                "event_id": str(event_id),
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if event is None:
            raise HTTPException(
                status_code=404,
                detail="Command event not found.",
            )

        if event["command_status"] in {
            "resolved",
            "cancelled",
        }:
            raise HTTPException(
                status_code=409,
                detail="Cannot reprioritize a closed event.",
            )

        if event["priority"] == payload.priority:
            raise HTTPException(
                status_code=409,
                detail="Event already has this priority.",
            )

        conn.execute(
            text(""" UPDATE quantum_command_events SET priority = :priority, updated_by = :updated_by, updated_at = :updated_at WHERE id = :event_id AND tenant_id = :tenant_id """),
            {
                "priority": payload.priority,
                "updated_by": command_get_actor(identity),
                "updated_at": now,
                "event_id": str(event_id),
                "tenant_id": tenant_id,
            },
        )

        command_record_audit(
            conn,
            identity,
            "command_event",
            event_id,
            "priority_updated",
            {
                "previous_priority": event["priority"],
                "new_priority": payload.priority,
                "reason": payload.reason,
            },
        )

    return {
        "event_id": str(event_id),
        "previous_priority": event["priority"],
        "priority": payload.priority,
        "updated_at": now.isoformat(),
    }


# ============================================================
# COMMAND OPERATIONAL PRIORITY BOARD
# ============================================================

@router.get( "/operations/priority-board", summary="Enterprise Operational Priority Board", )
def command_priority_board( limit: int = Query(default=50, ge=1, le=200), identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)

    tenant_id = command_get_tenant(identity)

    with get_engine().connect() as conn:

        rows = conn.execute(
            text(""" SELECT id, title, domain, priority, command_status, vessel_id, assigned_to, created_at, updated_at FROM quantum_command_events WHERE tenant_id = :tenant_id AND command_status IN ( 'open', 'in_progress' ) ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, created_at ASC, id ASC LIMIT :limit """),
            {
                "tenant_id": tenant_id,
                "limit": limit,
            },
        ).mappings().all()

    events = [
        command_serialize_row(row)
        for row in rows
    ]

    return {
        "module": "Operational Priority Board",
        "count": len(events),
        "critical_count": sum(
            1 for event in events
            if event["priority"] == "critical"
        ),
        "high_count": sum(
            1 for event in events
            if event["priority"] == "high"
        ),
        "events": events,
        "generated_at": command_now().isoformat(),
        "note": (
            "Counts reflect returned events only, "
            "subject to the requested limit."
        ),
    }


# ============================================================
# COMMAND EVENT STATISTICS
# ============================================================

@router.get( "/intelligence/events", summary="Enterprise Command Event Intelligence", )
def command_event_intelligence( identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)

    tenant_id = command_get_tenant(identity)

    with get_engine().connect() as conn:

        summary = conn.execute(
            text(""" SELECT COUNT(*) AS total_events, COUNT(*) FILTER ( WHERE command_status = 'open' ) AS open_events, COUNT(*) FILTER ( WHERE command_status = 'in_progress' ) AS in_progress_events, COUNT(*) FILTER ( WHERE command_status = 'resolved' ) AS resolved_events, COUNT(*) FILTER ( WHERE command_status = 'cancelled' ) AS cancelled_events, COUNT(*) FILTER ( WHERE priority = 'critical' AND command_status IN ( 'open', 'in_progress' ) ) AS active_critical_events, COUNT(*) FILTER ( WHERE priority = 'high' AND command_status IN ( 'open', 'in_progress' ) ) AS active_high_events FROM quantum_command_events WHERE tenant_id = :tenant_id """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().one()

        domains = conn.execute(
            text(""" SELECT domain, COUNT(*) AS total_events, COUNT(*) FILTER ( WHERE command_status IN ( 'open', 'in_progress' ) ) AS active_events FROM quantum_command_events WHERE tenant_id = :tenant_id GROUP BY domain ORDER BY active_events DESC, domain """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().all()

    return {
        "module": "Enterprise Command Event Intelligence",
        "summary": command_serialize_row(summary),
        "domain_distribution": [
            command_serialize_row(row)
            for row in domains
        ],
        "generated_at": command_now().isoformat(),
    }


# ============================================================
# END OF MARITIME QUANTUM COMMAND BLOK 2
# ============================================================

# ============================================================
# MQEIP - MARITIME QUANTUM COMMAND CENTER
# BLOK 3 - EXECUTIVE STRATEGIC INTELLIGENCE
# ============================================================


# ============================================================
# CREATE EXECUTIVE STRATEGIC NOTE
# ============================================================

@router.post( "/executive/notes", status_code=status.HTTP_201_CREATED, summary="Create Executive Strategic Note", )
def command_create_executive_note( payload: CommandExecutiveNoteCreate, identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)

    tenant_id = command_get_tenant(identity)
    actor_id = command_get_actor(identity)

    note_id = uuid4()
    now = command_now()

    with get_engine().begin() as conn:

        conn.execute(
            text(""" INSERT INTO quantum_command_notes ( id, tenant_id, title, content, category, created_by, created_at, updated_at ) VALUES ( :id, :tenant_id, :title, :content, :category, :created_by, :created_at, :updated_at ) """),
            {
                "id": str(note_id),
                "tenant_id": tenant_id,
                "title": payload.title,
                "content": payload.content,
                "category": payload.category,
                "created_by": actor_id,
                "created_at": now,
                "updated_at": now,
            },
        )

        command_record_audit(
            conn,
            identity,
            "executive_note",
            note_id,
            "created",
            {
                "category": payload.category,
            },
        )

    return {
        "note_id": str(note_id),
        "title": payload.title,
        "category": payload.category,
        "created_at": now.isoformat(),
    }


# ============================================================
# LIST EXECUTIVE STRATEGIC NOTES
# ============================================================

@router.get( "/executive/notes", summary="List Executive Strategic Notes", )
def command_list_executive_notes( category: Optional[ Literal[ "strategic", "operational", "financial", "safety", "performance", "risk", ] ] = None, limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0), identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)

    tenant_id = command_get_tenant(identity)

    with get_engine().connect() as conn:

        rows = conn.execute(
            text(""" SELECT id, title, content, category, created_by, created_at, updated_at FROM quantum_command_notes WHERE tenant_id = :tenant_id AND ( CAST(:category AS TEXT) IS NULL OR category = :category ) ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset """),
            {
                "tenant_id": tenant_id,
                "category": category,
                "limit": limit,
                "offset": offset,
            },
        ).mappings().all()

    return {
        "module": "Executive Strategic Notes",
        "count": len(rows),
        "limit": limit,
        "offset": offset,
        "notes": [
            command_serialize_row(row)
            for row in rows
        ],
    }


# ============================================================
# GET EXECUTIVE STRATEGIC NOTE
# ============================================================

@router.get( "/executive/notes/{note_id}", summary="Get Executive Strategic Note", )
def command_get_executive_note( note_id: UUID, identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)

    with get_engine().connect() as conn:

        note = conn.execute(
            text(""" SELECT id, title, content, category, created_by, created_at, updated_at FROM quantum_command_notes WHERE id = :note_id AND tenant_id = :tenant_id """),
            {
                "note_id": str(note_id),
                "tenant_id": command_get_tenant(identity),
            },
        ).mappings().first()

    if note is None:
        raise HTTPException(
            status_code=404,
            detail="Executive strategic note not found.",
        )

    return {
        "note": command_serialize_row(note),
    }


# ============================================================
# UPDATE EXECUTIVE STRATEGIC NOTE
# ============================================================

class CommandExecutiveNoteUpdate(CommandBaseModel):

    title: Optional[str] = Field(
        default=None,
        min_length=5,
        max_length=200,
    )

    content: Optional[str] = Field(
        default=None,
        min_length=5,
        max_length=5000,
    )

    category: Optional[
        Literal[
            "strategic",
            "operational",
            "financial",
            "safety",
            "performance",
            "risk",
        ]
    ] = None


@router.patch( "/executive/notes/{note_id}", summary="Update Executive Strategic Note", )
def command_update_executive_note( note_id: UUID, payload: CommandExecutiveNoteUpdate, identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)

    tenant_id = command_get_tenant(identity)
    updates = payload.model_dump(exclude_unset=True)

    if not updates:
        raise HTTPException(
            status_code=422,
            detail="At least one field must be provided.",
        )

    if any(value is None for value in updates.values()):
        raise HTTPException(
            status_code=422,
            detail="Updated fields cannot be null.",
        )

    now = command_now()

    with get_engine().begin() as conn:

        note = conn.execute(
            text(""" SELECT id, title, content, category FROM quantum_command_notes WHERE id = :note_id AND tenant_id = :tenant_id FOR UPDATE """),
            {
                "note_id": str(note_id),
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if note is None:
            raise HTTPException(
                status_code=404,
                detail="Executive strategic note not found.",
            )

        conn.execute(
            text(""" UPDATE quantum_command_notes SET title = :title, content = :content, category = :category, updated_at = :updated_at WHERE id = :note_id AND tenant_id = :tenant_id """),
            {
                "title": updates.get(
                    "title",
                    note["title"],
                ),
                "content": updates.get(
                    "content",
                    note["content"],
                ),
                "category": updates.get(
                    "category",
                    note["category"],
                ),
                "updated_at": now,
                "note_id": str(note_id),
                "tenant_id": tenant_id,
            },
        )

        command_record_audit(
            conn,
            identity,
            "executive_note",
            note_id,
            "updated",
            {
                "changed_fields": list(updates.keys()),
            },
        )

    return {
        "note_id": str(note_id),
        "updated_fields": list(updates.keys()),
        "updated_at": now.isoformat(),
    }


# ============================================================
# EXECUTIVE DECISION SUPPORT
# ============================================================

@router.get( "/executive/decision-support", summary="Enterprise Executive Decision Support", )
def command_executive_decision_support( identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)

    tenant_id = command_get_tenant(identity)

    with get_engine().connect() as conn:

        priority_summary = conn.execute(
            text(""" SELECT COUNT(*) FILTER ( WHERE priority = 'critical' ) AS critical_events, COUNT(*) FILTER ( WHERE priority = 'high' ) AS high_events, COUNT(*) FILTER ( WHERE priority = 'medium' ) AS medium_events, COUNT(*) FILTER ( WHERE priority = 'low' ) AS low_events FROM quantum_command_events WHERE tenant_id = :tenant_id AND command_status IN ( 'open', 'in_progress' ) """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().one()

        critical_events = conn.execute(
            text(""" SELECT id, title, domain, priority, command_status, vessel_id, assigned_to, created_at FROM quantum_command_events WHERE tenant_id = :tenant_id AND command_status IN ( 'open', 'in_progress' ) AND priority IN ('critical', 'high') ORDER BY CASE priority WHEN 'critical' THEN 1 ELSE 2 END, created_at ASC, id ASC LIMIT 25 """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().all()

    summary = command_serialize_row(priority_summary)

    recommendations = []

    if summary["critical_events"] > 0:
        recommendations.append({
            "priority": "critical",
            "action": (
                "Review critical command events "
                "and initiate immediate management response."
            ),
        })

    if summary["high_events"] > 0:
        recommendations.append({
            "priority": "high",
            "action": (
                "Assign responsible managers and verify "
                "mitigation plans for high-priority events."
            ),
        })

    if not recommendations:
        recommendations.append({
            "priority": "routine",
            "action": (
                "Continue routine operational monitoring "
                "and verify incoming reports."
            ),
        })

    return {
        "module": "Executive Decision Support",
        "active_event_summary": summary,
        "priority_events": [
            command_serialize_row(row)
            for row in critical_events
        ],
        "recommendations": recommendations,
        "generated_at": command_now().isoformat(),
        "analysis_method": "Deterministic priority rules",
        "note": (
            "Recommendations are generated from "
            "Command Center event records only. "
            "They do not replace operational judgment."
        ),
    }


# ============================================================
# EXECUTIVE RISK INTELLIGENCE
# ============================================================

@router.get( "/executive/risk-intelligence", summary="Enterprise Executive Risk Intelligence", )
def command_executive_risk_intelligence( identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)

    tenant_id = command_get_tenant(identity)

    with get_engine().connect() as conn:

        domain_risks = conn.execute(
            text(""" SELECT domain, COUNT(*) AS active_events, COUNT(*) FILTER ( WHERE priority = 'critical' ) AS critical_events, COUNT(*) FILTER ( WHERE priority = 'high' ) AS high_events, COUNT(*) FILTER ( WHERE assigned_to IS NULL OR BTRIM(assigned_to) = '' ) AS unassigned_events FROM quantum_command_events WHERE tenant_id = :tenant_id AND command_status IN ( 'open', 'in_progress' ) GROUP BY domain ORDER BY critical_events DESC, high_events DESC, active_events DESC, domain """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().all()

    risks = []

    for row in domain_risks:

        item = command_serialize_row(row)

        if item["critical_events"] > 0:
            item["risk_level"] = "critical"

        elif item["high_events"] > 0:
            item["risk_level"] = "high"

        elif item["active_events"] > 0:
            item["risk_level"] = "moderate"

        else:
            item["risk_level"] = "low"

        risks.append(item)

    return {
        "module": "Executive Risk Intelligence",
        "domain_count": len(risks),
        "domain_risks": risks,
        "generated_at": command_now().isoformat(),
        "assessment_method": (
            "Rule-based assessment of open "
            "and in-progress command events."
        ),
    }


# ============================================================
# EXECUTIVE PERFORMANCE DASHBOARD
# ============================================================

@router.get( "/executive/performance", summary="Executive Command Performance Dashboard", )
def command_executive_performance( identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)

    tenant_id = command_get_tenant(identity)

    with get_engine().connect() as conn:

        metrics = conn.execute(
            text(""" SELECT COUNT(*) AS total_events, COUNT(*) FILTER ( WHERE command_status = 'resolved' ) AS resolved_events, COUNT(*) FILTER ( WHERE command_status IN ( 'open', 'in_progress' ) ) AS active_events, COUNT(*) FILTER ( WHERE command_status = 'cancelled' ) AS cancelled_events, COUNT(*) FILTER ( WHERE command_status IN ( 'open', 'in_progress' ) AND ( assigned_to IS NULL OR BTRIM(assigned_to) = '' ) ) AS unassigned_active_events, AVG( EXTRACT( EPOCH FROM ( resolved_at - created_at ) ) / 3600.0 ) FILTER ( WHERE command_status = 'resolved' AND resolved_at IS NOT NULL ) AS average_resolution_hours FROM quantum_command_events WHERE tenant_id = :tenant_id """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().one()

    result = command_serialize_row(metrics)

    total_events = int(result["total_events"])
    resolved_events = int(result["resolved_events"])

    resolution_rate = (
        round(
            (resolved_events / total_events) * 100,
            2,
        )
        if total_events > 0
        else 0.0
    )

    average_hours = result["average_resolution_hours"]

    return {
        "module": "Executive Command Performance",
        "total_events": total_events,
        "resolved_events": resolved_events,
        "active_events": result["active_events"],
        "cancelled_events": result["cancelled_events"],
        "unassigned_active_events":
            result["unassigned_active_events"],
        "resolution_rate_percent": resolution_rate,
        "average_resolution_hours": (
            round(float(average_hours), 2)
            if average_hours is not None
            else None
        ),
        "generated_at": command_now().isoformat(),
        "note": (
            "Resolution rate uses all recorded events "
            "as the denominator, including cancelled "
            "and currently active events."
        ),
    }


# ============================================================
# END OF MARITIME QUANTUM COMMAND BLOK 3
# ============================================================

# ============================================================
# MQEIP - MARITIME QUANTUM COMMAND CENTER
# BLOK 4 - ENTERPRISE INTELLIGENCE & SNAPSHOTS
# ============================================================


# ============================================================
# SNAPSHOT INPUT MODEL
# ============================================================

class CommandSnapshotCreate(CommandBaseModel):

    snapshot_type: Literal[
        "enterprise",
        "fleet",
        "marine_operations",
        "technical",
        "crew",
        "finance",
        "supply_chain",
        "hsse",
        "executive",
    ] = "enterprise"

    snapshot_data: dict[str, Any] = Field(
        default_factory=dict,
    )


# ============================================================
# SNAPSHOT SIZE & STRUCTURE VALIDATION
# ============================================================

COMMAND_SNAPSHOT_MAX_BYTES = 65536


def command_validate_snapshot_data(data):

    try:
        encoded = json.dumps(
            data,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")

    except (TypeError, ValueError, OverflowError):
        raise HTTPException(
            status_code=422,
            detail="Invalid snapshot JSON data.",
        )

    if len(encoded) > COMMAND_SNAPSHOT_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Snapshot exceeds 64 KB limit.",
        )

    return encoded.decode("utf-8")


# ============================================================
# CREATE COMMAND INTELLIGENCE SNAPSHOT
# ============================================================

@router.post( "/snapshots", status_code=status.HTTP_201_CREATED, summary="Create Enterprise Intelligence Snapshot", )
def command_create_snapshot( payload: CommandSnapshotCreate, identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)

    tenant_id = command_get_tenant(identity)
    actor_id = command_get_actor(identity)
    snapshot_id = uuid4()
    now = command_now()
    snapshot_data = command_validate_snapshot_data(payload.snapshot_data)

    with get_engine().begin() as conn:
        conn.execute(
            text(""" INSERT INTO quantum_command_snapshots ( id, tenant_id, snapshot_type, snapshot_data, generated_by, generated_at ) VALUES ( :id, :tenant_id, :snapshot_type, :snapshot_data, :generated_by, :generated_at ) """),
            {
                "id": str(snapshot_id),
                "tenant_id": tenant_id,
                "snapshot_type": payload.snapshot_type,
                "snapshot_data": snapshot_data,
                "generated_by": actor_id,
                "generated_at": now,
            },
        )
        command_record_audit(
            conn, identity, "command_snapshot", snapshot_id, "created",
            {"snapshot_type": payload.snapshot_type},
        )

    return {
        "snapshot_id": str(snapshot_id),
        "snapshot_type": payload.snapshot_type,
        "generated_at": now.isoformat(),
        "status": "created",
    }


# ============================================================
# ENTERPRISE DATA STATUS & ERP INTEGRATION VERIFICATION
# ============================================================

COMMAND_ERP_MODULE_ALIASES = {
    "fleet": ("fleet",),
    "marine_operations": ("marine_operations", "marine-operations", "marine operations"),
    "technical": ("technical",),
    "crew": ("crew",),
    "finance": ("finance",),
    "supply_chain": ("supply_chain", "supply-chain", "supply chain"),
    "inventory": ("inventory",),
    "commercial": ("commercial",),
    "hsse": ("hsse",),
    "compliance": ("compliance",),
    "legal": ("legal",),
    "audit": ("audit",),
    "esg": ("esg",),
    "ai_intelligence": ("ai_intelligence", "ai-intelligence", "ai intelligence"),
    "quantum_optimization": ("quantum_optimization", "quantum-optimization", "quantum optimization"),
    "digital_twin": ("digital_twin", "digital-twin", "digital twin"),
    "business_intelligence": ("business_intelligence", "business-intelligence", "business intelligence"),
}

# Awalan nama tabel per modul ERP (disesuaikan dengan skema database MQEIP).
# Tabel cocok jika namanya sama persis atau diawali salah satu entri.
# Daftar tabel yang dicek selalu dilaporkan agar bisa diaudit.
COMMAND_ERP_TABLE_PREFIXES = {
    "fleet": ("vessels",),
    "marine_operations": ("marine_",),
    "technical": ("technical_", "pms_"),
    "crew": ("crew_",),
    "finance": ("finance_",),
    "supply_chain": ("supply_chain_", "purchase_requisition"),
    "inventory": ("inventory_",),
    "commercial": ("commercial_",),
    "hsse": ("hsse_",),
    "compliance": ("hsse_compliance",),
    "legal": ("legal_",),
    "audit": ("audit_",),
    "esg": ("esg_",),
    "ai_intelligence": ("ai_",),
    "quantum_optimization": ("quantum_optimization", "quantum_audit"),
    "digital_twin": ("digital_twin",),
    "business_intelligence": ("bi_",),
}

COMMAND_ERP_MAX_TABLES_PER_MODULE = 4
COMMAND_ERP_ROW_COUNT_CAP = 1000
_COMMAND_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


COMMAND_ERP_MODULE_PREFIXES = {
    "fleet": ("/api/v1/fleet",),
    "marine_operations": ("/api/v1/marine-operations",),
    "technical": ("/api/v1/technical", "/api/v1/pms"),
    "crew": ("/api/v1/crew",),
    "finance": ("/api/v1/finance",),
    "supply_chain": ("/api/v1/supply-chain",),
    "inventory": ("/api/v1/inventory",),
    "commercial": ("/api/v1/commercial",),
    "hsse": ("/api/v1/hsse",),
    "compliance": ("/api/v1/hsse/compliance",),
    "legal": ("/api/v1/legal",),
    "audit": ("/api/v1/audit",),
    "esg": ("/api/v1/esg",),
    "ai_intelligence": ("/api/v1/ai/",),
    "quantum_optimization": ("/api/v1/quantum/",),
    "digital_twin": ("/api/v1/digital-twin",),
    "business_intelligence": ("/api/v1/bi/",),
}

_COMMAND_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def command_collect_app_paths(app):
    """ Kumpulkan path API dari OpenAPI schema (sumber yang sama dengan Swagger /docs). Jika OpenAPI gagal/kosong, fallback ke app.routes. Mengembalikan dict {path: [methods]}. """
    paths = {}
    try:
        schema = app.openapi()
        for path, operations in (schema.get("paths") or {}).items():
            methods = [
                method.upper() for method in (operations or {})
                if str(method).lower() in _COMMAND_HTTP_METHODS
            ]
            if methods:
                paths[str(path)] = methods
    except Exception:
        logger.exception("Gagal membaca OpenAPI schema untuk deteksi modul ERP")

    if not paths:
        for route in getattr(app, "routes", []):
            path = str(getattr(route, "path", "") or "")
            methods = sorted(
                method for method in (getattr(route, "methods", None) or set())
                if method not in {"HEAD", "OPTIONS"}
            )
            if path and methods:
                paths[path] = methods
    return paths


def command_discover_erp_api_modules(app):
    """ Discover which ERP API modules are registered in the running FastAPI app. Membaca path dari OpenAPI schema (sama seperti Swagger) dan mencocokkan dengan prefix modul. Hanya memeriksa registrasi route; integrasi data diverifikasi terpisah oleh command_verify_erp_data_integration(). """
    paths = {
        path: methods
        for path, methods in command_collect_app_paths(app).items()
        if path.startswith("/api/v1/")
        and not path.startswith("/api/v1/quantum-command")
    }

    discovered = {}
    for module, prefixes in COMMAND_ERP_MODULE_PREFIXES.items():
        matches = sorted(path for path in paths if path.startswith(prefixes))
        discovered[module] = {
            "api_routes_detected": bool(matches),
            "route_count": len(matches),
            "sample_paths": matches[:3],
            "data_integration": (
                "not_verified" if matches else "routes_not_registered"
            ),
            "tables_checked": [],
        }

    return discovered


def command_verify_erp_data_integration(discovered, tenant_id):
    """ Verifikasi nyata (read-only) per modul: mencari tabel ber-kolom tenant_id yang cocok dengan modul, lalu menjalankan COUNT terbatas tenant. Status 'verified' hanya diberikan jika query tenant-scoped benar-benar berhasil dijalankan. Mengubah dict `discovered` di tempat. """
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT c.table_name "
                "FROM information_schema.columns c "
                "JOIN information_schema.tables t "
                " ON t.table_schema = c.table_schema "
                " AND t.table_name = c.table_name "
                "WHERE c.table_schema = current_schema() "
                " AND c.column_name = 'tenant_id' "
                " AND t.table_type = 'BASE TABLE'"
            )).all()
        tenant_tables = sorted(
            name for (name,) in rows
            if not name.startswith("quantum_command_")
            and _COMMAND_SAFE_IDENTIFIER.match(name)
        )
    except Exception:
        logger.exception("ERP table discovery failed")
        for item in discovered.values():
            if item["api_routes_detected"]:
                item["data_integration"] = "check_failed"
        return discovered

    for module, item in discovered.items():
        if not item["api_routes_detected"]:
            continue

        prefixes = COMMAND_ERP_TABLE_PREFIXES.get(module, (module + "_",))
        matched = [
            table for table in tenant_tables
            if any(table.startswith(prefix) for prefix in prefixes)
        ][:COMMAND_ERP_MAX_TABLES_PER_MODULE]

        if not matched:
            item["data_integration"] = "no_tenant_tables_detected"
            continue

        checked = []
        try:
            with get_engine().connect() as conn:
                for table in matched:
                    try:
                        conn.execute(text("SET LOCAL statement_timeout = 3000"))
                        count = conn.execute(
                            text(
                                "SELECT COUNT(*) FROM ("
                                f'SELECT 1 FROM "{table}" '
                                "WHERE CAST(tenant_id AS TEXT) = :tenant_id "
                                "LIMIT :row_cap) AS limited_rows"
                            ),
                            {
                                "tenant_id": tenant_id,
                                "row_cap": COMMAND_ERP_ROW_COUNT_CAP,
                            },
                        ).scalar()
                        count = int(count or 0)
                        checked.append({
                            "table": table,
                            "tenant_rows": count,
                            "tenant_rows_capped": count >= COMMAND_ERP_ROW_COUNT_CAP,
                        })
                    except Exception:
                        logger.warning(
                            "ERP integration check failed for table %s", table,
                            exc_info=True,
                        )
                        conn.rollback()
        except Exception:
            logger.exception("ERP integration check failed for module %s", module)

        item["tables_checked"] = checked
        item["data_integration"] = "verified" if checked else "check_failed"

    return discovered


def command_summarize_erp_integration(discovered):
    with_routes = [
        name for name, item in discovered.items()
        if item["api_routes_detected"]
    ]
    verified = [
        name for name in with_routes
        if discovered[name]["data_integration"] == "verified"
    ]
    unverified = [name for name in with_routes if name not in verified]

    if with_routes and len(verified) == len(with_routes):
        overall = "verified"
    elif verified:
        overall = "partial"
    else:
        overall = "not_verified"

    return {
        "overall": overall,
        "total_modules": len(discovered),
        "routes_registered": len(with_routes),
        "verified": len(verified),
        "verified_modules": verified,
        "unverified_modules": unverified,
        "modules_without_routes": [
            name for name, item in discovered.items()
            if not item["api_routes_detected"]
        ],
    }


def command_build_erp_integration(app, tenant_id):
    modules = command_discover_erp_api_modules(app)
    command_verify_erp_data_integration(modules, tenant_id)
    return modules, command_summarize_erp_integration(modules)


@router.get( "/enterprise-data-status", summary="Enterprise Data Status and Command Module Connectivity", )
def command_enterprise_data_status( request: Request, identity: TokenIdentity = Depends(verify_access_token), ):
    """Report real connectivity and verified ERP data integration per module."""
    command_require_executive(identity)
    tenant_id = command_get_tenant(identity)

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            counts = conn.execute(text(""" SELECT (SELECT COUNT(*) FROM quantum_command_events WHERE tenant_id = :tenant_id) AS command_events, (SELECT COUNT(*) FROM quantum_command_notes WHERE tenant_id = :tenant_id) AS executive_notes, (SELECT COUNT(*) FROM quantum_command_snapshots WHERE tenant_id = :tenant_id) AS snapshots """), {"tenant_id": tenant_id}).mappings().one()
    except Exception:
        logger.exception("Enterprise data status check failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ENTERPRISE_DATA_UNAVAILABLE",
                "message": "Enterprise command data is temporarily unavailable.",
            },
        )

    modules, summary = command_build_erp_integration(request.app, tenant_id)

    return {
        "status": "success",
        "module": "Maritime Quantum Command Center",
        "api_connection": "online",
        "erp_module_integration": summary["overall"],
        "erp_integration_summary": summary,
        "erp_api_modules": modules,
        "integration_note": (
            "Status 'verified' berarti route modul terdaftar dan query "
            "read-only terbatas tenant pada tabel modul tersebut berhasil. "
            "Pencocokan tabel berdasarkan nama tabel; daftar tabel yang dicek "
            "tercantum di tables_checked setiap modul."
        ),
        "tenant_id": tenant_id,
        "data": {
            "command_events": int(counts["command_events"]),
            "executive_notes": int(counts["executive_notes"]),
            "snapshots": int(counts["snapshots"]),
        },
        "checked_at": command_now().isoformat(),
    }


# ============================================================
# SNAPSHOTS: GENERATE FROM LIVE DATA, LIST, DETAIL
# ============================================================

def command_parse_snapshot_row(row):
    result = command_serialize_row(row)
    raw = result.get("snapshot_data")
    if isinstance(raw, str):
        try:
            result["snapshot_data"] = json.loads(raw)
        except ValueError:
            pass
    return result


@router.post( "/snapshots/generate", status_code=status.HTTP_201_CREATED, summary="Generate Enterprise Snapshot From Live Command Data", )
def command_generate_snapshot( request: Request, identity: TokenIdentity = Depends(verify_access_token), ):
    """Buat snapshot dari data nyata Command Center (tanpa input manual)."""
    command_require_executive(identity)

    tenant_id = command_get_tenant(identity)
    actor_id = command_get_actor(identity)

    with get_engine().connect() as conn:
        event_summary = conn.execute(
            text(""" SELECT COUNT(*) AS total_events, COUNT(*) FILTER ( WHERE command_status = 'open' ) AS open_events, COUNT(*) FILTER ( WHERE command_status = 'in_progress' ) AS in_progress_events, COUNT(*) FILTER ( WHERE command_status = 'resolved' ) AS resolved_events, COUNT(*) FILTER ( WHERE command_status = 'cancelled' ) AS cancelled_events, COUNT(*) FILTER ( WHERE priority = 'critical' AND command_status IN ( 'open', 'in_progress' ) ) AS active_critical_events, COUNT(*) FILTER ( WHERE priority = 'high' AND command_status IN ( 'open', 'in_progress' ) ) AS active_high_events FROM quantum_command_events WHERE tenant_id = :tenant_id """),
            {"tenant_id": tenant_id},
        ).mappings().one()
        notes_count = conn.execute(
            text("SELECT COUNT(*) FROM quantum_command_notes WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        ).scalar()

    modules, summary = command_build_erp_integration(request.app, tenant_id)

    snapshot_payload = {
        "event_summary": command_serialize_row(event_summary),
        "executive_notes": int(notes_count or 0),
        "erp_integration": {
            "overall": summary["overall"],
            "verified_modules": summary["verified_modules"],
            "unverified_modules": summary["unverified_modules"],
            "modules_without_routes": summary["modules_without_routes"],
        },
        "source": "live_command_data",
    }
    snapshot_data = command_validate_snapshot_data(snapshot_payload)

    snapshot_id = uuid4()
    now = command_now()

    with get_engine().begin() as conn:
        conn.execute(
            text(""" INSERT INTO quantum_command_snapshots ( id, tenant_id, snapshot_type, snapshot_data, generated_by, generated_at ) VALUES ( :id, :tenant_id, :snapshot_type, :snapshot_data, :generated_by, :generated_at ) """),
            {
                "id": str(snapshot_id),
                "tenant_id": tenant_id,
                "snapshot_type": "enterprise",
                "snapshot_data": snapshot_data,
                "generated_by": actor_id,
                "generated_at": now,
            },
        )
        command_record_audit(
            conn, identity, "command_snapshot", snapshot_id, "generated",
            {"snapshot_type": "enterprise", "source": "live_command_data"},
        )

    return {
        "snapshot_id": str(snapshot_id),
        "snapshot_type": "enterprise",
        "generated_at": now.isoformat(),
        "status": "created",
        "snapshot_data": snapshot_payload,
    }


@router.get( "/snapshots", summary="List Enterprise Intelligence Snapshots", )
def command_list_snapshots( snapshot_type: Optional[ Literal[ "enterprise", "fleet", "marine_operations", "technical", "crew", "finance", "supply_chain", "hsse", "executive", ] ] = None, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0), identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)
    tenant_id = command_get_tenant(identity)

    with get_engine().connect() as conn:
        rows = conn.execute(
            text(""" SELECT id, snapshot_type, snapshot_data, generated_by, generated_at FROM quantum_command_snapshots WHERE tenant_id = :tenant_id AND ( CAST(:snapshot_type AS TEXT) IS NULL OR snapshot_type = :snapshot_type ) ORDER BY generated_at DESC, id DESC LIMIT :limit OFFSET :offset """),
            {
                "tenant_id": tenant_id,
                "snapshot_type": snapshot_type,
                "limit": limit,
                "offset": offset,
            },
        ).mappings().all()

    return {
        "module": "Maritime Quantum Command Center",
        "count": len(rows),
        "limit": limit,
        "offset": offset,
        "snapshots": [command_parse_snapshot_row(row) for row in rows],
    }


@router.get( "/snapshots/{snapshot_id}", summary="Get Enterprise Intelligence Snapshot", )
def command_get_snapshot( snapshot_id: UUID, identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_executive(identity)

    with get_engine().connect() as conn:
        row = conn.execute(
            text(""" SELECT id, snapshot_type, snapshot_data, generated_by, generated_at FROM quantum_command_snapshots WHERE id = :snapshot_id AND tenant_id = :tenant_id """),
            {
                "snapshot_id": str(snapshot_id),
                "tenant_id": command_get_tenant(identity),
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Snapshot not found.",
        )

    return {"snapshot": command_parse_snapshot_row(row)}


# ============================================================
# TEMPORARY ADMIN DIAGNOSTIC: DATABASE TABLE NAMES
# Hanya nama tabel (tanpa isi data). Khusus platform/tenant admin.
# Boleh dihapus setelah kata kunci integrasi ERP selesai disesuaikan.
# ============================================================

@router.get( "/admin/tables", summary="Admin: List Database Table Names (Diagnostic)", )
def command_admin_list_tables( identity: TokenIdentity = Depends(verify_access_token), ):
    command_require_admin(identity)

    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT t.table_name, "
                " EXISTS ( "
                " SELECT 1 FROM information_schema.columns c "
                " WHERE c.table_schema = t.table_schema "
                " AND c.table_name = t.table_name "
                " AND c.column_name = 'tenant_id' "
                " ) AS has_tenant_id "
                "FROM information_schema.tables t "
                "WHERE t.table_schema = current_schema() "
                " AND t.table_type = 'BASE TABLE' "
                "ORDER BY t.table_name"
            )).all()
    except Exception:
        logger.exception("Admin table listing failed")
        raise HTTPException(
            status_code=503,
            detail="Database table listing is temporarily unavailable.",
        )

    tables = [
        {"table": name, "has_tenant_id": bool(has_tenant)}
        for name, has_tenant in rows
    ]

    return {
        "table_count": len(tables),
        "tables_with_tenant_id": [
            item["table"] for item in tables if item["has_tenant_id"]
        ],
        "tables_without_tenant_id": [
            item["table"] for item in tables if not item["has_tenant_id"]
        ],
        "generated_at": command_now().isoformat(),
    }

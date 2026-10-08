# ============================================================
# MQEIP - ENTERPRISE IDENTITY & ACCESS MANAGEMENT
# BLOK 1 - SECURITY FOUNDATION & DATABASE
# ============================================================

import json
import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
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


router = APIRouter(
    prefix="/api/v1/iam",
    tags=["Enterprise Administration & IAM"],
)


# ============================================================
# SECURITY CONSTANTS
# ============================================================

IAM_ADMIN_ROLES = {
    "admin",
    "administrator",
    "superadmin",
    "tenant_admin",
}

IAM_USER_STATUSES = {
    "invited",
    "active",
    "suspended",
    "disabled",
}

IAM_ROLE_STATUSES = {
    "active",
    "disabled",
}

IAM_PERMISSION_ACTIONS = {
    "read",
    "create",
    "update",
    "delete",
    "approve",
    "export",
    "manage",
}

IAM_MAX_PAGE_SIZE = 200


# ============================================================
# BASE VALIDATION MODEL
# ============================================================

class IAMBaseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )


# ============================================================
# USER REGISTRATION MODEL
# ============================================================

class IAMUserCreate(IAMBaseModel):
    username: str = Field(
        min_length=3,
        max_length=100,
    )

    full_name: str = Field(
        min_length=2,
        max_length=200,
    )

    email: str = Field(
        min_length=5,
        max_length=254,
    )

    department: Optional[str] = Field(
        default=None,
        max_length=150,
    )

    job_title: Optional[str] = Field(
        default=None,
        max_length=150,
    )

    employee_reference: Optional[str] = Field(
        default=None,
        max_length=100,
    )

    @field_validator("username")
    @classmethod
    def validate_username(cls, value):
        if not re.fullmatch(
            r"[A-Za-z0-9._-]{3,100}",
            value,
        ):
            raise ValueError(
                "Username may contain letters, numbers, "
                "dots, underscores and hyphens only."
            )

        return value.lower()

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        value = value.lower()

        if (
            "@" not in value
            or value.startswith("@")
            or value.endswith("@")
            or any(char.isspace() for char in value)
        ):
            raise ValueError("Invalid email format.")

        return value


# ============================================================
# USER STATUS MODEL
# ============================================================

class IAMUserStatusUpdate(IAMBaseModel):
    user_status: Literal[
        "invited",
        "active",
        "suspended",
        "disabled",
    ]

    reason: str = Field(
        min_length=5,
        max_length=500,
    )


# ============================================================
# ROLE MANAGEMENT MODEL
# ============================================================

class IAMRoleCreate(IAMBaseModel):
    role_code: str = Field(
        min_length=3,
        max_length=100,
    )

    role_name: str = Field(
        min_length=3,
        max_length=150,
    )

    description: Optional[str] = Field(
        default=None,
        max_length=1000,
    )

    @field_validator("role_code")
    @classmethod
    def validate_role_code(cls, value):
        value = value.lower()

        if not re.fullmatch(
            r"[a-z][a-z0-9_]{2,99}",
            value,
        ):
            raise ValueError(
                "Role code must use lowercase letters, "
                "numbers and underscores."
            )

        return value


# ============================================================
# PERMISSION MODEL
# ============================================================

class IAMPermissionCreate(IAMBaseModel):
    resource_code: str = Field(
        min_length=2,
        max_length=100,
    )

    action: Literal[
        "read",
        "create",
        "update",
        "delete",
        "approve",
        "export",
        "manage",
    ]

    description: Optional[str] = Field(
        default=None,
        max_length=500,
    )

    @field_validator("resource_code")
    @classmethod
    def validate_resource_code(cls, value):
        value = value.lower()

        if not re.fullmatch(
            r"[a-z][a-z0-9_]{1,99}",
            value,
        ):
            raise ValueError(
                "Resource code must use lowercase letters, "
                "numbers and underscores."
            )

        return value


# ============================================================
# ROLE ASSIGNMENT MODEL
# ============================================================

class IAMRoleAssignmentCreate(IAMBaseModel):
    user_id: UUID
    role_id: UUID

    assignment_reason: str = Field(
        min_length=5,
        max_length=500,
    )


# ============================================================
# ROLE PERMISSION MODEL
# ============================================================

class IAMRolePermissionCreate(IAMBaseModel):
    role_id: UUID
    permission_id: UUID


# ============================================================
# COMMON HELPER FUNCTIONS
# ============================================================

def iam_now():
    return datetime.now(timezone.utc)


def iam_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


def iam_serialize_row(row):
    result = {}

    for key, value in dict(row).items():
        if isinstance(value, UUID):
            result[key] = str(value)

        elif isinstance(value, datetime):
            result[key] = value.isoformat()

        else:
            result[key] = value

    return result


def iam_require_admin(identity):
    role = str(
        getattr(identity, "role", "") or ""
    ).strip().lower()

    if role not in IAM_ADMIN_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Enterprise IAM administrator required.",
        )


def iam_get_tenant(identity):
    tenant_id = str(
        getattr(identity, "tenant_id", "") or ""
    ).strip()

    if not tenant_id:
        raise HTTPException(
            status_code=403,
            detail="Tenant identity is required.",
        )

    return tenant_id


def iam_get_actor(identity):
    actor_id = str(
        getattr(identity, "user_id", "") or ""
    ).strip()

    if not actor_id:
        raise HTTPException(
            status_code=403,
            detail="Authenticated user identity required.",
        )

    return actor_id


# ============================================================
# DATABASE AUDIT TRAIL
# ============================================================

def iam_record_audit(
    conn,
    identity,
    entity_type,
    entity_id,
    action,
    details=None,
):
    conn.execute(
        text("""
            INSERT INTO iam_audit_log (
                id,
                tenant_id,
                entity_type,
                entity_id,
                action,
                details_json,
                actor_id,
                created_at
            )
            VALUES (
                :id,
                :tenant_id,
                :entity_type,
                :entity_id,
                :action,
                :details_json,
                :actor_id,
                :created_at
            )
        """),
        {
            "id": uuid4(),
            "tenant_id": iam_get_tenant(identity),
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "action": action,
            "details_json": iam_json(details or {}),
            "actor_id": iam_get_actor(identity),
            "created_at": iam_now(),
        },
    )


# ============================================================
# POSTGRESQL DATABASE INITIALIZATION
# ============================================================

def init_iam_tables():

    statements = [

        """
        CREATE TABLE IF NOT EXISTS iam_users (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            username VARCHAR(100) NOT NULL,
            full_name VARCHAR(200) NOT NULL,
            email VARCHAR(254) NOT NULL,
            department VARCHAR(150),
            job_title VARCHAR(150),
            employee_reference VARCHAR(100),
            user_status VARCHAR(30)
                NOT NULL DEFAULT 'invited',
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            UNIQUE (tenant_id, username),
            UNIQUE (tenant_id, email),
            UNIQUE (tenant_id, id)
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS iam_roles (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            role_code VARCHAR(100) NOT NULL,
            role_name VARCHAR(150) NOT NULL,
            description TEXT,
            role_status VARCHAR(30)
                NOT NULL DEFAULT 'active',
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            UNIQUE (tenant_id, role_code),
            UNIQUE (tenant_id, id)
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS iam_permissions (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            resource_code VARCHAR(100) NOT NULL,
            action VARCHAR(30) NOT NULL,
            description TEXT,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            UNIQUE (tenant_id, resource_code, action),
            UNIQUE (tenant_id, id)
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS iam_user_roles (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id UUID NOT NULL,
            role_id UUID NOT NULL,
            assignment_reason TEXT NOT NULL,
            assigned_by TEXT NOT NULL,
            assigned_at TIMESTAMPTZ NOT NULL,
            UNIQUE (tenant_id, user_id, role_id),
            FOREIGN KEY (tenant_id, user_id)
                REFERENCES iam_users (tenant_id, id),
            FOREIGN KEY (tenant_id, role_id)
                REFERENCES iam_roles (tenant_id, id)
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS iam_role_permissions (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            role_id UUID NOT NULL,
            permission_id UUID NOT NULL,
            granted_by TEXT NOT NULL,
            granted_at TIMESTAMPTZ NOT NULL,
            UNIQUE (tenant_id, role_id, permission_id),
            FOREIGN KEY (tenant_id, role_id)
                REFERENCES iam_roles (tenant_id, id),
            FOREIGN KEY (tenant_id, permission_id)
                REFERENCES iam_permissions (tenant_id, id)
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS iam_audit_log (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            entity_type VARCHAR(100) NOT NULL,
            entity_id TEXT NOT NULL,
            action VARCHAR(100) NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            actor_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_iam_users_tenant
        ON iam_users (
            tenant_id,
            user_status
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_iam_roles_tenant
        ON iam_roles (
            tenant_id,
            role_status
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_iam_user_roles
        ON iam_user_roles (
            tenant_id,
            user_id
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_iam_role_permissions
        ON iam_role_permissions (
            tenant_id,
            role_id
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_iam_audit
        ON iam_audit_log (
            tenant_id,
            created_at DESC
        )
        """,
    ]

    with get_engine().begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


# ============================================================
# END OF ENTERPRISE IAM BLOK 1
# ============================================================

# ============================================================
# MQEIP - ENTERPRISE IAM
# BLOK 2 - USER ADMINISTRATION & LIFECYCLE MANAGEMENT
# ============================================================


# ============================================================
# USER PROFILE UPDATE MODEL
# ============================================================

class IAMUserProfileUpdate(IAMBaseModel):
    full_name: Optional[str] = Field(
        default=None,
        min_length=2,
        max_length=200,
    )

    department: Optional[str] = Field(
        default=None,
        max_length=150,
    )

    job_title: Optional[str] = Field(
        default=None,
        max_length=150,
    )

    employee_reference: Optional[str] = Field(
        default=None,
        max_length=100,
    )


# ============================================================
# CREATE ENTERPRISE USER RECORD
# ============================================================

@router.post(
    "/users",
    status_code=status.HTTP_201_CREATED,
    summary="Register Enterprise IAM User",
)
def iam_create_user(
    payload: IAMUserCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)
    actor_id = iam_get_actor(identity)
    user_id = uuid4()
    now = iam_now()

    with get_engine().begin() as conn:

        # Serialize creation within this tenant to prevent
        # duplicate-registration races.
        conn.execute(
            text("""
                SELECT pg_advisory_xact_lock(
                    hashtextextended(:tenant_id, 0)
                )
            """),
            {"tenant_id": tenant_id},
        )

        existing = conn.execute(
            text("""
                SELECT id
                FROM iam_users
                WHERE tenant_id = :tenant_id
                  AND (
                      username = :username
                      OR email = :email
                  )
                LIMIT 1
            """),
            {
                "tenant_id": tenant_id,
                "username": payload.username,
                "email": payload.email,
            },
        ).first()

        if existing:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Username or email already exists "
                    "within this company."
                ),
            )

        conn.execute(
            text("""
                INSERT INTO iam_users (
                    id,
                    tenant_id,
                    username,
                    full_name,
                    email,
                    department,
                    job_title,
                    employee_reference,
                    user_status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :username,
                    :full_name,
                    :email,
                    :department,
                    :job_title,
                    :employee_reference,
                    'invited',
                    :created_by,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "id": user_id,
                "tenant_id": tenant_id,
                "username": payload.username,
                "full_name": payload.full_name,
                "email": payload.email,
                "department": payload.department,
                "job_title": payload.job_title,
                "employee_reference":
                    payload.employee_reference,
                "created_by": actor_id,
                "created_at": now,
                "updated_at": now,
            },
        )

        iam_record_audit(
            conn,
            identity,
            "user",
            user_id,
            "created",
            {
                "username": payload.username,
                "user_status": "invited",
            },
        )

    return {
        "user_id": str(user_id),
        "username": payload.username,
        "full_name": payload.full_name,
        "email": payload.email,
        "user_status": "invited",
        "created_at": now.isoformat(),
        "note": (
            "IAM directory record created. "
            "No login credentials or invitation "
            "email have been issued."
        ),
    }


# ============================================================
# LIST ENTERPRISE USERS
# ============================================================

@router.get(
    "/users",
    summary="List Enterprise IAM Users",
)
def iam_list_users(
    user_status: Optional[
        Literal[
            "invited",
            "active",
            "suspended",
            "disabled",
        ]
    ] = None,
    department: Optional[str] = None,
    search: Optional[str] = Query(
        default=None,
        max_length=100,
    ),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)

    search_pattern = (
        f"%{search}%"
        if search
        else None
    )

    with get_engine().connect() as conn:

        rows = conn.execute(
            text("""
                SELECT
                    id,
                    username,
                    full_name,
                    email,
                    department,
                    job_title,
                    employee_reference,
                    user_status,
                    created_by,
                    created_at,
                    updated_at
                FROM iam_users
                WHERE tenant_id = :tenant_id
                  AND (
                      :user_status IS NULL
                      OR user_status = :user_status
                  )
                  AND (
                      :department IS NULL
                      OR department = :department
                  )
                  AND (
                      :search_pattern IS NULL
                      OR username ILIKE :search_pattern
                      OR full_name ILIKE :search_pattern
                      OR email ILIKE :search_pattern
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
                OFFSET :offset
            """),
            {
                "tenant_id": tenant_id,
                "user_status": user_status,
                "department": department,
                "search_pattern": search_pattern,
                "limit": limit,
                "offset": offset,
            },
        ).mappings().all()

    return {
        "module": "Enterprise IAM User Administration",
        "count": len(rows),
        "limit": limit,
        "offset": offset,
        "users": [
            iam_serialize_row(row)
            for row in rows
        ],
    }


# ============================================================
# GET USER DETAILS
# ============================================================

@router.get(
    "/users/{user_id}",
    summary="Get Enterprise IAM User Details",
)
def iam_get_user(
    user_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)

    with get_engine().connect() as conn:

        user = conn.execute(
            text("""
                SELECT
                    id,
                    username,
                    full_name,
                    email,
                    department,
                    job_title,
                    employee_reference,
                    user_status,
                    created_by,
                    created_at,
                    updated_at
                FROM iam_users
                WHERE id = :user_id
                  AND tenant_id = :tenant_id
            """),
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if user is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise IAM user not found.",
            )

        roles = conn.execute(
            text("""
                SELECT
                    r.id,
                    r.role_code,
                    r.role_name,
                    r.role_status,
                    ur.assigned_at
                FROM iam_user_roles ur
                JOIN iam_roles r
                  ON r.id = ur.role_id
                 AND r.tenant_id = ur.tenant_id
                WHERE ur.tenant_id = :tenant_id
                  AND ur.user_id = :user_id
                ORDER BY r.role_code
            """),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
            },
        ).mappings().all()

    return {
        "user": iam_serialize_row(user),
        "assigned_roles": [
            iam_serialize_row(row)
            for row in roles
        ],
        "note": (
            "IAM user status and roles are directory "
            "records; JWT authorization integration "
            "is not yet enforced."
        ),
    }


# ============================================================
# UPDATE USER PROFILE
# ============================================================

@router.patch(
    "/users/{user_id}/profile",
    summary="Update Enterprise IAM User Profile",
)
def iam_update_user_profile(
    user_id: UUID,
    payload: IAMUserProfileUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)
    now = iam_now()

    updates = payload.model_dump(
        exclude_unset=True
    )

    if not updates:
        raise HTTPException(
            status_code=422,
            detail="At least one profile field is required.",
        )

    with get_engine().begin() as conn:

        user = conn.execute(
            text("""
                SELECT
                    id,
                    username,
                    full_name,
                    department,
                    job_title,
                    employee_reference
                FROM iam_users
                WHERE id = :user_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if user is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise IAM user not found.",
            )

        # Use a fixed SQL statement with an allowlisted
        # set of fields. Never interpolate field names.
        conn.execute(
            text("""
                UPDATE iam_users
                SET full_name = :full_name,
                    department = :department,
                    job_title = :job_title,
                    employee_reference = :employee_reference,
                    updated_at = :updated_at
                WHERE id = :user_id
                  AND tenant_id = :tenant_id
            """),
            {
                "full_name": updates.get(
                    "full_name",
                    user["full_name"],
                ),
                "department": updates.get(
                    "department",
                    user["department"],
                ),
                "job_title": updates.get(
                    "job_title",
                    user["job_title"],
                ),
                "employee_reference": updates.get(
                    "employee_reference",
                    user["employee_reference"],
                ),
                "updated_at": now,
                "user_id": user_id,
                "tenant_id": tenant_id,
            },
        )

        iam_record_audit(
            conn,
            identity,
            "user",
            user_id,
            "profile_updated",
            {
                "changed_fields": list(updates.keys()),
            },
        )

    return {
        "user_id": str(user_id),
        "username": user["username"],
        "updated_fields": list(updates.keys()),
        "updated_at": now.isoformat(),
    }


# ============================================================
# CHANGE USER LIFECYCLE STATUS
# ============================================================

@router.patch(
    "/users/{user_id}/status",
    summary="Update Enterprise IAM User Status",
)
def iam_update_user_status(
    user_id: UUID,
    payload: IAMUserStatusUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)
    now = iam_now()

    with get_engine().begin() as conn:

        user = conn.execute(
            text("""
                SELECT
                    id,
                    username,
                    user_status
                FROM iam_users
                WHERE id = :user_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if user is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise IAM user not found.",
            )

        if user["user_status"] == payload.user_status:
            raise HTTPException(
                status_code=409,
                detail="User already has this status.",
            )

        conn.execute(
            text("""
                UPDATE iam_users
                SET user_status = :user_status,
                    updated_at = :updated_at
                WHERE id = :user_id
                  AND tenant_id = :tenant_id
            """),
            {
                "user_status": payload.user_status,
                "updated_at": now,
                "user_id": user_id,
                "tenant_id": tenant_id,
            },
        )

        iam_record_audit(
            conn,
            identity,
            "user",
            user_id,
            "status_updated",
            {
                "previous_status": user["user_status"],
                "new_status": payload.user_status,
                "reason": payload.reason,
            },
        )

    return {
        "user_id": str(user_id),
        "username": user["username"],
        "previous_status": user["user_status"],
        "user_status": payload.user_status,
        "updated_at": now.isoformat(),
        "security_note": (
            "Directory status updated. "
            "Existing JWT tokens are not revoked "
            "by this endpoint."
        ),
    }


# ============================================================
# ENTERPRISE USER STATISTICS
# ============================================================

@router.get(
    "/intelligence/users",
    summary="Enterprise IAM User Intelligence",
)
def iam_user_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)

    with get_engine().connect() as conn:

        status_rows = conn.execute(
            text("""
                SELECT
                    user_status,
                    COUNT(*) AS user_count
                FROM iam_users
                WHERE tenant_id = :tenant_id
                GROUP BY user_status
                ORDER BY user_status
            """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().all()

        department_rows = conn.execute(
            text("""
                SELECT
                    COALESCE(
                        NULLIF(department, ''),
                        'Unassigned'
                    ) AS department,
                    COUNT(*) AS user_count
                FROM iam_users
                WHERE tenant_id = :tenant_id
                GROUP BY
                    COALESCE(
                        NULLIF(department, ''),
                        'Unassigned'
                    )
                ORDER BY user_count DESC, department
            """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().all()

    statuses = [
        iam_serialize_row(row)
        for row in status_rows
    ]

    departments = [
        iam_serialize_row(row)
        for row in department_rows
    ]

    return {
        "module": "Enterprise IAM User Intelligence",
        "total_users": sum(
            row["user_count"]
            for row in statuses
        ),
        "users_by_status": statuses,
        "users_by_department": departments,
        "generated_at": iam_now().isoformat(),
    }


# ============================================================
# END OF ENTERPRISE IAM BLOK 2
# ============================================================

# ============================================================
# MQEIP - ENTERPRISE IAM
# BLOK 3 - ROLE-BASED ACCESS CONTROL (RBAC)
# ============================================================


# ============================================================
# ROLE STATUS UPDATE MODEL
# ============================================================

class IAMRoleStatusUpdate(IAMBaseModel):
    role_status: Literal[
        "active",
        "disabled",
    ]

    reason: str = Field(
        min_length=5,
        max_length=500,
    )


# ============================================================
# CREATE ENTERPRISE ROLE
# ============================================================

@router.post(
    "/roles",
    status_code=status.HTTP_201_CREATED,
    summary="Create Enterprise IAM Role",
)
def iam_create_role(
    payload: IAMRoleCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)
    actor_id = iam_get_actor(identity)
    role_id = uuid4()
    now = iam_now()

    with get_engine().begin() as conn:

        # Tenant-level transaction lock prevents duplicate
        # role creation under concurrent requests.
        conn.execute(
            text("""
                SELECT pg_advisory_xact_lock(
                    hashtextextended(:tenant_id, 1)
                )
            """),
            {"tenant_id": tenant_id},
        )

        existing = conn.execute(
            text("""
                SELECT id
                FROM iam_roles
                WHERE tenant_id = :tenant_id
                  AND role_code = :role_code
                LIMIT 1
            """),
            {
                "tenant_id": tenant_id,
                "role_code": payload.role_code,
            },
        ).first()

        if existing:
            raise HTTPException(
                status_code=409,
                detail="Role code already exists.",
            )

        conn.execute(
            text("""
                INSERT INTO iam_roles (
                    id,
                    tenant_id,
                    role_code,
                    role_name,
                    description,
                    role_status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :role_code,
                    :role_name,
                    :description,
                    'active',
                    :created_by,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "id": role_id,
                "tenant_id": tenant_id,
                "role_code": payload.role_code,
                "role_name": payload.role_name,
                "description": payload.description,
                "created_by": actor_id,
                "created_at": now,
                "updated_at": now,
            },
        )

        iam_record_audit(
            conn,
            identity,
            "role",
            role_id,
            "created",
            {
                "role_code": payload.role_code,
                "role_name": payload.role_name,
            },
        )

    return {
        "role_id": str(role_id),
        "role_code": payload.role_code,
        "role_name": payload.role_name,
        "role_status": "active",
        "created_at": now.isoformat(),
    }


# ============================================================
# LIST ENTERPRISE ROLES
# ============================================================

@router.get(
    "/roles",
    summary="List Enterprise IAM Roles",
)
def iam_list_roles(
    role_status: Optional[
        Literal["active", "disabled"]
    ] = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    with get_engine().connect() as conn:

        rows = conn.execute(
            text("""
                SELECT
                    r.id,
                    r.role_code,
                    r.role_name,
                    r.description,
                    r.role_status,
                    r.created_by,
                    r.created_at,
                    r.updated_at,
                    (
                        SELECT COUNT(*)
                        FROM iam_user_roles ur
                        WHERE ur.tenant_id = r.tenant_id
                          AND ur.role_id = r.id
                    ) AS assigned_user_count,
                    (
                        SELECT COUNT(*)
                        FROM iam_role_permissions rp
                        WHERE rp.tenant_id = r.tenant_id
                          AND rp.role_id = r.id
                    ) AS permission_count
                FROM iam_roles r
                WHERE r.tenant_id = :tenant_id
                  AND (
                      :role_status IS NULL
                      OR r.role_status = :role_status
                  )
                ORDER BY r.role_code
                LIMIT :limit
                OFFSET :offset
            """),
            {
                "tenant_id": iam_get_tenant(identity),
                "role_status": role_status,
                "limit": limit,
                "offset": offset,
            },
        ).mappings().all()

    return {
        "module": "Enterprise IAM Roles",
        "count": len(rows),
        "roles": [
            iam_serialize_row(row)
            for row in rows
        ],
    }


# ============================================================
# GET ROLE DETAILS
# ============================================================

@router.get(
    "/roles/{role_id}",
    summary="Get Enterprise IAM Role Details",
)
def iam_get_role(
    role_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)

    with get_engine().connect() as conn:

        role = conn.execute(
            text("""
                SELECT *
                FROM iam_roles
                WHERE id = :role_id
                  AND tenant_id = :tenant_id
            """),
            {
                "role_id": role_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if role is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise IAM role not found.",
            )

        permissions = conn.execute(
            text("""
                SELECT
                    p.id,
                    p.resource_code,
                    p.action,
                    p.description
                FROM iam_role_permissions rp
                JOIN iam_permissions p
                  ON p.id = rp.permission_id
                 AND p.tenant_id = rp.tenant_id
                WHERE rp.tenant_id = :tenant_id
                  AND rp.role_id = :role_id
                ORDER BY p.resource_code, p.action
            """),
            {
                "tenant_id": tenant_id,
                "role_id": role_id,
            },
        ).mappings().all()

    return {
        "role": iam_serialize_row(role),
        "permissions": [
            iam_serialize_row(row)
            for row in permissions
        ],
    }


# ============================================================
# UPDATE ROLE STATUS
# ============================================================

@router.patch(
    "/roles/{role_id}/status",
    summary="Update Enterprise IAM Role Status",
)
def iam_update_role_status(
    role_id: UUID,
    payload: IAMRoleStatusUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)
    now = iam_now()

    with get_engine().begin() as conn:

        role = conn.execute(
            text("""
                SELECT
                    id,
                    role_code,
                    role_status
                FROM iam_roles
                WHERE id = :role_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "role_id": role_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if role is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise IAM role not found.",
            )

        if role["role_status"] == payload.role_status:
            raise HTTPException(
                status_code=409,
                detail="Role already has this status.",
            )

        conn.execute(
            text("""
                UPDATE iam_roles
                SET role_status = :role_status,
                    updated_at = :updated_at
                WHERE id = :role_id
                  AND tenant_id = :tenant_id
            """),
            {
                "role_status": payload.role_status,
                "updated_at": now,
                "role_id": role_id,
                "tenant_id": tenant_id,
            },
        )

        iam_record_audit(
            conn,
            identity,
            "role",
            role_id,
            "status_updated",
            {
                "previous_status": role["role_status"],
                "new_status": payload.role_status,
                "reason": payload.reason,
            },
        )

    return {
        "role_id": str(role_id),
        "role_code": role["role_code"],
        "role_status": payload.role_status,
        "updated_at": now.isoformat(),
        "security_note": (
            "IAM directory role status updated. "
            "Authorization enforcement in existing "
            "MQEIP modules is not yet integrated."
        ),
    }


# ============================================================
# CREATE ENTERPRISE PERMISSION
# ============================================================

@router.post(
    "/permissions",
    status_code=status.HTTP_201_CREATED,
    summary="Create Enterprise IAM Permission",
)
def iam_create_permission(
    payload: IAMPermissionCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)
    permission_id = uuid4()
    now = iam_now()

    with get_engine().begin() as conn:

        conn.execute(
            text("""
                SELECT pg_advisory_xact_lock(
                    hashtextextended(:tenant_id, 2)
                )
            """),
            {"tenant_id": tenant_id},
        )

        existing = conn.execute(
            text("""
                SELECT id
                FROM iam_permissions
                WHERE tenant_id = :tenant_id
                  AND resource_code = :resource_code
                  AND action = :action
                LIMIT 1
            """),
            {
                "tenant_id": tenant_id,
                "resource_code": payload.resource_code,
                "action": payload.action,
            },
        ).first()

        if existing:
            raise HTTPException(
                status_code=409,
                detail="Permission already exists.",
            )

        conn.execute(
            text("""
                INSERT INTO iam_permissions (
                    id,
                    tenant_id,
                    resource_code,
                    action,
                    description,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :resource_code,
                    :action,
                    :description,
                    :created_by,
                    :created_at
                )
            """),
            {
                "id": permission_id,
                "tenant_id": tenant_id,
                "resource_code": payload.resource_code,
                "action": payload.action,
                "description": payload.description,
                "created_by": iam_get_actor(identity),
                "created_at": now,
            },
        )

        iam_record_audit(
            conn,
            identity,
            "permission",
            permission_id,
            "created",
            {
                "resource_code": payload.resource_code,
                "action": payload.action,
            },
        )

    return {
        "permission_id": str(permission_id),
        "resource_code": payload.resource_code,
        "action": payload.action,
        "created_at": now.isoformat(),
    }


# ============================================================
# LIST ENTERPRISE PERMISSIONS
# ============================================================

@router.get(
    "/permissions",
    summary="List Enterprise IAM Permissions",
)
def iam_list_permissions(
    resource_code: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    with get_engine().connect() as conn:

        rows = conn.execute(
            text("""
                SELECT
                    id,
                    resource_code,
                    action,
                    description,
                    created_by,
                    created_at
                FROM iam_permissions
                WHERE tenant_id = :tenant_id
                  AND (
                      :resource_code IS NULL
                      OR resource_code = :resource_code
                  )
                ORDER BY resource_code, action
                LIMIT :limit
                OFFSET :offset
            """),
            {
                "tenant_id": iam_get_tenant(identity),
                "resource_code": resource_code,
                "limit": limit,
                "offset": offset,
            },
        ).mappings().all()

    return {
        "module": "Enterprise IAM Permissions",
        "count": len(rows),
        "permissions": [
            iam_serialize_row(row)
            for row in rows
        ],
    }


# ============================================================
# GRANT PERMISSION TO ROLE
# ============================================================

@router.post(
    "/roles/permissions",
    status_code=status.HTTP_201_CREATED,
    summary="Grant IAM Permission to Role",
)
def iam_grant_role_permission(
    payload: IAMRolePermissionCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)
    assignment_id = uuid4()
    now = iam_now()

    with get_engine().begin() as conn:

        role = conn.execute(
            text("""
                SELECT id, role_code, role_status
                FROM iam_roles
                WHERE id = :role_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "role_id": payload.role_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if role is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise IAM role not found.",
            )

        if role["role_status"] != "active":
            raise HTTPException(
                status_code=409,
                detail="Role must be active.",
            )

        permission = conn.execute(
            text("""
                SELECT
                    id,
                    resource_code,
                    action
                FROM iam_permissions
                WHERE id = :permission_id
                  AND tenant_id = :tenant_id
            """),
            {
                "permission_id": payload.permission_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if permission is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise IAM permission not found.",
            )

        existing = conn.execute(
            text("""
                SELECT id
                FROM iam_role_permissions
                WHERE tenant_id = :tenant_id
                  AND role_id = :role_id
                  AND permission_id = :permission_id
            """),
            {
                "tenant_id": tenant_id,
                "role_id": payload.role_id,
                "permission_id": payload.permission_id,
            },
        ).first()

        if existing:
            raise HTTPException(
                status_code=409,
                detail="Permission already assigned to role.",
            )

        conn.execute(
            text("""
                INSERT INTO iam_role_permissions (
                    id,
                    tenant_id,
                    role_id,
                    permission_id,
                    granted_by,
                    granted_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :role_id,
                    :permission_id,
                    :granted_by,
                    :granted_at
                )
            """),
            {
                "id": assignment_id,
                "tenant_id": tenant_id,
                "role_id": payload.role_id,
                "permission_id": payload.permission_id,
                "granted_by": iam_get_actor(identity),
                "granted_at": now,
            },
        )

        iam_record_audit(
            conn,
            identity,
            "role_permission",
            assignment_id,
            "granted",
            {
                "role_id": str(payload.role_id),
                "permission_id": str(payload.permission_id),
                "resource_code": permission["resource_code"],
                "action": permission["action"],
            },
        )

    return {
        "assignment_id": str(assignment_id),
        "role_id": str(payload.role_id),
        "permission_id": str(payload.permission_id),
        "status": "granted",
        "granted_at": now.isoformat(),
        "note": (
            "Permission is stored in the IAM directory. "
            "It is not yet enforced by other API modules."
        ),
    }


# ============================================================
# REVOKE PERMISSION FROM ROLE
# ============================================================

@router.delete(
    "/roles/{role_id}/permissions/{permission_id}",
    summary="Revoke IAM Permission from Role",
)
def iam_revoke_role_permission(
    role_id: UUID,
    permission_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)

    with get_engine().begin() as conn:

        assignment = conn.execute(
            text("""
                SELECT id
                FROM iam_role_permissions
                WHERE tenant_id = :tenant_id
                  AND role_id = :role_id
                  AND permission_id = :permission_id
                FOR UPDATE
            """),
            {
                "tenant_id": tenant_id,
                "role_id": role_id,
                "permission_id": permission_id,
            },
        ).mappings().first()

        if assignment is None:
            raise HTTPException(
                status_code=404,
                detail="Role permission assignment not found.",
            )

        conn.execute(
            text("""
                DELETE FROM iam_role_permissions
                WHERE id = :assignment_id
                  AND tenant_id = :tenant_id
            """),
            {
                "assignment_id": assignment["id"],
                "tenant_id": tenant_id,
            },
        )

        iam_record_audit(
            conn,
            identity,
            "role_permission",
            assignment["id"],
            "revoked",
            {
                "role_id": str(role_id),
                "permission_id": str(permission_id),
            },
        )

    return {
        "role_id": str(role_id),
        "permission_id": str(permission_id),
        "status": "revoked",
        "note": (
            "IAM directory permission revoked. "
            "Existing JWT authorization is unchanged."
        ),
    }


# ============================================================
# END OF ENTERPRISE IAM BLOK 3
# ============================================================

# ============================================================
# MQEIP - ENTERPRISE IAM
# BLOK 4 - USER ROLE ASSIGNMENT & PERMISSION INTELLIGENCE
# ============================================================


# ============================================================
# ASSIGN ROLE TO ENTERPRISE USER
# ============================================================

@router.post(
    "/users/roles",
    status_code=status.HTTP_201_CREATED,
    summary="Assign Enterprise Role to User",
)
def iam_assign_user_role(
    payload: IAMRoleAssignmentCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)
    actor_id = iam_get_actor(identity)
    assignment_id = uuid4()
    now = iam_now()

    with get_engine().begin() as conn:

        user = conn.execute(
            text("""
                SELECT id, username, user_status
                FROM iam_users
                WHERE id = :user_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "user_id": payload.user_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if user is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise IAM user not found.",
            )

        if user["user_status"] in {
            "suspended",
            "disabled",
        }:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot assign roles to suspended "
                    "or disabled users."
                ),
            )

        role = conn.execute(
            text("""
                SELECT id, role_code, role_status
                FROM iam_roles
                WHERE id = :role_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "role_id": payload.role_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if role is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise IAM role not found.",
            )

        if role["role_status"] != "active":
            raise HTTPException(
                status_code=409,
                detail="Cannot assign a disabled role.",
            )

        existing = conn.execute(
            text("""
                SELECT id
                FROM iam_user_roles
                WHERE tenant_id = :tenant_id
                  AND user_id = :user_id
                  AND role_id = :role_id
            """),
            {
                "tenant_id": tenant_id,
                "user_id": payload.user_id,
                "role_id": payload.role_id,
            },
        ).first()

        if existing:
            raise HTTPException(
                status_code=409,
                detail="Role already assigned to user.",
            )

        conn.execute(
            text("""
                INSERT INTO iam_user_roles (
                    id,
                    tenant_id,
                    user_id,
                    role_id,
                    assignment_reason,
                    assigned_by,
                    assigned_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :user_id,
                    :role_id,
                    :assignment_reason,
                    :assigned_by,
                    :assigned_at
                )
            """),
            {
                "id": assignment_id,
                "tenant_id": tenant_id,
                "user_id": payload.user_id,
                "role_id": payload.role_id,
                "assignment_reason":
                    payload.assignment_reason,
                "assigned_by": actor_id,
                "assigned_at": now,
            },
        )

        iam_record_audit(
            conn,
            identity,
            "user_role",
            assignment_id,
            "assigned",
            {
                "user_id": str(payload.user_id),
                "role_id": str(payload.role_id),
                "role_code": role["role_code"],
                "reason": payload.assignment_reason,
            },
        )

    return {
        "assignment_id": str(assignment_id),
        "user_id": str(payload.user_id),
        "username": user["username"],
        "role_id": str(payload.role_id),
        "role_code": role["role_code"],
        "status": "assigned",
        "assigned_at": now.isoformat(),
        "note": (
            "Role assignment saved to the IAM directory. "
            "It does not modify existing JWT claims."
        ),
    }


# ============================================================
# LIST USER ROLE ASSIGNMENTS
# ============================================================

@router.get(
    "/users/{user_id}/roles",
    summary="List User Role Assignments",
)
def iam_list_user_roles(
    user_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)

    with get_engine().connect() as conn:

        user = conn.execute(
            text("""
                SELECT id, username, user_status
                FROM iam_users
                WHERE id = :user_id
                  AND tenant_id = :tenant_id
            """),
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if user is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise IAM user not found.",
            )

        rows = conn.execute(
            text("""
                SELECT
                    ur.id AS assignment_id,
                    r.id AS role_id,
                    r.role_code,
                    r.role_name,
                    r.role_status,
                    ur.assignment_reason,
                    ur.assigned_by,
                    ur.assigned_at
                FROM iam_user_roles ur
                JOIN iam_roles r
                  ON r.id = ur.role_id
                 AND r.tenant_id = ur.tenant_id
                WHERE ur.tenant_id = :tenant_id
                  AND ur.user_id = :user_id
                ORDER BY r.role_code
            """),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
            },
        ).mappings().all()

    return {
        "user_id": str(user_id),
        "username": user["username"],
        "user_status": user["user_status"],
        "role_count": len(rows),
        "roles": [
            iam_serialize_row(row)
            for row in rows
        ],
    }


# ============================================================
# REVOKE ROLE FROM USER
# ============================================================

@router.delete(
    "/users/{user_id}/roles/{role_id}",
    summary="Revoke Enterprise Role from User",
)
def iam_revoke_user_role(
    user_id: UUID,
    role_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)

    with get_engine().begin() as conn:

        assignment = conn.execute(
            text("""
                SELECT id
                FROM iam_user_roles
                WHERE tenant_id = :tenant_id
                  AND user_id = :user_id
                  AND role_id = :role_id
                FOR UPDATE
            """),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "role_id": role_id,
            },
        ).mappings().first()

        if assignment is None:
            raise HTTPException(
                status_code=404,
                detail="User role assignment not found.",
            )

        conn.execute(
            text("""
                DELETE FROM iam_user_roles
                WHERE id = :assignment_id
                  AND tenant_id = :tenant_id
            """),
            {
                "assignment_id": assignment["id"],
                "tenant_id": tenant_id,
            },
        )

        iam_record_audit(
            conn,
            identity,
            "user_role",
            assignment["id"],
            "revoked",
            {
                "user_id": str(user_id),
                "role_id": str(role_id),
            },
        )

    return {
        "user_id": str(user_id),
        "role_id": str(role_id),
        "status": "revoked",
        "security_note": (
            "Directory assignment revoked. "
            "Existing JWT tokens remain unchanged."
        ),
    }


# ============================================================
# EFFECTIVE USER PERMISSIONS
# ============================================================

@router.get(
    "/users/{user_id}/effective-permissions",
    summary="Calculate Effective IAM User Permissions",
)
def iam_get_effective_permissions(
    user_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)

    with get_engine().connect() as conn:

        user = conn.execute(
            text("""
                SELECT
                    id,
                    username,
                    user_status
                FROM iam_users
                WHERE id = :user_id
                  AND tenant_id = :tenant_id
            """),
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if user is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise IAM user not found.",
            )

        rows = conn.execute(
            text("""
                SELECT DISTINCT
                    p.resource_code,
                    p.action
                FROM iam_user_roles ur
                JOIN iam_roles r
                  ON r.id = ur.role_id
                 AND r.tenant_id = ur.tenant_id
                JOIN iam_role_permissions rp
                  ON rp.role_id = r.id
                 AND rp.tenant_id = r.tenant_id
                JOIN iam_permissions p
                  ON p.id = rp.permission_id
                 AND p.tenant_id = rp.tenant_id
                WHERE ur.tenant_id = :tenant_id
                  AND ur.user_id = :user_id
                  AND r.role_status = 'active'
                ORDER BY p.resource_code, p.action
            """),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
            },
        ).mappings().all()

    # Only active directory users receive an effective
    # permission set in this calculation.
    effective = (
        [iam_serialize_row(row) for row in rows]
        if user["user_status"] == "active"
        else []
    )

    return {
        "user_id": str(user_id),
        "username": user["username"],
        "user_status": user["user_status"],
        "effective_permission_count": len(effective),
        "effective_permissions": effective,
        "authorization_enforced": False,
        "note": (
            "This is a directory-based permission "
            "calculation. It does not authorize requests "
            "to other MQEIP modules."
        ),
    }


# ============================================================
# CHECK USER PERMISSION
# ============================================================

@router.get(
    "/users/{user_id}/access-check",
    summary="Check IAM Directory User Permission",
)
def iam_check_user_access(
    user_id: UUID,
    resource_code: str = Query(
        min_length=2,
        max_length=100,
    ),
    action: Literal[
        "read",
        "create",
        "update",
        "delete",
        "approve",
        "export",
        "manage",
    ] = Query(...),
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)

    with get_engine().connect() as conn:

        user = conn.execute(
            text("""
                SELECT id, username, user_status
                FROM iam_users
                WHERE id = :user_id
                  AND tenant_id = :tenant_id
            """),
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if user is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise IAM user not found.",
            )

        permission_exists = conn.execute(
            text("""
                SELECT EXISTS (
                    SELECT 1
                    FROM iam_user_roles ur
                    JOIN iam_roles r
                      ON r.id = ur.role_id
                     AND r.tenant_id = ur.tenant_id
                    JOIN iam_role_permissions rp
                      ON rp.role_id = r.id
                     AND rp.tenant_id = r.tenant_id
                    JOIN iam_permissions p
                      ON p.id = rp.permission_id
                     AND p.tenant_id = rp.tenant_id
                    WHERE ur.tenant_id = :tenant_id
                      AND ur.user_id = :user_id
                      AND r.role_status = 'active'
                      AND p.resource_code = :resource_code
                      AND p.action = :action
                )
            """),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "resource_code": resource_code.lower(),
                "action": action,
            },
        ).scalar_one()

    permitted = (
        user["user_status"] == "active"
        and bool(permission_exists)
    )

    return {
        "user_id": str(user_id),
        "username": user["username"],
        "resource_code": resource_code.lower(),
        "action": action,
        "directory_permission_present": permitted,
        "authorization_enforced": False,
        "note": (
            "Administrative directory check only. "
            "It is not an authorization decision "
            "for the calling user's JWT."
        ),
    }


# ============================================================
# ENTERPRISE ACCESS INTELLIGENCE
# ============================================================

@router.get(
    "/intelligence/access",
    summary="Enterprise IAM Access Intelligence",
)
def iam_access_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)

    with get_engine().connect() as conn:

        role_groups = conn.execute(
            text("""
                SELECT
                    r.role_code,
                    r.role_name,
                    r.role_status,
                    COUNT(DISTINCT ur.user_id)
                        AS assigned_user_count,
                    (
                        SELECT COUNT(*)
                        FROM iam_role_permissions rp
                        WHERE rp.tenant_id = r.tenant_id
                          AND rp.role_id = r.id
                    ) AS permission_count
                FROM iam_roles r
                LEFT JOIN iam_user_roles ur
                  ON ur.role_id = r.id
                 AND ur.tenant_id = r.tenant_id
                WHERE r.tenant_id = :tenant_id
                GROUP BY
                    r.id,
                    r.tenant_id,
                    r.role_code,
                    r.role_name,
                    r.role_status
                ORDER BY r.role_code
            """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().all()

        unassigned = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM iam_users u
                WHERE u.tenant_id = :tenant_id
                  AND NOT EXISTS (
                      SELECT 1
                      FROM iam_user_roles ur
                      WHERE ur.tenant_id = u.tenant_id
                        AND ur.user_id = u.id
                  )
            """),
            {
                "tenant_id": tenant_id,
            },
        ).scalar_one()

    roles = [
        iam_serialize_row(row)
        for row in role_groups
    ]

    return {
        "module": "Enterprise IAM Access Intelligence",
        "total_roles": len(roles),
        "users_without_assigned_roles": unassigned,
        "role_access_distribution": roles,
        "generated_at": iam_now().isoformat(),
        "authorization_enforced": False,
    }


# ============================================================
# END OF ENTERPRISE IAM BLOK 4
# ============================================================

# ============================================================
# MQEIP - ENTERPRISE IDENTITY & ACCESS MANAGEMENT
# BLOK 5 - SECURITY DASHBOARD, AUDIT & DATABASE INITIALIZATION
# ============================================================


# ============================================================
# ENTERPRISE SECURITY DASHBOARD
# ============================================================

@router.get(
    "/executive/dashboard",
    summary="Enterprise IAM Security Dashboard",
)
def iam_executive_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)

    with get_engine().connect() as conn:

        users = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_users,
                    COUNT(*) FILTER (
                        WHERE user_status = 'active'
                    ) AS active_users,
                    COUNT(*) FILTER (
                        WHERE user_status = 'invited'
                    ) AS invited_users,
                    COUNT(*) FILTER (
                        WHERE user_status = 'suspended'
                    ) AS suspended_users,
                    COUNT(*) FILTER (
                        WHERE user_status = 'disabled'
                    ) AS disabled_users
                FROM iam_users
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": tenant_id},
        ).mappings().one()

        roles = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_roles,
                    COUNT(*) FILTER (
                        WHERE role_status = 'active'
                    ) AS active_roles,
                    COUNT(*) FILTER (
                        WHERE role_status = 'disabled'
                    ) AS disabled_roles
                FROM iam_roles
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": tenant_id},
        ).mappings().one()

        permissions = conn.execute(
            text("""
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM iam_permissions
                        WHERE tenant_id = :tenant_id
                    ) AS total_permissions,
                    (
                        SELECT COUNT(*)
                        FROM iam_user_roles
                        WHERE tenant_id = :tenant_id
                    ) AS user_role_assignments,
                    (
                        SELECT COUNT(*)
                        FROM iam_role_permissions
                        WHERE tenant_id = :tenant_id
                    ) AS role_permission_assignments
            """),
            {"tenant_id": tenant_id},
        ).mappings().one()

    return {
        "platform": "MQEIP",
        "module": "Enterprise Administration & IAM",
        "users": iam_serialize_row(users),
        "roles": iam_serialize_row(roles),
        "permissions": iam_serialize_row(permissions),
        "generated_at": iam_now().isoformat(),
        "security_status": {
            "tenant_scoped_directory": True,
            "role_registry": True,
            "permission_registry": True,
            "directory_access_calculation": True,
            "audit_trail": True,
            "jwt_permission_enforcement": False,
            "automatic_token_revocation": False,
        },
        "note": (
            "This dashboard summarizes IAM directory data. "
            "It does not establish that directory permissions "
            "are enforced by other MQEIP modules."
        ),
    }


# ============================================================
# ENTERPRISE SECURITY RISK INTELLIGENCE
# ============================================================

@router.get(
    "/intelligence/security-risks",
    summary="Enterprise IAM Security Risk Intelligence",
)
def iam_security_risk_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)

    with get_engine().connect() as conn:

        risks = conn.execute(
            text("""
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM iam_users u
                        WHERE u.tenant_id = :tenant_id
                          AND u.user_status = 'active'
                          AND NOT EXISTS (
                              SELECT 1
                              FROM iam_user_roles ur
                              JOIN iam_roles r
                                ON r.id = ur.role_id
                               AND r.tenant_id = ur.tenant_id
                              WHERE ur.tenant_id = u.tenant_id
                                AND ur.user_id = u.id
                                AND r.role_status = 'active'
                          )
                    ) AS active_users_without_active_roles,

                    (
                        SELECT COUNT(*)
                        FROM iam_user_roles ur
                        JOIN iam_users u
                          ON u.id = ur.user_id
                         AND u.tenant_id = ur.tenant_id
                        WHERE ur.tenant_id = :tenant_id
                          AND u.user_status IN (
                              'suspended',
                              'disabled'
                          )
                    ) AS inactive_user_role_assignments,

                    (
                        SELECT COUNT(*)
                        FROM iam_roles r
                        WHERE r.tenant_id = :tenant_id
                          AND r.role_status = 'active'
                          AND NOT EXISTS (
                              SELECT 1
                              FROM iam_role_permissions rp
                              WHERE rp.tenant_id = r.tenant_id
                                AND rp.role_id = r.id
                          )
                    ) AS active_roles_without_permissions,

                    (
                        SELECT COUNT(*)
                        FROM iam_role_permissions rp
                        JOIN iam_roles r
                          ON r.id = rp.role_id
                         AND r.tenant_id = rp.tenant_id
                        WHERE rp.tenant_id = :tenant_id
                          AND r.role_status = 'disabled'
                    ) AS disabled_role_permission_assignments
            """),
            {"tenant_id": tenant_id},
        ).mappings().one()

    result = iam_serialize_row(risks)

    total_findings = sum(
        int(value)
        for value in result.values()
    )

    return {
        "module": "Enterprise IAM Security Risk Intelligence",
        "total_directory_findings": total_findings,
        "findings": result,
        "generated_at": iam_now().isoformat(),
        "assessment_scope": "IAM directory records only",
        "limitations": (
            "JWT claims, live sessions, MFA, login activity, "
            "and permissions enforced in other modules "
            "are not assessed by this report."
        ),
    }


# ============================================================
# ENTERPRISE IAM AUDIT TRAIL
# ============================================================

@router.get(
    "/audit",
    summary="Enterprise IAM Audit Trail",
)
def iam_list_audit(
    entity_type: Optional[str] = Query(
        default=None,
        max_length=100,
    ),
    entity_id: Optional[str] = Query(
        default=None,
        max_length=150,
    ),
    action: Optional[str] = Query(
        default=None,
        max_length=100,
    ),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)

    with get_engine().connect() as conn:

        rows = conn.execute(
            text("""
                SELECT
                    id,
                    entity_type,
                    entity_id,
                    action,
                    details_json,
                    actor_id,
                    created_at
                FROM iam_audit_log
                WHERE tenant_id = :tenant_id
                  AND (
                      :entity_type IS NULL
                      OR entity_type = :entity_type
                  )
                  AND (
                      :entity_id IS NULL
                      OR entity_id = :entity_id
                  )
                  AND (
                      :action IS NULL
                      OR action = :action
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
                OFFSET :offset
            """),
            {
                "tenant_id": tenant_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "action": action,
                "limit": limit,
                "offset": offset,
            },
        ).mappings().all()

    return {
        "module": "Enterprise IAM Audit Trail",
        "count": len(rows),
        "limit": limit,
        "offset": offset,
        "records": [
            iam_serialize_row(row)
            for row in rows
        ],
    }


# ============================================================
# ENTERPRISE IAM SYSTEM STATUS
# ============================================================

@router.get(
    "/system/status",
    summary="Enterprise IAM System Status",
)
def iam_system_status(
    identity: TokenIdentity = Depends(verify_access_token),
):
    iam_require_admin(identity)

    tenant_id = iam_get_tenant(identity)

    with get_engine().connect() as conn:

        counts = conn.execute(
            text("""
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM iam_users
                        WHERE tenant_id = :tenant_id
                    ) AS user_count,

                    (
                        SELECT COUNT(*)
                        FROM iam_roles
                        WHERE tenant_id = :tenant_id
                    ) AS role_count,

                    (
                        SELECT COUNT(*)
                        FROM iam_permissions
                        WHERE tenant_id = :tenant_id
                    ) AS permission_count,

                    (
                        SELECT COUNT(*)
                        FROM iam_user_roles
                        WHERE tenant_id = :tenant_id
                    ) AS user_role_count,

                    (
                        SELECT COUNT(*)
                        FROM iam_role_permissions
                        WHERE tenant_id = :tenant_id
                    ) AS role_permission_count,

                    (
                        SELECT COUNT(*)
                        FROM iam_audit_log
                        WHERE tenant_id = :tenant_id
                    ) AS audit_record_count
            """),
            {"tenant_id": tenant_id},
        ).mappings().one()

    return {
        "platform": "MQEIP",
        "module": "Enterprise Administration & IAM",
        "database_status": "connected",
        "directory": iam_serialize_row(counts),
        "capabilities": {
            "tenant_isolated_directory_queries": True,
            "user_administration": True,
            "role_management": True,
            "permission_management": True,
            "role_assignment": True,
            "effective_permission_reporting": True,
            "audit_logging": True,
            "jwt_authorization_integration": False,
            "login_account_provisioning": False,
            "token_revocation": False,
            "mfa": False,
        },
        "generated_at": iam_now().isoformat(),
    }


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

# Create IAM tables when this module is imported.
# A dedicated migration system is recommended before
# production use to manage schema changes safely.

init_iam_tables()


# ============================================================
# END OF ENTERPRISE IAM BLOK 5
# ============================================================

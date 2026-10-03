from enum import Enum
from typing import FrozenSet

from fastapi import HTTPException, status


class Role(str, Enum):
    PLATFORM_ADMIN = "platform_admin"
    COMPANY_ADMIN = "company_admin"
    DPA = "dpa"
    MARINE_SUPERINTENDENT = "marine_superintendent"
    TECHNICAL_SUPERINTENDENT = "technical_superintendent"
    HSSE = "hsse"
    FINANCE = "finance"
    PROCUREMENT = "procurement"
    CREWING = "crewing"
    MASTER = "master"
    CHIEF_ENGINEER = "chief_engineer"
    VIEWER = "viewer"


class Permission(str, Enum):
    PLATFORM_MANAGE = "platform:manage"
    COMPANY_MANAGE = "company:manage"
    VESSEL_READ = "vessel:read"
    VESSEL_WRITE = "vessel:write"
    VOYAGE_READ = "voyage:read"
    VOYAGE_WRITE = "voyage:write"
    TECHNICAL_READ = "technical:read"
    TECHNICAL_WRITE = "technical:write"
    HSSE_READ = "hsse:read"
    HSSE_WRITE = "hsse:write"
    FINANCE_READ = "finance:read"
    FINANCE_WRITE = "finance:write"
    PROCUREMENT_READ = "procurement:read"
    PROCUREMENT_WRITE = "procurement:write"
    CREW_READ = "crew:read"
    CREW_WRITE = "crew:write"
    AI_USE = "ai:use"
    AUDIT_READ = "audit:read"


ALL_PERMISSIONS: FrozenSet[Permission] = frozenset(Permission)


ROLE_PERMISSIONS = {
    Role.PLATFORM_ADMIN: ALL_PERMISSIONS,

    Role.COMPANY_ADMIN: frozenset(
        permission
        for permission in Permission
        if permission != Permission.PLATFORM_MANAGE
    ),

    Role.DPA: frozenset({
        Permission.VESSEL_READ,
        Permission.VOYAGE_READ,
        Permission.HSSE_READ,
        Permission.HSSE_WRITE,
        Permission.AI_USE,
        Permission.AUDIT_READ,
    }),

    Role.MARINE_SUPERINTENDENT: frozenset({
        Permission.VESSEL_READ,
        Permission.VESSEL_WRITE,
        Permission.VOYAGE_READ,
        Permission.VOYAGE_WRITE,
        Permission.HSSE_READ,
        Permission.AI_USE,
    }),

    Role.TECHNICAL_SUPERINTENDENT: frozenset({
        Permission.VESSEL_READ,
        Permission.TECHNICAL_READ,
        Permission.TECHNICAL_WRITE,
        Permission.AI_USE,
    }),

    Role.HSSE: frozenset({
        Permission.VESSEL_READ,
        Permission.HSSE_READ,
        Permission.HSSE_WRITE,
        Permission.AI_USE,
    }),

    Role.FINANCE: frozenset({
        Permission.FINANCE_READ,
        Permission.FINANCE_WRITE,
    }),

    Role.PROCUREMENT: frozenset({
        Permission.PROCUREMENT_READ,
        Permission.PROCUREMENT_WRITE,
        Permission.VESSEL_READ,
    }),

    Role.CREWING: frozenset({
        Permission.CREW_READ,
        Permission.CREW_WRITE,
        Permission.VESSEL_READ,
    }),

    Role.MASTER: frozenset({
        Permission.VESSEL_READ,
        Permission.VOYAGE_READ,
        Permission.VOYAGE_WRITE,
        Permission.HSSE_READ,
        Permission.HSSE_WRITE,
        Permission.CREW_READ,
        Permission.AI_USE,
    }),

    Role.CHIEF_ENGINEER: frozenset({
        Permission.VESSEL_READ,
        Permission.TECHNICAL_READ,
        Permission.TECHNICAL_WRITE,
        Permission.AI_USE,
    }),

    Role.VIEWER: frozenset({
        Permission.VESSEL_READ,
        Permission.VOYAGE_READ,
    }),
}


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def require_permission(role: Role, permission: Permission) -> None:
    if not has_permission(role, permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {permission.value}",
        )

from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, status


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    user_id: Optional[str] = None
    role: Optional[str] = None


async def get_tenant_context(
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-ID"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
) -> TenantContext:
    """
    Resolve the tenant context for the current request.

    X-Tenant-ID is mandatory for tenant-scoped operations.
    Authentication will later replace trusted user headers
    with verified identity claims.
    """

    if not x_tenant_id or not x_tenant_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-ID header is required.",
        )

    return TenantContext(
        tenant_id=x_tenant_id.strip(),
        user_id=x_user_id.strip() if x_user_id else None,
        role=x_user_role.strip() if x_user_role else None,
    )

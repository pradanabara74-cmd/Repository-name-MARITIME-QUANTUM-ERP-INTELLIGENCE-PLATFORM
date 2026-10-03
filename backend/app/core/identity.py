from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, status

from .rbac import Role


@dataclass(frozen=True)
class IdentityContext:
    """
    Verified identity representation used by MQEIP services.

    This is the security boundary between incoming authentication
    credentials and internal application authorization.
    """

    user_id: str
    tenant_id: str
    role: Role
    email: Optional[str] = None


async def get_identity_context(
    x_user_id: Optional[str] = Header(default=None, alias="X-User-ID"),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_user_email: Optional[str] = Header(default=None, alias="X-User-Email"),
) -> IdentityContext:
    """
    Resolve the authenticated identity for the current request.

    DEVELOPMENT FOUNDATION:
    Headers are temporarily used as identity inputs.

    PRODUCTION:
    These values will be replaced by cryptographically verified
    token claims from the MQEIP authentication provider.
    """

    if not x_user_id or not x_user_id.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user identity is required.",
        )

    if not x_tenant_id or not x_tenant_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant identity is required.",
        )

    if not x_user_role or not x_user_role.strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User role is required.",
        )

    try:
        role = Role(x_user_role.strip().lower())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or unauthorized user role.",
        )

    return IdentityContext(
        user_id=x_user_id.strip(),
        tenant_id=x_tenant_id.strip(),
        role=role,
        email=x_user_email.strip() if x_user_email else None,
    )

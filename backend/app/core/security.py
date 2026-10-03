import os
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from .rbac import Role


@dataclass(frozen=True)
class TokenIdentity:
    user_id: str
    tenant_id: str
    role: Role
    email: str | None = None


def _jwt_settings() -> tuple[str, str, str]:
    secret = os.getenv("MQEIP_JWT_SECRET", "").strip()
    issuer = os.getenv("MQEIP_JWT_ISSUER", "mqeip").strip()
    audience = os.getenv("MQEIP_JWT_AUDIENCE", "mqeip-api").strip()

    if not secret:
        raise RuntimeError("MQEIP_JWT_SECRET is not configured.")

    return secret, issuer, audience

bearer_scheme = HTTPBearer()


def verify_access_token(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> TokenIdentity:
    token = credentials.credentials
    secret, issuer, audience = _jwt_settings()

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=issuer,
            audience=audience,
            options={"require_exp": True},
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = str(payload.get("sub", "")).strip()
    tenant_id = str(payload.get("tenant_id", "")).strip()
    role_value = str(payload.get("role", "")).strip().lower()
    email = str(payload.get("email", "")).strip() or None

    if not user_id or not tenant_id or not role_value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token is missing required identity claims.",
        )

    try:
        role = Role(role_value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access token contains an unauthorized role.",
        )

    return TokenIdentity(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=email,
    )

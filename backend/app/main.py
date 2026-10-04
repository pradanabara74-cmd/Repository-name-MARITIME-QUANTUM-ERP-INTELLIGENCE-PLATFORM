from datetime import datetime, timezone
import os

from fastapi import Depends, FastAPI, HTTPException, status
from jose import jwt
from pydantic import BaseModel

from . import __platform__, __version__
from .core.security import TokenIdentity, verify_access_token
from .modules.fleet import router as fleet_router

class LoginRequest(BaseModel):
    username: str
    password: str


def create_access_token(user_id: str, tenant_id: str, role: str, email: str | None = None) -> str:
    secret = os.getenv("MQEIP_JWT_SECRET", "").strip()
    issuer = os.getenv("MQEIP_JWT_ISSUER", "mqeip").strip()
    audience = os.getenv("MQEIP_JWT_AUDIENCE", "mqeip-api").strip()

    if not secret:
        raise RuntimeError("MQEIP_JWT_SECRET is not configured.")

    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "email": email,
        "iss": issuer,
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + 3600,
    }

    return jwt.encode(payload, secret, algorithm="HS256")


app = FastAPI(
    title="Maritime Quantum ERP & Intelligence Platform",
    description="Enterprise Maritime ERP, Integration and Intelligence API",
    version=__version__,
)
app.include_router(fleet_router)

@app.post("/api/v1/login", tags=["Security"])
async def login(request: LoginRequest):
    admin_username = os.getenv("MQEIP_ADMIN_USERNAME", "").strip()
    admin_password = os.getenv("MQEIP_ADMIN_PASSWORD", "").strip()

    if not admin_username or not admin_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login service is not configured.",
        )

    if request.username != admin_username or request.password != admin_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    access_token = create_access_token(
        user_id="admin-001",
        tenant_id="client-001",
        role="platform_admin",
        email=None,
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 3600,
    }


@app.get("/", tags=["Platform"])
async def root():
    return {
        "platform": __platform__,
        "name": "Maritime Quantum ERP & Intelligence Platform",
        "version": __version__,
        "architecture": "Enterprise API-First",
        "status": "operational",
    }


@app.get("/health", tags=["System"])
async def health():
    return {
        "status": "healthy",
        "platform": __platform__,
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/platform", tags=["Platform"])
async def platform_information():
    return {
        "platform": __platform__,
        "version": __version__,
        "api_version": "v1",
        "capabilities": [
            "Maritime ERP Core",
            "MQX Integration Gateway",
            "Maritime Data Platform",
            "AI Intelligence",
            "Digital Twin",
            "Quantum-Inspired Optimization",
            "Maritime Command Center",
        ],
    }

@app.get("/api/v1/me", tags=["Security"])
async def current_identity(
    identity: TokenIdentity = Depends(verify_access_token),
):
    return {
        "user_id": identity.user_id,
        "tenant_id": identity.tenant_id,
        "role": identity.role.value,
        "email": identity.email,
    }

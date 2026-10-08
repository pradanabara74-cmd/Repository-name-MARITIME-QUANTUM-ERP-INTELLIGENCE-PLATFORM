from datetime import datetime, timezone
import os

from fastapi import Depends, FastAPI, HTTPException, status
from jose import jwt
from pydantic import BaseModel

from . import __platform__, __version__
from .core.security import TokenIdentity, verify_access_token
from .modules.fleet import router as fleet_router
from .modules.executive import router as executive_router
from .modules.crew import router as crew_router
from .modules.sms import router as sms_router
from .modules.pms import router as pms_router
from .modules.supply_chain import router as supply_chain_router
from .modules.marine_operations import router as marine_operations_router
from .modules.commercial import router as commercial_router
from .modules.inventory import router as inventory_router
from .modules.finance import router as finance_router
from .modules.hr import router as hr_router
from .modules.hsse import router as hsse_router
from .modules.technical import router as technical_router
from .modules.legal import router as legal_router
from .modules.audit import router as audit_router
from .modules.document_management import router as document_management_router
from .modules.workflow import router as workflow_router
from .modules.notifications import router as notifications_router
from .modules.notifications import router as notifications_router

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
app.include_router(executive_router)
app.include_router(crew_router)
app.include_router(sms_router)
app.include_router(pms_router)
app.include_router(supply_chain_router)
app.include_router(marine_operations_router)
app.include_router(commercial_router)
app.include_router(inventory_router)
app.include_router(finance_router)
app.include_router(hr_router)
app.include_router(hsse_router)
app.include_router(technical_router)
app.include_router(legal_router)
app.include_router(audit_router)
app.include_router(document_management_router)
app.include_router(workflow_router)
app.include_router(notifications_router)
app.include_router(notifications_router)
    
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

app.include_router(marine_operations_router)
    

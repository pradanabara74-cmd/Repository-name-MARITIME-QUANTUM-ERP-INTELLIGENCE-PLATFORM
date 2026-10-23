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
from .modules import maritime_quantum_command
quantum_command_router = maritime_quantum_command.router
from .modules.esg import router as esg_router
from .modules.business_intelligence import router as bi_router
from .modules.ai_intelligence import router as ai_router
from .modules.quantum_optimization import router as quantum_router
from .modules import digital_twin
from .modules import mqx_gateway
from .modules import enterprise_iam
from .modules import maritime_quantum_command

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


import asyncio
from contextlib import asynccontextmanager
from .modules.aisstream_connector import stream_ais
from .modules.aisstream_connector import router as ais_router

@asynccontextmanager
async def ais_lifespan(app):
    task = None
    print("MQEIP AIS LIFESPAN STARTED", flush=True)
    if os.getenv("AISSTREAM_API_KEY"):
        task = asyncio.create_task(stream_ais())
    try:
        yield
    finally:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

app = FastAPI(
    title="Maritime Quantum ERP & Intelligence Platform",
    description="Enterprise Maritime ERP, Integration and Intelligence API",
    version=__version__,
    lifespan=ais_lifespan,
)
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://mqeip-premium-frontend.onrender.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
app.include_router(esg_router)
app.include_router(bi_router)
app.include_router(ai_router)
app.include_router(ais_router)
app.include_router(quantum_router)
app.include_router(digital_twin.router)
app.include_router(mqx_gateway.router)
app.include_router(enterprise_iam.router)
app.include_router(maritime_quantum_command.router)
    
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

app.include_router(quantum_command_router)

app.include_router(marine_operations_router)
    

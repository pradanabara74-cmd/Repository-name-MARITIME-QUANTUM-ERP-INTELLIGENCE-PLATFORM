from datetime import datetime, timezone

from fastapi import Depends, FastAPI

from . import __platform__, __version__
from .core.security import TokenIdentity, verify_access_token


app = FastAPI(
    title="Maritime Quantum ERP & Intelligence Platform",
    description="Enterprise Maritime ERP, Integration and Intelligence API",
    version=__version__,
)


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

import asyncio
import hmac
import importlib
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from jose import jwt
from pydantic import BaseModel

from . import __platform__, __version__
from .core.security import TokenIdentity, verify_access_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mqeip")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------
def create_access_token( user_id: str, tenant_id: str, role: str, email: str | None = None, ) -> str:
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


# ---------------------------------------------------------------------------
# Router registry
# Urutan sama persis dengan versi sebelumnya. Setiap modul dimuat satu per
# satu: jika SATU modul gagal di-import, modul lain tetap hidup dan
# endpoint-nya tetap muncul di Swagger (sebelumnya seluruh backend mati).
# Set MQEIP_STRICT_ROUTERS=1 bila ingin deploy gagal total saat ada modul error.
# ---------------------------------------------------------------------------
ROUTER_MODULES = [
    "fleet",
    "executive",
    "crew",
    "sms",
    "pms",
    "supply_chain",
    "marine_operations",
    "commercial",
    "inventory",
    "finance",
    "hr",
    "hsse",
    "technical",
    "legal",
    "audit",
    "document_management",
    "workflow",
    "notifications",
    "esg",
    "business_intelligence",
    "ai_intelligence",
    "aisstream_connector",
    "quantum_optimization",
    "digital_twin",
    "mqx_gateway",
    "enterprise_iam",
    "maritime_quantum_command",
]

_loaded_modules: dict = {}
_router_failures: dict[str, str] = {}


def _load_routers(target_app: FastAPI) -> None:
    strict = os.getenv("MQEIP_STRICT_ROUTERS", "").strip().lower() in ("1", "true", "yes")
    for name in ROUTER_MODULES:
        try:
            module = importlib.import_module(f".modules.{name}", package=__package__)
            target_app.include_router(module.router)
            _loaded_modules[name] = module
        except Exception as exc:  # noqa: BLE001
            if strict:
                raise
            _router_failures[name] = f"{type(exc).__name__}: {exc}"
            logger.exception("MQEIP: gagal memuat modul '%s'", name)


# ---------------------------------------------------------------------------
# Lifespan (AIS stream background task)
# ---------------------------------------------------------------------------
def _log_ais_task_result(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("MQEIP AIS stream berhenti karena error: %r", exc)


@asynccontextmanager
async def ais_lifespan(app: FastAPI):
    task = None
    print("MQEIP AIS LIFESPAN STARTED", flush=True)
    ais_module = _loaded_modules.get("aisstream_connector")
    stream_ais = getattr(ais_module, "stream_ais", None)
    if os.getenv("AISSTREAM_API_KEY") and stream_ais:
        task = asyncio.create_task(stream_ais())
        task.add_done_callback(_log_ais_task_result)
    try:
        yield
    finally:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Maritime Quantum ERP & Intelligence Platform",
    description="Enterprise Maritime ERP, Integration and Intelligence API",
    version=__version__,
    lifespan=ais_lifespan,
)

# Render berada di belakang proxy: tanpa ini, redirect (mis. trailing slash)
# bisa berubah jadi http:// dan diblokir browser dari frontend https.
try:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
except ImportError:
    pass

# ---------------------------------------------------------------------------
# CORS
# Origin frontend MQEIP Premium SELALU diizinkan, ditambah origin dari
# MQEIP_CORS_ORIGINS (pisahkan dengan koma), GitHub Pages, subdomain
# mqeip*.onrender.com, dan localhost untuk development.
# ---------------------------------------------------------------------------
_DEFAULT_CORS_ORIGINS = ["https://mqeip-premium-frontend.onrender.com"]
_env_origins = [
    origin.strip().rstrip("/")
    for origin in os.getenv("MQEIP_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
_cors_origins = list(dict.fromkeys(_DEFAULT_CORS_ORIGINS + _env_origins))

_CORS_ORIGIN_REGEX = (
    r"https://([A-Za-z0-9-]+\.github\.io|mqeip[A-Za-z0-9-]*\.onrender\.com)"
    r"|http://(localhost|127\.0\.0\.1)(:\d+)?"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return False
    return (
        origin in _cors_origins
        or "*" in _cors_origins
        or re.fullmatch(_CORS_ORIGIN_REGEX, origin) is not None
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Error 500 tidak melewati CORSMiddleware, sehingga frontend melihatnya sebagai 'CORS error'. Handler ini menambahkan header CORS secara manual agar frontend menerima pesan error yang jelas."""
    logger.exception("Unhandled error pada %s %s", request.method, request.url.path)
    headers = {}
    origin = request.headers.get("origin")
    if _origin_allowed(origin):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
        headers["Vary"] = "Origin"
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error.", "error": type(exc).__name__},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
_load_routers(app)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post("/api/v1/login", tags=["Security"])
async def login(request: LoginRequest):
    admin_username = os.getenv("MQEIP_ADMIN_USERNAME", "").strip()
    admin_password = os.getenv("MQEIP_ADMIN_PASSWORD", "").strip()

    if not admin_username or not admin_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login service is not configured.",
        )

    username_ok = hmac.compare_digest(
        request.username.encode("utf-8"), admin_username.encode("utf-8")
    )
    password_ok = hmac.compare_digest(
        request.password.encode("utf-8"), admin_password.encode("utf-8")
    )
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    try:
        access_token = create_access_token(
            user_id="admin-001",
            tenant_id="client-001",
            role="platform_admin",
            email=None,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
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
        "modules_loaded": len(_loaded_modules),
        "modules_failed": len(_router_failures),
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
async def current_identity( identity: TokenIdentity = Depends(verify_access_token), ):
    return {
        "user_id": identity.user_id,
        "tenant_id": identity.tenant_id,
        "role": identity.role.value,
        "email": identity.email,
    }


# ---------------------------------------------------------------------------
# Diagnostics & resilient OpenAPI
# ---------------------------------------------------------------------------
def _find_duplicate_routes() -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods or []:
                key = f"{method} {route.path}"
                seen.setdefault(key, []).append(route.endpoint.__module__)
    return {k: v for k, v in seen.items() if len(v) > 1}


_duplicate_routes = _find_duplicate_routes()
for _key, _owners in _duplicate_routes.items():
    logger.warning("MQEIP: route ganda %s -> %s (yang pertama terdaftar yang aktif)", _key, _owners)

_openapi_skipped: dict[str, str] = {}


def _safe_openapi():
    """Jika satu route merusak pembuatan skema, /docs tidak lagi kosong: route bermasalah dilewati dan sisanya tetap tampil di Swagger."""
    if app.openapi_schema:
        return app.openapi_schema

    base = dict(title=app.title, version=app.version, description=app.description)
    try:
        schema = get_openapi(routes=app.routes, **base)
    except Exception:  # noqa: BLE001
        good = []
        for route in app.routes:
            try:
                get_openapi(routes=[route], **base)
                good.append(route)
            except Exception as exc:  # noqa: BLE001
                name = getattr(route, "path", repr(route))
                _openapi_skipped[name] = f"{type(exc).__name__}: {exc}"
                logger.error("MQEIP: route '%s' dilewati dari OpenAPI: %s", name, exc)
        schema = get_openapi(routes=good, **base)

    app.openapi_schema = schema
    return schema


app.openapi = _safe_openapi


@app.get("/api/v1/diagnostics", tags=["System"])
async def diagnostics(identity: TokenIdentity = Depends(verify_access_token)):
    return {
        "total_routes": len(app.routes),
        "modules_loaded": sorted(_loaded_modules),
        "modules_failed": _router_failures,
        "duplicate_routes": _duplicate_routes,
        "openapi_skipped_routes": _openapi_skipped,
        "cors_origins": _cors_origins,
    }

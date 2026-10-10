from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status, Header
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from app.core.database import get_database_url
from app.core.security import TokenIdentity, Role, verify_access_token
import os
import secrets
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

def verify_ais_token(
    authorization: HTTPAuthorizationCredentials = Depends(HTTPBearer())
) -> TokenIdentity:
    expected = os.getenv("MQEIP_AIS_SERVICE_TOKEN", "")
    if not expected or not secrets.compare_digest(
        authorization.credentials, expected
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid AIS service token"
        )

    return TokenIdentity(
        user_id="aisstream-service",
        tenant_id=os.environ["MQEIP_AIS_TENANT_ID"],
        role=Role.COMPANY_ADMIN,
        email=None,
    )


router = APIRouter(
    prefix="/api/v1/marine-operations",
    tags=["Marine Operations Intelligence"],
)

def get_engine():
    return create_engine(
        get_database_url(),
        pool_pre_ping=True,
    )


# ============================================================
# HELPERS
# ============================================================

def serialize_row(row):
    data = dict(row)

    for key, value in data.items():
        if isinstance(value, UUID):
            data[key] = str(value)
        elif isinstance(value, datetime):
            data[key] = value.isoformat()

    return data


def utc_now():
    return datetime.now(timezone.utc)


def freshness_status(observed_at: datetime) -> str:
    now = utc_now()

    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)

    age_seconds = (now - observed_at).total_seconds()

    if age_seconds <= 900:
        return "LIVE"
    if age_seconds <= 3600:
        return "DELAYED"
    return "STALE"


# ============================================================
# REQUEST MODELS
# ============================================================

class AISPositionCreate(BaseModel):
    vessel_id: Optional[str] = None
    vessel_name: str = Field(min_length=1, max_length=255)

    imo: Optional[str] = None
    mmsi: Optional[str] = None

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    sog_knots: Optional[float] = Field(default=None, ge=0)
    cog_degrees: Optional[float] = Field(default=None, ge=0, le=360)
    heading_degrees: Optional[float] = Field(
        default=None,
        ge=0,
        le=360,
    )

    navigational_status: Optional[str] = None
    destination: Optional[str] = None
    eta: Optional[str] = None
    draught_m: Optional[float] = Field(default=None, ge=0)

    source: str = Field(min_length=1, max_length=100)
    source_type: str = Field(
        default="AIS",
        min_length=1,
        max_length=50,
    )

    observed_at: datetime


class WeatherObservationCreate(BaseModel):
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    wind_speed_knots: Optional[float] = Field(default=None, ge=0)
    wind_gust_knots: Optional[float] = Field(default=None, ge=0)
    wind_direction_deg: Optional[float] = Field(
        default=None,
        ge=0,
        le=360,
    )

    wave_height_m: Optional[float] = Field(default=None, ge=0)
    wave_direction_deg: Optional[float] = Field(
        default=None,
        ge=0,
        le=360,
    )
    wave_period_s: Optional[float] = Field(default=None, ge=0)

    swell_height_m: Optional[float] = Field(default=None, ge=0)
    swell_direction_deg: Optional[float] = Field(
        default=None,
        ge=0,
        le=360,
    )
    swell_period_s: Optional[float] = Field(default=None, ge=0)

    pressure_hpa: Optional[float] = None
    air_temperature_c: Optional[float] = None
    sea_temperature_c: Optional[float] = None
    visibility_nm: Optional[float] = Field(default=None, ge=0)

    current_speed_knots: Optional[float] = Field(default=None, ge=0)
    current_direction_deg: Optional[float] = Field(
        default=None,
        ge=0,
        le=360,
    )

    tide_height_m: Optional[float] = None

    source: str = Field(min_length=1, max_length=100)
    observed_at: datetime


class MarineAlertCreate(BaseModel):
    vessel_id: Optional[str] = None
    vessel_name: Optional[str] = None

    alert_type: str = Field(min_length=1, max_length=100)
    severity: str = Field(min_length=1, max_length=50)

    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1)

    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)

    source: str = Field(min_length=1, max_length=100)
    observed_at: datetime


class VoyageCreate(BaseModel):
    vessel_id: Optional[str] = None
    vessel_name: str = Field(min_length=1, max_length=255)

    voyage_number: str = Field(min_length=1, max_length=100)

    departure_port: str = Field(min_length=1, max_length=255)
    destination_port: str = Field(min_length=1, max_length=255)

    departure_time: Optional[datetime] = None
    planned_eta: Optional[datetime] = None

    status: str = Field(default="planned", max_length=50)
    notes: Optional[str] = None


# ============================================================
# DATABASE
# ============================================================

def init_marine_operations_tables():
    engine = get_engine()

    with engine.begin() as conn:

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS marine_ais_positions (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,

                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255) NOT NULL,

                    imo VARCHAR(50),
                    mmsi VARCHAR(50),

                    latitude DOUBLE PRECISION NOT NULL,
                    longitude DOUBLE PRECISION NOT NULL,

                    sog_knots DOUBLE PRECISION,
                    cog_degrees DOUBLE PRECISION,
                    heading_degrees DOUBLE PRECISION,

                    navigational_status VARCHAR(100),
                    destination VARCHAR(255),
                    eta VARCHAR(100),
                    draught_m DOUBLE PRECISION,

                    source VARCHAR(100) NOT NULL,
                    source_type VARCHAR(50) NOT NULL,

                    observed_at TIMESTAMPTZ NOT NULL,
                    received_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_marine_ais_vessel_time
                ON marine_ais_positions
                (tenant_id, vessel_id, observed_at DESC)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_marine_ais_mmsi_time
                ON marine_ais_positions
                (tenant_id, mmsi, observed_at DESC)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS marine_weather_observations (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,

                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),

                    latitude DOUBLE PRECISION NOT NULL,
                    longitude DOUBLE PRECISION NOT NULL,

                    wind_speed_knots DOUBLE PRECISION,
                    wind_gust_knots DOUBLE PRECISION,
                    wind_direction_deg DOUBLE PRECISION,

                    wave_height_m DOUBLE PRECISION,
                    wave_direction_deg DOUBLE PRECISION,
                    wave_period_s DOUBLE PRECISION,

                    swell_height_m DOUBLE PRECISION,
                    swell_direction_deg DOUBLE PRECISION,
                    swell_period_s DOUBLE PRECISION,

                    pressure_hpa DOUBLE PRECISION,
                    air_temperature_c DOUBLE PRECISION,
                    sea_temperature_c DOUBLE PRECISION,
                    visibility_nm DOUBLE PRECISION,

                    current_speed_knots DOUBLE PRECISION,
                    current_direction_deg DOUBLE PRECISION,
                    tide_height_m DOUBLE PRECISION,

                    source VARCHAR(100) NOT NULL,

                    observed_at TIMESTAMPTZ NOT NULL,
                    received_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_marine_weather_time
                ON marine_weather_observations
                (tenant_id, observed_at DESC)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS marine_alerts (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,

                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255),

                    alert_type VARCHAR(100) NOT NULL,
                    severity VARCHAR(50) NOT NULL,

                    title VARCHAR(255) NOT NULL,
                    description TEXT NOT NULL,

                    latitude DOUBLE PRECISION,
                    longitude DOUBLE PRECISION,

                    source VARCHAR(100) NOT NULL,

                    observed_at TIMESTAMPTZ NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,

                    status VARCHAR(50) NOT NULL DEFAULT 'active'
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_marine_alerts_status
                ON marine_alerts
                (tenant_id, status, observed_at DESC)
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS marine_voyages (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(255) NOT NULL,

                    vessel_id VARCHAR(255),
                    vessel_name VARCHAR(255) NOT NULL,

                    voyage_number VARCHAR(100) NOT NULL,

                    departure_port VARCHAR(255) NOT NULL,
                    destination_port VARCHAR(255) NOT NULL,

                    departure_time TIMESTAMPTZ,
                    planned_eta TIMESTAMPTZ,

                    status VARCHAR(50) NOT NULL,
                    notes TEXT,

                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,

                    UNIQUE (tenant_id, voyage_number)
                )
                """
            )
        )


# ============================================================
# AIS / REAL VESSEL POSITION
# ============================================================

@router.post(
    "/ais/positions",
    status_code=status.HTTP_201_CREATED,
    summary="Ingest Verified AIS Position",
)
def ingest_ais_position(
    payload: AISPositionCreate,
    identity: TokenIdentity = Depends(verify_ais_token)
):
    init_marine_operations_tables()

    position_id = uuid4()
    received_at = utc_now()

    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO marine_ais_positions (
                    id,
                    tenant_id,
                    vessel_id,
                    vessel_name,
                    imo,
                    mmsi,
                    latitude,
                    longitude,
                    sog_knots,
                    cog_degrees,
                    heading_degrees,
                    navigational_status,
                    destination,
                    eta,
                    draught_m,
                    source,
                    source_type,
                    observed_at,
                    received_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :vessel_id,
                    :vessel_name,
                    :imo,
                    :mmsi,
                    :latitude,
                    :longitude,
                    :sog_knots,
                    :cog_degrees,
                    :heading_degrees,
                    :navigational_status,
                    :destination,
                    :eta,
                    :draught_m,
                    :source,
                    :source_type,
                    :observed_at,
                    :received_at
                )
                """
            ),
            {
                "id": position_id,
                "tenant_id": identity.tenant_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "imo": payload.imo,
                "mmsi": payload.mmsi,
                "latitude": payload.latitude,
                "longitude": payload.longitude,
                "sog_knots": payload.sog_knots,
                "cog_degrees": payload.cog_degrees,
                "heading_degrees": payload.heading_degrees,
                "navigational_status": payload.navigational_status,
                "destination": payload.destination,
                "eta": payload.eta,
                "draught_m": payload.draught_m,
                "source": payload.source,
                "source_type": payload.source_type,
                "observed_at": payload.observed_at,
                "received_at": received_at,
            },
        )

    return {
        "id": str(position_id),
        "vessel_name": payload.vessel_name,
        "latitude": payload.latitude,
        "longitude": payload.longitude,
        "source": payload.source,
        "observed_at": payload.observed_at,
        "freshness": freshness_status(payload.observed_at),
    }


@router.get(
    "/fleet/live",
    summary="World Fleet Live Position",
)
def world_fleet_live(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_marine_operations_tables()

    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT ON (
                    COALESCE(vessel_id, mmsi, vessel_name)
                )
                    *
                FROM marine_ais_positions
                WHERE tenant_id = :tenant_id
  AND source_type = 'AIS'
  AND UPPER(COALESCE(source, '')) NOT LIKE '%SIMULATION%'
  AND UPPER(COALESCE(source, '')) NOT LIKE '%TEST%'
                ORDER BY
                    COALESCE(vessel_id, mmsi, vessel_name),
                    observed_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    result = []

    for row in rows:
        data = serialize_row(row)
        data["freshness"] = freshness_status(row["observed_at"])
        result.append(data)

    return {
        "data_policy": "REAL_PROVIDER_DATA_ONLY",
        "fleet": result,
    }


@router.get(
    "/vessels/{vessel_id}/track",
    summary="Vessel AIS Track History",
)
def vessel_track_history(
    vessel_id: str,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_marine_operations_tables()

    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM marine_ais_positions
                WHERE tenant_id = :tenant_id
                  AND vessel_id = :vessel_id
                ORDER BY observed_at DESC
                LIMIT 1000
                """
            ),
            {
                "tenant_id": identity.tenant_id,
                "vessel_id": vessel_id,
            },
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# ============================================================
# WEATHER / OCEAN INTELLIGENCE
# ============================================================

@router.post(
    "/weather/observations",
    status_code=status.HTTP_201_CREATED,
    summary="Ingest Verified Weather Ocean Observation",
)
def ingest_weather_observation(
    payload: WeatherObservationCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_marine_operations_tables()

    observation_id = uuid4()
    received_at = utc_now()

    values = payload.model_dump()

    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO marine_weather_observations (
                    id,
                    tenant_id,
                    vessel_id,
                    vessel_name,
                    latitude,
                    longitude,
                    wind_speed_knots,
                    wind_gust_knots,
                    wind_direction_deg,
                    wave_height_m,
                    wave_direction_deg,
                    wave_period_s,
                    swell_height_m,
                    swell_direction_deg,
                    swell_period_s,
                    pressure_hpa,
                    air_temperature_c,
                    sea_temperature_c,
                    visibility_nm,
                    current_speed_knots,
                    current_direction_deg,
                    tide_height_m,
                    source,
                    observed_at,
                    received_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :vessel_id,
                    :vessel_name,
                    :latitude,
                    :longitude,
                    :wind_speed_knots,
                    :wind_gust_knots,
                    :wind_direction_deg,
                    :wave_height_m,
                    :wave_direction_deg,
                    :wave_period_s,
                    :swell_height_m,
                    :swell_direction_deg,
                    :swell_period_s,
                    :pressure_hpa,
                    :air_temperature_c,
                    :sea_temperature_c,
                    :visibility_nm,
                    :current_speed_knots,
                    :current_direction_deg,
                    :tide_height_m,
                    :source,
                    :observed_at,
                    :received_at
                )
                """
            ),
            {
                "id": observation_id,
                "tenant_id": identity.tenant_id,
                **values,
                "received_at": received_at,
            },
        )

    return {
        "id": str(observation_id),
        "source": payload.source,
        "observed_at": payload.observed_at,
        "freshness": freshness_status(payload.observed_at),
    }


@router.get(
    "/weather/latest",
    summary="Latest Weather Ocean Data",
)
def latest_weather(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_marine_operations_tables()

    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT ON (
                    COALESCE(
                        vessel_id,
                        vessel_name,
                        latitude::text || ',' || longitude::text
                    )
                )
                    *
                FROM marine_weather_observations
                WHERE tenant_id = :tenant_id
                ORDER BY
                    COALESCE(
                        vessel_id,
                        vessel_name,
                        latitude::text || ',' || longitude::text
                    ),
                    observed_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    result = []

    for row in rows:
        data = serialize_row(row)
        data["freshness"] = freshness_status(row["observed_at"])
        result.append(data)

    return {
        "data_policy": "REAL_PROVIDER_DATA_ONLY",
        "observations": result,
    }


# ============================================================
# TYPHOON / STORM / NAVIGATION / OPERATIONAL ALERTS
# ============================================================

@router.post(
    "/alerts",
    status_code=status.HTTP_201_CREATED,
    summary="Create Verified Marine Alert",
)
def create_marine_alert(
    payload: MarineAlertCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_marine_operations_tables()

    alert_id = uuid4()
    now = utc_now()

    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO marine_alerts (
                    id,
                    tenant_id,
                    vessel_id,
                    vessel_name,
                    alert_type,
                    severity,
                    title,
                    description,
                    latitude,
                    longitude,
                    source,
                    observed_at,
                    created_at,
                    status
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :vessel_id,
                    :vessel_name,
                    :alert_type,
                    :severity,
                    :title,
                    :description,
                    :latitude,
                    :longitude,
                    :source,
                    :observed_at,
                    :created_at,
                    'active'
                )
                """
            ),
            {
                "id": alert_id,
                "tenant_id": identity.tenant_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "alert_type": payload.alert_type.upper(),
                "severity": payload.severity.upper(),
                "title": payload.title,
                "description": payload.description,
                "latitude": payload.latitude,
                "longitude": payload.longitude,
                "source": payload.source,
                "observed_at": payload.observed_at,
                "created_at": now,
            },
        )

    return {
        "id": str(alert_id),
        "alert_type": payload.alert_type.upper(),
        "severity": payload.severity.upper(),
        "source": payload.source,
        "status": "active",
    }


@router.get(
    "/alerts",
    summary="Marine Operational Alerts",
)
def list_marine_alerts(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_marine_operations_tables()

    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM marine_alerts
                WHERE tenant_id = :tenant_id
                  AND status = 'active'
                ORDER BY observed_at DESC
                LIMIT 500
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# ============================================================
# VOYAGE MANAGEMENT
# ============================================================

@router.post(
    "/voyages",
    status_code=status.HTTP_201_CREATED,
    summary="Create Voyage",
)
def create_voyage(
    payload: VoyageCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_marine_operations_tables()

    voyage_id = uuid4()
    now = utc_now()

    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO marine_voyages (
                    id,
                    tenant_id,
                    vessel_id,
                    vessel_name,
                    voyage_number,
                    departure_port,
                    destination_port,
                    departure_time,
                    planned_eta,
                    status,
                    notes,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :vessel_id,
                    :vessel_name,
                    :voyage_number,
                    :departure_port,
                    :destination_port,
                    :departure_time,
                    :planned_eta,
                    :status,
                    :notes,
                    :created_by,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "id": voyage_id,
                "tenant_id": identity.tenant_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "voyage_number": payload.voyage_number,
                "departure_port": payload.departure_port,
                "destination_port": payload.destination_port,
                "departure_time": payload.departure_time,
                "planned_eta": payload.planned_eta,
                "status": payload.status.lower(),
                "notes": payload.notes,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(voyage_id),
        "voyage_number": payload.voyage_number,
        "vessel_name": payload.vessel_name,
        "status": payload.status.lower(),
    }


@router.get(
    "/voyages",
    summary="List Voyages",
)
def list_voyages(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_marine_operations_tables()

    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM marine_voyages
                WHERE tenant_id = :tenant_id
                ORDER BY created_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [serialize_row(row) for row in rows]


# ============================================================
# MARINE OPERATIONS COMMAND CENTER
# ============================================================

@router.get(
    "/command-center",
    summary="Marine Operations Command Center",
)
def marine_command_center(
    identity: TokenIdentity = Depends(verify_access_token),
):
    init_marine_operations_tables()

    engine = get_engine()

    with engine.begin() as conn:

        fleet = conn.execute(
            text(
                """
                SELECT DISTINCT ON (
                    COALESCE(vessel_id, mmsi, vessel_name)
                )
                    vessel_id,
                    vessel_name,
                    imo,
                    mmsi,
                    latitude,
                    longitude,
                    sog_knots,
                    cog_degrees,
                    heading_degrees,
                    navigational_status,
                    destination,
                    eta,
                    source,
                    observed_at
                FROM marine_ais_positions
                WHERE tenant_id = :tenant_id
                ORDER BY
                    COALESCE(vessel_id, mmsi, vessel_name),
                    observed_at DESC
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

        active_alerts = conn.execute(
            text(
                """
                SELECT COUNT(*) AS count
                FROM marine_alerts
                WHERE tenant_id = :tenant_id
                  AND status = 'active'
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

        active_voyages = conn.execute(
            text(
                """
                SELECT COUNT(*) AS count
                FROM marine_voyages
                WHERE tenant_id = :tenant_id
                  AND status IN (
                      'active',
                      'underway',
                      'in_progress'
                  )
                """
            ),
            {"tenant_id": identity.tenant_id},
        ).scalar_one()

    live_count = 0
    delayed_count = 0
    stale_count = 0

    fleet_data = []

    for row in fleet:
        data = serialize_row(row)
        freshness = freshness_status(row["observed_at"])
        data["freshness"] = freshness

        if freshness == "LIVE":
            live_count += 1
        elif freshness == "DELAYED":
            delayed_count += 1
        else:
            stale_count += 1

        fleet_data.append(data)

    return {
        "system": "MQEIP MARINE OPERATIONS INTELLIGENCE",
        "data_policy": "REAL_PROVIDER_DATA_ONLY",
        "total_tracked_vessels": len(fleet_data),
        "live_positions": live_count,
        "delayed_positions": delayed_count,
        "stale_positions": stale_count,
        "active_voyages": active_voyages,
        "active_marine_alerts": active_alerts,
        "world_fleet": fleet_data,
    }

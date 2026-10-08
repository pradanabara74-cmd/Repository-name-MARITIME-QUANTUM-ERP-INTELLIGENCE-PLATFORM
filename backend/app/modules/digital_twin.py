# ============================================================
# MQEIP - MARITIME DIGITAL TWIN INTELLIGENCE
# BLOK 1 - CORE, DATA MODELS & POSTGRESQL FOUNDATION
# ============================================================

import json
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import text

from ..core.database import get_engine
from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/digital-twin",
    tags=["Maritime Digital Twin Intelligence"],
)


UTC = timezone.utc

TWIN_STATUSES = (
    "registered",
    "active",
    "inactive",
    "maintenance",
)

DATA_SOURCES = (
    "manual",
    "dpr",
    "sensor",
    "api",
    "integration",
)

MAX_SNAPSHOT_RECORDS = 500


# ============================================================
# BASE VALIDATION MODEL
# ============================================================

class DigitalTwinBaseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )


# ============================================================
# VESSEL DIGITAL TWIN REGISTRATION
# ============================================================

class VesselTwinCreate(DigitalTwinBaseModel):
    vessel_name: str = Field(
        min_length=2,
        max_length=150,
    )

    vessel_type: str = Field(
        min_length=2,
        max_length=100,
    )

    imo_number: Optional[str] = Field(
        default=None,
        max_length=20,
    )

    vessel_reference: Optional[str] = Field(
        default=None,
        max_length=150,
    )

    flag_state: Optional[str] = Field(
        default=None,
        max_length=100,
    )

    gross_tonnage: Optional[float] = Field(
        default=None,
        ge=0,
    )

    deadweight_tonnage: Optional[float] = Field(
        default=None,
        ge=0,
    )

    main_engine_count: int = Field(
        default=1,
        ge=0,
        le=20,
    )

    main_engine_total_power_kw: Optional[float] = Field(
        default=None,
        ge=0,
    )

    auxiliary_engine_count: int = Field(
        default=1,
        ge=0,
        le=20,
    )

    fuel_capacity_liters: Optional[float] = Field(
        default=None,
        ge=0,
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
    )


# ============================================================
# VESSEL DIGITAL TWIN STATUS
# ============================================================

class VesselTwinStatusUpdate(DigitalTwinBaseModel):
    twin_status: Literal[
        "registered",
        "active",
        "inactive",
        "maintenance",
    ]


# ============================================================
# DIGITAL TWIN OPERATIONAL SNAPSHOT
# ============================================================

class TwinSnapshotCreate(DigitalTwinBaseModel):
    recorded_at: datetime

    data_source: Literal[
        "manual",
        "dpr",
        "sensor",
        "api",
        "integration",
    ] = "manual"

    voyage_reference: Optional[str] = Field(
        default=None,
        max_length=150,
    )

    latitude: Optional[float] = Field(
        default=None,
        ge=-90,
        le=90,
    )

    longitude: Optional[float] = Field(
        default=None,
        ge=-180,
        le=180,
    )

    speed_knots: Optional[float] = Field(
        default=None,
        ge=0,
    )

    heading_degrees: Optional[float] = Field(
        default=None,
        ge=0,
        lt=360,
    )

    main_engine_rpm: Optional[float] = Field(
        default=None,
        ge=0,
    )

    main_engine_load_percent: Optional[float] = Field(
        default=None,
        ge=0,
        le=100,
    )

    auxiliary_engine_load_percent: Optional[float] = Field(
        default=None,
        ge=0,
        le=100,
    )

    fuel_remaining_liters: Optional[float] = Field(
        default=None,
        ge=0,
    )

    fuel_consumption_lph: Optional[float] = Field(
        default=None,
        ge=0,
    )

    engine_temperature_c: Optional[float] = None

    engine_oil_pressure_bar: Optional[float] = Field(
        default=None,
        ge=0,
    )

    sea_state: Optional[str] = Field(
        default=None,
        max_length=100,
    )

    weather_description: Optional[str] = Field(
        default=None,
        max_length=500,
    )

    equipment_status: dict[str, Any] = Field(
        default_factory=dict,
    )

    additional_metrics: dict[str, Any] = Field(
        default_factory=dict,
    )


# ============================================================
# DIGITAL TWIN EVENT
# ============================================================

class TwinEventCreate(DigitalTwinBaseModel):
    event_type: str = Field(
        min_length=2,
        max_length=100,
    )

    severity: Literal[
        "info",
        "low",
        "medium",
        "high",
        "critical",
    ] = "info"

    description: str = Field(
        min_length=5,
        max_length=5000,
    )

    source_module: Optional[str] = Field(
        default=None,
        max_length=100,
    )

    source_record_id: Optional[str] = Field(
        default=None,
        max_length=150,
    )

    event_data: dict[str, Any] = Field(
        default_factory=dict,
    )


# ============================================================
# SERIALIZATION HELPERS
# ============================================================

def twin_serialize_value(value):
    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {
            str(key): twin_serialize_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            twin_serialize_value(item)
            for item in value
        ]

    return value


def twin_serialize_row(row):
    return {
        key: twin_serialize_value(value)
        for key, value in dict(row).items()
    }


def twin_json(data):
    return json.dumps(
        data,
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


# ============================================================
# AUDIT TRAIL
# ============================================================

def record_twin_audit(
    conn,
    identity,
    entity_type,
    entity_id,
    action,
    details=None,
):
    conn.execute(
        text("""
            INSERT INTO digital_twin_audit_log (
                id,
                tenant_id,
                entity_type,
                entity_id,
                action,
                details,
                actor_id,
                created_at
            )
            VALUES (
                :id,
                :tenant_id,
                :entity_type,
                :entity_id,
                :action,
                :details,
                :actor_id,
                :created_at
            )
        """),
        {
            "id": uuid4(),
            "tenant_id": str(identity.tenant_id),
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "action": action,
            "details": details,
            "actor_id": str(identity.user_id),
            "created_at": datetime.now(UTC),
        },
    )


# ============================================================
# POSTGRESQL DATABASE INITIALIZATION
# ============================================================

def init_digital_twin_tables():
    engine = get_engine()

    statements = [

        """
        CREATE TABLE IF NOT EXISTS digital_twins (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            vessel_name VARCHAR(150) NOT NULL,
            vessel_type VARCHAR(100) NOT NULL,
            imo_number VARCHAR(20),
            vessel_reference VARCHAR(150),
            flag_state VARCHAR(100),
            gross_tonnage DOUBLE PRECISION,
            deadweight_tonnage DOUBLE PRECISION,
            main_engine_count INTEGER NOT NULL,
            main_engine_total_power_kw DOUBLE PRECISION,
            auxiliary_engine_count INTEGER NOT NULL,
            fuel_capacity_liters DOUBLE PRECISION,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            twin_status VARCHAR(30)
                NOT NULL DEFAULT 'registered',
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS digital_twin_snapshots (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            twin_id UUID NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL,
            data_source VARCHAR(30) NOT NULL,
            voyage_reference TEXT,
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            speed_knots DOUBLE PRECISION,
            heading_degrees DOUBLE PRECISION,
            main_engine_rpm DOUBLE PRECISION,
            main_engine_load_percent DOUBLE PRECISION,
            auxiliary_engine_load_percent DOUBLE PRECISION,
            fuel_remaining_liters DOUBLE PRECISION,
            fuel_consumption_lph DOUBLE PRECISION,
            engine_temperature_c DOUBLE PRECISION,
            engine_oil_pressure_bar DOUBLE PRECISION,
            sea_state TEXT,
            weather_description TEXT,
            equipment_status_json TEXT NOT NULL DEFAULT '{}',
            additional_metrics_json TEXT NOT NULL DEFAULT '{}',
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS digital_twin_events (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            twin_id UUID NOT NULL,
            event_type VARCHAR(100) NOT NULL,
            severity VARCHAR(30) NOT NULL,
            description TEXT NOT NULL,
            source_module TEXT,
            source_record_id TEXT,
            event_data_json TEXT NOT NULL DEFAULT '{}',
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS digital_twin_audit_log (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            entity_type VARCHAR(100) NOT NULL,
            entity_id TEXT NOT NULL,
            action VARCHAR(100) NOT NULL,
            details TEXT,
            actor_id TEXT,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_twin_tenant
        ON digital_twins (
            tenant_id,
            vessel_name
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_twin_snapshots
        ON digital_twin_snapshots (
            tenant_id,
            twin_id,
            recorded_at DESC
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_twin_events
        ON digital_twin_events (
            tenant_id,
            twin_id,
            created_at DESC
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_twin_audit
        ON digital_twin_audit_log (
            tenant_id,
            created_at DESC
        )
        """,
    ]

    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


# ============================================================
# END OF DIGITAL TWIN BLOK 1
# ============================================================
# ============================================================
# MQEIP - MARITIME DIGITAL TWIN INTELLIGENCE
# BLOK 2 - VESSEL TWIN REGISTRY & LIFECYCLE
# ============================================================


# ============================================================
# REGISTER VESSEL DIGITAL TWIN
# ============================================================

@router.post(
    "/vessels",
    status_code=status.HTTP_201_CREATED,
    summary="Register Vessel Digital Twin",
)
def create_vessel_twin(
    payload: VesselTwinCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    twin_id = uuid4()
    now = datetime.now(UTC)
    tenant_id = str(identity.tenant_id)

    vessel_name = payload.vessel_name.strip()
    vessel_type = payload.vessel_type.strip()

    if not vessel_name or not vessel_type:
        raise HTTPException(
            status_code=422,
            detail="Vessel name and type are required.",
        )

    with get_engine().begin() as conn:

        if payload.vessel_reference:
            existing = conn.execute(
                text("""
                    SELECT id
                    FROM digital_twins
                    WHERE tenant_id = :tenant_id
                      AND vessel_reference = :vessel_reference
                    LIMIT 1
                """),
                {
                    "tenant_id": tenant_id,
                    "vessel_reference": payload.vessel_reference,
                },
            ).first()

            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A Digital Twin with this vessel "
                        "reference already exists."
                    ),
                )

        conn.execute(
            text("""
                INSERT INTO digital_twins (
                    id,
                    tenant_id,
                    vessel_name,
                    vessel_type,
                    imo_number,
                    vessel_reference,
                    flag_state,
                    gross_tonnage,
                    deadweight_tonnage,
                    main_engine_count,
                    main_engine_total_power_kw,
                    auxiliary_engine_count,
                    fuel_capacity_liters,
                    metadata_json,
                    twin_status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :vessel_name,
                    :vessel_type,
                    :imo_number,
                    :vessel_reference,
                    :flag_state,
                    :gross_tonnage,
                    :deadweight_tonnage,
                    :main_engine_count,
                    :main_engine_total_power_kw,
                    :auxiliary_engine_count,
                    :fuel_capacity_liters,
                    :metadata_json,
                    'registered',
                    :created_by,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "id": twin_id,
                "tenant_id": tenant_id,
                "vessel_name": vessel_name,
                "vessel_type": vessel_type,
                "imo_number": payload.imo_number,
                "vessel_reference": payload.vessel_reference,
                "flag_state": payload.flag_state,
                "gross_tonnage": payload.gross_tonnage,
                "deadweight_tonnage": payload.deadweight_tonnage,
                "main_engine_count": payload.main_engine_count,
                "main_engine_total_power_kw":
                    payload.main_engine_total_power_kw,
                "auxiliary_engine_count":
                    payload.auxiliary_engine_count,
                "fuel_capacity_liters":
                    payload.fuel_capacity_liters,
                "metadata_json": twin_json(payload.metadata),
                "created_by": str(identity.user_id),
                "created_at": now,
                "updated_at": now,
            },
        )

        record_twin_audit(
            conn,
            identity,
            "vessel_twin",
            twin_id,
            "created",
            twin_json({
                "vessel_name": vessel_name,
                "vessel_type": vessel_type,
                "vessel_reference": payload.vessel_reference,
            }),
        )

    return {
        "id": str(twin_id),
        "vessel_name": vessel_name,
        "vessel_type": vessel_type,
        "twin_status": "registered",
        "created_at": now.isoformat(),
    }


# ============================================================
# LIST VESSEL DIGITAL TWINS
# ============================================================

@router.get(
    "/vessels",
    summary="List Vessel Digital Twins",
)
def list_vessel_twins(
    twin_status: Optional[str] = None,
    vessel_type: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    id,
                    tenant_id,
                    vessel_name,
                    vessel_type,
                    imo_number,
                    vessel_reference,
                    flag_state,
                    gross_tonnage,
                    deadweight_tonnage,
                    main_engine_count,
                    main_engine_total_power_kw,
                    auxiliary_engine_count,
                    fuel_capacity_liters,
                    twin_status,
                    created_at,
                    updated_at
                FROM digital_twins
                WHERE tenant_id = :tenant_id
                  AND (
                      :twin_status IS NULL
                      OR twin_status = :twin_status
                  )
                  AND (
                      :vessel_type IS NULL
                      OR vessel_type = :vessel_type
                  )
                ORDER BY vessel_name, id
                LIMIT :limit
            """),
            {
                "tenant_id": str(identity.tenant_id),
                "twin_status": twin_status,
                "vessel_type": vessel_type,
                "limit": limit,
            },
        ).mappings().all()

    return {
        "module": "Maritime Digital Twin Registry",
        "count": len(rows),
        "vessels": [
            twin_serialize_row(row)
            for row in rows
        ],
    }


# ============================================================
# GET VESSEL DIGITAL TWIN DETAILS
# ============================================================

@router.get(
    "/vessels/{twin_id}",
    summary="Get Vessel Digital Twin Details",
)
def get_vessel_twin(
    twin_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        row = conn.execute(
            text("""
                SELECT *
                FROM digital_twins
                WHERE id = :twin_id
                  AND tenant_id = :tenant_id
            """),
            {
                "twin_id": twin_id,
                "tenant_id": str(identity.tenant_id),
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Vessel Digital Twin not found.",
        )

    item = twin_serialize_row(row)
    raw = item.pop("metadata_json", None)

    try:
        item["metadata"] = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        item["metadata"] = {}

    return item


# ============================================================
# UPDATE VESSEL TWIN STATUS
# ============================================================

@router.patch(
    "/vessels/{twin_id}/status",
    summary="Update Vessel Digital Twin Status",
)
def update_vessel_twin_status(
    twin_id: UUID,
    payload: VesselTwinStatusUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    now = datetime.now(UTC)
    tenant_id = str(identity.tenant_id)

    with get_engine().begin() as conn:
        existing = conn.execute(
            text("""
                SELECT
                    id,
                    vessel_name,
                    twin_status
                FROM digital_twins
                WHERE id = :twin_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "twin_id": twin_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="Vessel Digital Twin not found.",
            )

        conn.execute(
            text("""
                UPDATE digital_twins
                SET twin_status = :twin_status,
                    updated_at = :updated_at
                WHERE id = :twin_id
                  AND tenant_id = :tenant_id
            """),
            {
                "twin_status": payload.twin_status,
                "updated_at": now,
                "twin_id": twin_id,
                "tenant_id": tenant_id,
            },
        )

        record_twin_audit(
            conn,
            identity,
            "vessel_twin",
            twin_id,
            "status_updated",
            twin_json({
                "previous_status": existing["twin_status"],
                "new_status": payload.twin_status,
            }),
        )

    return {
        "id": str(twin_id),
        "vessel_name": existing["vessel_name"],
        "previous_status": existing["twin_status"],
        "twin_status": payload.twin_status,
        "updated_at": now.isoformat(),
    }


# ============================================================
# FLEET DIGITAL TWIN REGISTRY INTELLIGENCE
# ============================================================

@router.get(
    "/intelligence/fleet",
    summary="Fleet Digital Twin Registry Intelligence",
)
def digital_twin_fleet_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    vessel_type,
                    twin_status,
                    COUNT(*) AS vessel_count
                FROM digital_twins
                WHERE tenant_id = :tenant_id
                GROUP BY vessel_type, twin_status
                ORDER BY vessel_type, twin_status
            """),
            {
                "tenant_id": str(identity.tenant_id),
            },
        ).mappings().all()

    groups = [
        twin_serialize_row(row)
        for row in rows
    ]

    return {
        "module": "Fleet Digital Twin Intelligence",
        "total_twins": sum(
            item["vessel_count"]
            for item in groups
        ),
        "groups": groups,
        "generated_at": datetime.now(UTC).isoformat(),
        "note": (
            "Registered Digital Twins are not necessarily "
            "connected to live vessel telemetry."
        ),
    }


# ============================================================
# END OF DIGITAL TWIN BLOK 2
# ============================================================

# ============================================================
# MQEIP - MARITIME DIGITAL TWIN INTELLIGENCE
# BLOK 3 - OPERATIONAL SNAPSHOTS & FUEL INTELLIGENCE
# ============================================================


# ============================================================
# SNAPSHOT SERIALIZATION
# ============================================================

def serialize_twin_snapshot(row):
    item = twin_serialize_row(row)

    for source_key, target_key in (
        ("equipment_status_json", "equipment_status"),
        ("additional_metrics_json", "additional_metrics"),
    ):
        raw = item.pop(source_key, None)

        try:
            item[target_key] = (
                json.loads(raw) if raw else {}
            )
        except (TypeError, ValueError):
            item[target_key] = {}

    return item


# ============================================================
# RECORD VESSEL OPERATIONAL SNAPSHOT
# ============================================================

@router.post(
    "/vessels/{twin_id}/snapshots",
    status_code=status.HTTP_201_CREATED,
    summary="Record Vessel Operational Snapshot",
)
def create_twin_snapshot(
    twin_id: UUID,
    payload: TwinSnapshotCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)
    snapshot_id = uuid4()
    now = datetime.now(UTC)

    if payload.recorded_at.utcoffset() is None:
        raise HTTPException(
            status_code=422,
            detail="recorded_at must include a timezone.",
        )

    if payload.recorded_at > now:
        raise HTTPException(
            status_code=422,
            detail="Snapshot timestamp cannot be in the future.",
        )

    with get_engine().begin() as conn:
        vessel = conn.execute(
            text("""
                SELECT
                    id,
                    vessel_name,
                    twin_status,
                    fuel_capacity_liters
                FROM digital_twins
                WHERE id = :twin_id
                  AND tenant_id = :tenant_id
            """),
            {
                "twin_id": twin_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if vessel is None:
            raise HTTPException(
                status_code=404,
                detail="Vessel Digital Twin not found.",
            )

        if vessel["twin_status"] == "inactive":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot record operational data "
                    "for an inactive Digital Twin."
                ),
            )

        capacity = vessel["fuel_capacity_liters"]
        remaining = payload.fuel_remaining_liters

        if (
            capacity is not None
            and remaining is not None
            and remaining > capacity
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Fuel remaining exceeds "
                    "registered vessel fuel capacity."
                ),
            )

        conn.execute(
            text("""
                INSERT INTO digital_twin_snapshots (
                    id,
                    tenant_id,
                    twin_id,
                    recorded_at,
                    data_source,
                    voyage_reference,
                    latitude,
                    longitude,
                    speed_knots,
                    heading_degrees,
                    main_engine_rpm,
                    main_engine_load_percent,
                    auxiliary_engine_load_percent,
                    fuel_remaining_liters,
                    fuel_consumption_lph,
                    engine_temperature_c,
                    engine_oil_pressure_bar,
                    sea_state,
                    weather_description,
                    equipment_status_json,
                    additional_metrics_json,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :twin_id,
                    :recorded_at,
                    :data_source,
                    :voyage_reference,
                    :latitude,
                    :longitude,
                    :speed_knots,
                    :heading_degrees,
                    :main_engine_rpm,
                    :main_engine_load_percent,
                    :auxiliary_engine_load_percent,
                    :fuel_remaining_liters,
                    :fuel_consumption_lph,
                    :engine_temperature_c,
                    :engine_oil_pressure_bar,
                    :sea_state,
                    :weather_description,
                    :equipment_status_json,
                    :additional_metrics_json,
                    :created_by,
                    :created_at
                )
            """),
            {
                "id": snapshot_id,
                "tenant_id": tenant_id,
                "twin_id": twin_id,
                "recorded_at": payload.recorded_at,
                "data_source": payload.data_source,
                "voyage_reference": payload.voyage_reference,
                "latitude": payload.latitude,
                "longitude": payload.longitude,
                "speed_knots": payload.speed_knots,
                "heading_degrees": payload.heading_degrees,
                "main_engine_rpm": payload.main_engine_rpm,
                "main_engine_load_percent":
                    payload.main_engine_load_percent,
                "auxiliary_engine_load_percent":
                    payload.auxiliary_engine_load_percent,
                "fuel_remaining_liters":
                    payload.fuel_remaining_liters,
                "fuel_consumption_lph":
                    payload.fuel_consumption_lph,
                "engine_temperature_c":
                    payload.engine_temperature_c,
                "engine_oil_pressure_bar":
                    payload.engine_oil_pressure_bar,
                "sea_state": payload.sea_state,
                "weather_description":
                    payload.weather_description,
                "equipment_status_json": twin_json(
                    payload.equipment_status
                ),
                "additional_metrics_json": twin_json(
                    payload.additional_metrics
                ),
                "created_by": str(identity.user_id),
                "created_at": now,
            },
        )

        record_twin_audit(
            conn,
            identity,
            "snapshot",
            snapshot_id,
            "created",
            twin_json({
                "twin_id": str(twin_id),
                "data_source": payload.data_source,
                "recorded_at": payload.recorded_at.isoformat(),
            }),
        )

    return {
        "snapshot_id": str(snapshot_id),
        "twin_id": str(twin_id),
        "vessel_name": vessel["vessel_name"],
        "recorded_at": payload.recorded_at.isoformat(),
        "data_source": payload.data_source,
        "status": "recorded",
    }


# ============================================================
# LIST VESSEL OPERATIONAL SNAPSHOTS
# ============================================================

@router.get(
    "/vessels/{twin_id}/snapshots",
    summary="List Vessel Operational Snapshots",
)
def list_twin_snapshots(
    twin_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:
        vessel = conn.execute(
            text("""
                SELECT id, vessel_name
                FROM digital_twins
                WHERE id = :twin_id
                  AND tenant_id = :tenant_id
            """),
            {
                "twin_id": twin_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if vessel is None:
            raise HTTPException(
                status_code=404,
                detail="Vessel Digital Twin not found.",
            )

        rows = conn.execute(
            text("""
                SELECT *
                FROM digital_twin_snapshots
                WHERE twin_id = :twin_id
                  AND tenant_id = :tenant_id
                ORDER BY recorded_at DESC, id DESC
                LIMIT :limit
            """),
            {
                "twin_id": twin_id,
                "tenant_id": tenant_id,
                "limit": limit,
            },
        ).mappings().all()

    return {
        "twin_id": str(twin_id),
        "vessel_name": vessel["vessel_name"],
        "count": len(rows),
        "snapshots": [
            serialize_twin_snapshot(row)
            for row in rows
        ],
    }


# ============================================================
# LATEST VESSEL DIGITAL TWIN STATE
# ============================================================

@router.get(
    "/vessels/{twin_id}/latest-state",
    summary="Get Latest Vessel Digital Twin State",
)
def get_twin_latest_state(
    twin_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:
        vessel = conn.execute(
            text("""
                SELECT
                    id,
                    vessel_name,
                    vessel_type,
                    twin_status,
                    fuel_capacity_liters
                FROM digital_twins
                WHERE id = :twin_id
                  AND tenant_id = :tenant_id
            """),
            {
                "twin_id": twin_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if vessel is None:
            raise HTTPException(
                status_code=404,
                detail="Vessel Digital Twin not found.",
            )

        latest = conn.execute(
            text("""
                SELECT *
                FROM digital_twin_snapshots
                WHERE twin_id = :twin_id
                  AND tenant_id = :tenant_id
                ORDER BY recorded_at DESC, id DESC
                LIMIT 1
            """),
            {
                "twin_id": twin_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

    if latest is None:
        return {
            "twin_id": str(twin_id),
            "vessel_name": vessel["vessel_name"],
            "twin_status": vessel["twin_status"],
            "data_status": "no_snapshots",
            "latest_state": None,
        }

    latest_data = serialize_twin_snapshot(latest)

    recorded_at = latest["recorded_at"]

    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=UTC)

    age_hours = max(
        0.0,
        (
            datetime.now(UTC) - recorded_at
        ).total_seconds() / 3600.0,
    )

    remaining = latest["fuel_remaining_liters"]
    consumption = latest["fuel_consumption_lph"]

    estimated_endurance_hours = None

    if (
        remaining is not None
        and consumption is not None
        and consumption > 0
    ):
        estimated_endurance_hours = round(
            remaining / consumption,
            2,
        )

    return {
        "twin_id": str(twin_id),
        "vessel_name": vessel["vessel_name"],
        "vessel_type": vessel["vessel_type"],
        "twin_status": vessel["twin_status"],
        "data_status": (
            "recent"
            if age_hours <= 24
            else "stale"
        ),
        "snapshot_age_hours": round(age_hours, 2),
        "latest_state": latest_data,
        "fuel_intelligence": {
            "estimated_endurance_hours":
                estimated_endurance_hours,
            "calculation_method": (
                "remaining_liters / consumption_lph"
            ),
            "assumption": (
                "Constant reported fuel consumption; "
                "no reserve deduction or weather adjustment."
            ),
        },
    }


# ============================================================
# ENGINE & FUEL PERFORMANCE INTELLIGENCE
# ============================================================

@router.get(
    "/vessels/{twin_id}/intelligence/performance",
    summary="Vessel Engine and Fuel Performance Intelligence",
)
def twin_performance_intelligence(
    twin_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:
        vessel = conn.execute(
            text("""
                SELECT id, vessel_name
                FROM digital_twins
                WHERE id = :twin_id
                  AND tenant_id = :tenant_id
            """),
            {
                "twin_id": twin_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if vessel is None:
            raise HTTPException(
                status_code=404,
                detail="Vessel Digital Twin not found.",
            )

        metrics = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS snapshot_count,
                    MIN(recorded_at) AS first_recorded_at,
                    MAX(recorded_at) AS last_recorded_at,

                    AVG(main_engine_rpm)
                        AS average_main_engine_rpm,

                    MAX(main_engine_rpm)
                        AS maximum_main_engine_rpm,

                    AVG(main_engine_load_percent)
                        AS average_main_engine_load_percent,

                    AVG(auxiliary_engine_load_percent)
                        AS average_auxiliary_engine_load_percent,

                    AVG(fuel_consumption_lph)
                        AS average_fuel_consumption_lph,

                    MIN(fuel_remaining_liters)
                        AS minimum_fuel_remaining_liters,

                    AVG(speed_knots)
                        AS average_speed_knots,

                    AVG(engine_temperature_c)
                        AS average_engine_temperature_c,

                    AVG(engine_oil_pressure_bar)
                        AS average_engine_oil_pressure_bar

                FROM (
                    SELECT
                        recorded_at,
                        main_engine_rpm,
                        main_engine_load_percent,
                        auxiliary_engine_load_percent,
                        fuel_consumption_lph,
                        fuel_remaining_liters,
                        speed_knots,
                        engine_temperature_c,
                        engine_oil_pressure_bar
                    FROM digital_twin_snapshots
                    WHERE tenant_id = :tenant_id
                      AND twin_id = :twin_id
                    ORDER BY recorded_at DESC, id DESC
                    LIMIT :limit
                ) AS recent_snapshots
            """),
            {
                "tenant_id": tenant_id,
                "twin_id": twin_id,
                "limit": limit,
            },
        ).mappings().one()

    return {
        "module": "Digital Twin Performance Intelligence",
        "twin_id": str(twin_id),
        "vessel_name": vessel["vessel_name"],
        "metrics": twin_serialize_row(metrics),
        "sample_limit": limit,
        "generated_at": datetime.now(UTC).isoformat(),
        "notes": [
            "Averages are calculated from available snapshots.",
            "Irregular reporting intervals are not time-weighted.",
            "Fuel consumption is a reported rate, not a verified total.",
            "Engine operating limits require vessel-specific data.",
        ],
    }


# ============================================================
# FLEET LATEST OPERATIONAL STATES
# ============================================================

@router.get(
    "/intelligence/fleet-live-state",
    summary="Fleet Latest Operational State Intelligence",
)
def twin_fleet_latest_state(
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    t.id AS twin_id,
                    t.vessel_name,
                    t.vessel_type,
                    t.twin_status,
                    s.recorded_at,
                    s.data_source,
                    s.latitude,
                    s.longitude,
                    s.speed_knots,
                    s.main_engine_rpm,
                    s.fuel_remaining_liters,
                    s.fuel_consumption_lph
                FROM digital_twins t
                LEFT JOIN LATERAL (
                    SELECT
                        recorded_at,
                        data_source,
                        latitude,
                        longitude,
                        speed_knots,
                        main_engine_rpm,
                        fuel_remaining_liters,
                        fuel_consumption_lph
                    FROM digital_twin_snapshots
                    WHERE tenant_id = t.tenant_id
                      AND twin_id = t.id
                    ORDER BY recorded_at DESC, id DESC
                    LIMIT 1
                ) s ON TRUE
                WHERE t.tenant_id = :tenant_id
                ORDER BY t.vessel_name, t.id
                LIMIT :limit
            """),
            {
                "tenant_id": tenant_id,
                "limit": limit,
            },
        ).mappings().all()

    now = datetime.now(UTC)
    vessels = []

    for row in rows:
        item = twin_serialize_row(row)
        recorded_at = row["recorded_at"]

        if recorded_at is None:
            item["data_status"] = "no_snapshots"
            item["snapshot_age_hours"] = None
        else:
            if recorded_at.tzinfo is None:
                recorded_at = recorded_at.replace(
                    tzinfo=UTC
                )

            age_hours = max(
                0.0,
                (now - recorded_at).total_seconds() / 3600.0,
            )

            item["snapshot_age_hours"] = round(
                age_hours,
                2,
            )

            item["data_status"] = (
                "recent"
                if age_hours <= 24
                else "stale"
            )

        vessels.append(item)

    return {
        "module": "Fleet Operational State Intelligence",
        "count": len(vessels),
        "vessels": vessels,
        "generated_at": now.isoformat(),
        "note": (
            "Latest available snapshots are displayed. "
            "This endpoint does not guarantee "
            "real-time sensor connectivity."
        ),
    }


# ============================================================
# END OF DIGITAL TWIN BLOK 3
# ============================================================

# ============================================================
# MQEIP - MARITIME DIGITAL TWIN INTELLIGENCE
# BLOK 4 - VESSEL EVENTS, RISK & CONDITION ASSESSMENT
# ============================================================


# ============================================================
# EVENT SERIALIZATION
# ============================================================

def serialize_twin_event(row):
    item = twin_serialize_row(row)
    raw = item.pop("event_data_json", None)

    try:
        item["event_data"] = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        item["event_data"] = {}

    return item


# ============================================================
# REGISTER VESSEL EVENT
# ============================================================

@router.post(
    "/vessels/{twin_id}/events",
    status_code=status.HTTP_201_CREATED,
    summary="Register Vessel Digital Twin Event",
)
def create_twin_event(
    twin_id: UUID,
    payload: TwinEventCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)
    event_id = uuid4()
    now = datetime.now(UTC)

    with get_engine().begin() as conn:
        vessel = conn.execute(
            text("""
                SELECT id, vessel_name
                FROM digital_twins
                WHERE id = :twin_id
                  AND tenant_id = :tenant_id
            """),
            {
                "twin_id": twin_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if vessel is None:
            raise HTTPException(
                status_code=404,
                detail="Vessel Digital Twin not found.",
            )

        conn.execute(
            text("""
                INSERT INTO digital_twin_events (
                    id,
                    tenant_id,
                    twin_id,
                    event_type,
                    severity,
                    description,
                    source_module,
                    source_record_id,
                    event_data_json,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :twin_id,
                    :event_type,
                    :severity,
                    :description,
                    :source_module,
                    :source_record_id,
                    :event_data_json,
                    :created_by,
                    :created_at
                )
            """),
            {
                "id": event_id,
                "tenant_id": tenant_id,
                "twin_id": twin_id,
                "event_type": payload.event_type,
                "severity": payload.severity,
                "description": payload.description,
                "source_module": payload.source_module,
                "source_record_id": payload.source_record_id,
                "event_data_json": twin_json(
                    payload.event_data
                ),
                "created_by": str(identity.user_id),
                "created_at": now,
            },
        )

        record_twin_audit(
            conn,
            identity,
            "vessel_event",
            event_id,
            "created",
            twin_json({
                "twin_id": str(twin_id),
                "event_type": payload.event_type,
                "severity": payload.severity,
            }),
        )

    return {
        "event_id": str(event_id),
        "twin_id": str(twin_id),
        "vessel_name": vessel["vessel_name"],
        "event_type": payload.event_type,
        "severity": payload.severity,
        "status": "recorded",
        "created_at": now.isoformat(),
    }


# ============================================================
# LIST VESSEL EVENTS
# ============================================================

@router.get(
    "/vessels/{twin_id}/events",
    summary="List Vessel Digital Twin Events",
)
def list_twin_events(
    twin_id: UUID,
    severity: Optional[
        Literal["info", "low", "medium", "high", "critical"]
    ] = None,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:
        vessel = conn.execute(
            text("""
                SELECT id, vessel_name
                FROM digital_twins
                WHERE id = :twin_id
                  AND tenant_id = :tenant_id
            """),
            {
                "twin_id": twin_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if vessel is None:
            raise HTTPException(
                status_code=404,
                detail="Vessel Digital Twin not found.",
            )

        rows = conn.execute(
            text("""
                SELECT *
                FROM digital_twin_events
                WHERE twin_id = :twin_id
                  AND tenant_id = :tenant_id
                  AND (
                      :severity IS NULL
                      OR severity = :severity
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
            """),
            {
                "twin_id": twin_id,
                "tenant_id": tenant_id,
                "severity": severity,
                "limit": limit,
            },
        ).mappings().all()

    return {
        "twin_id": str(twin_id),
        "vessel_name": vessel["vessel_name"],
        "count": len(rows),
        "events": [
            serialize_twin_event(row)
            for row in rows
        ],
    }


# ============================================================
# VESSEL RISK INTELLIGENCE
# ============================================================

@router.get(
    "/vessels/{twin_id}/intelligence/risk",
    summary="Vessel Digital Twin Risk Intelligence",
)
def twin_vessel_risk_intelligence(
    twin_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)
    now = datetime.now(UTC)

    with get_engine().connect() as conn:
        vessel = conn.execute(
            text("""
                SELECT
                    id,
                    vessel_name,
                    vessel_type,
                    twin_status
                FROM digital_twins
                WHERE id = :twin_id
                  AND tenant_id = :tenant_id
            """),
            {
                "twin_id": twin_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if vessel is None:
            raise HTTPException(
                status_code=404,
                detail="Vessel Digital Twin not found.",
            )

        events = conn.execute(
            text("""
                SELECT
                    severity,
                    COUNT(*) AS event_count
                FROM digital_twin_events
                WHERE twin_id = :twin_id
                  AND tenant_id = :tenant_id
                  AND created_at >= :cutoff
                GROUP BY severity
            """),
            {
                "twin_id": twin_id,
                "tenant_id": tenant_id,
                "cutoff": now - __import__(
                    "datetime"
                ).timedelta(days=30),
            },
        ).mappings().all()

        latest = conn.execute(
            text("""
                SELECT
                    recorded_at,
                    main_engine_load_percent,
                    engine_temperature_c,
                    engine_oil_pressure_bar,
                    fuel_remaining_liters
                FROM digital_twin_snapshots
                WHERE twin_id = :twin_id
                  AND tenant_id = :tenant_id
                ORDER BY recorded_at DESC, id DESC
                LIMIT 1
            """),
            {
                "twin_id": twin_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

    severity_weights = {
        "info": 0,
        "low": 2,
        "medium": 5,
        "high": 12,
        "critical": 25,
    }

    event_counts = {
        row["severity"]: row["event_count"]
        for row in events
    }

    event_score = sum(
        severity_weights.get(level, 0) * count
        for level, count in event_counts.items()
    )

    data_warnings = []

    if latest is None:
        data_warnings.append(
            "No operational snapshot is available."
        )
    else:
        recorded_at = latest["recorded_at"]

        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=UTC)

        age_hours = (
            now - recorded_at
        ).total_seconds() / 3600.0

        if age_hours > 24:
            data_warnings.append(
                "Latest operational snapshot is older than 24 hours."
            )

        if latest["main_engine_load_percent"] is None:
            data_warnings.append(
                "Main engine load data is unavailable."
            )

        if latest["engine_temperature_c"] is None:
            data_warnings.append(
                "Engine temperature data is unavailable."
            )

        if latest["engine_oil_pressure_bar"] is None:
            data_warnings.append(
                "Engine oil pressure data is unavailable."
            )

    risk_score = min(100, event_score)

    if risk_score >= 75:
        risk_level = "critical"
    elif risk_score >= 40:
        risk_level = "high"
    elif risk_score >= 15:
        risk_level = "medium"
    else:
        risk_level = "low"

    return {
        "module": "Vessel Digital Twin Risk Intelligence",
        "twin_id": str(twin_id),
        "vessel_name": vessel["vessel_name"],
        "twin_status": vessel["twin_status"],
        "risk_score": risk_score,
        "risk_level": risk_level,
        "event_counts_last_30_days": event_counts,
        "data_quality_warnings": data_warnings,
        "assessment_method": (
            "Preliminary event-severity scoring"
        ),
        "assessment_scope": "recorded_events_only",
        "generated_at": now.isoformat(),
        "disclaimer": (
            "This is a preliminary event-based indicator, "
            "not a certified maritime safety assessment. "
            "Missing telemetry does not imply low risk."
        ),
    }


# ============================================================
# ENGINE CONDITION ASSESSMENT
# ============================================================

@router.get(
    "/vessels/{twin_id}/intelligence/condition",
    summary="Vessel Engine Condition Assessment",
)
def twin_engine_condition_assessment(
    twin_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:
        vessel = conn.execute(
            text("""
                SELECT
                    id,
                    vessel_name,
                    vessel_type,
                    main_engine_total_power_kw
                FROM digital_twins
                WHERE id = :twin_id
                  AND tenant_id = :tenant_id
            """),
            {
                "twin_id": twin_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

        if vessel is None:
            raise HTTPException(
                status_code=404,
                detail="Vessel Digital Twin not found.",
            )

        latest = conn.execute(
            text("""
                SELECT
                    recorded_at,
                    main_engine_rpm,
                    main_engine_load_percent,
                    auxiliary_engine_load_percent,
                    engine_temperature_c,
                    engine_oil_pressure_bar,
                    fuel_consumption_lph
                FROM digital_twin_snapshots
                WHERE twin_id = :twin_id
                  AND tenant_id = :tenant_id
                ORDER BY recorded_at DESC, id DESC
                LIMIT 1
            """),
            {
                "twin_id": twin_id,
                "tenant_id": tenant_id,
            },
        ).mappings().first()

    if latest is None:
        return {
            "twin_id": str(twin_id),
            "vessel_name": vessel["vessel_name"],
            "condition_status": "insufficient_data",
            "latest_metrics": None,
            "warnings": [
                "No engine operational snapshot is available."
            ],
        }

    metrics = twin_serialize_row(latest)
    warnings = []

    if latest["main_engine_rpm"] is None:
        warnings.append("Main engine RPM is missing.")

    if latest["main_engine_load_percent"] is None:
        warnings.append("Main engine load is missing.")

    if latest["engine_temperature_c"] is None:
        warnings.append("Engine temperature is missing.")

    if latest["engine_oil_pressure_bar"] is None:
        warnings.append("Engine oil pressure is missing.")

    recorded_at = latest["recorded_at"]

    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=UTC)

    age_hours = max(
        0.0,
        (
            datetime.now(UTC) - recorded_at
        ).total_seconds() / 3600.0,
    )

    if age_hours > 24:
        warnings.append(
            "Engine condition data is older than 24 hours."
        )

    condition_status = (
        "data_incomplete"
        if warnings
        else "metrics_available"
    )

    return {
        "module": "Engine Condition Assessment",
        "twin_id": str(twin_id),
        "vessel_name": vessel["vessel_name"],
        "condition_status": condition_status,
        "snapshot_age_hours": round(age_hours, 2),
        "latest_metrics": metrics,
        "warnings": warnings,
        "assessment_method": (
            "Telemetry completeness and freshness check"
        ),
        "note": (
            "Engine health is not classified as safe or "
            "unsafe without manufacturer-specific "
            "operating limits and validated sensor data."
        ),
    }


# ============================================================
# FLEET EVENT INTELLIGENCE DASHBOARD
# ============================================================

@router.get(
    "/intelligence/events",
    summary="Fleet Digital Twin Event Intelligence",
)
def twin_fleet_event_intelligence(
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    t.id AS twin_id,
                    t.vessel_name,
                    e.severity,
                    COUNT(*) AS event_count
                FROM digital_twin_events e
                JOIN digital_twins t
                  ON t.id = e.twin_id
                 AND t.tenant_id = e.tenant_id
                WHERE e.tenant_id = :tenant_id
                GROUP BY
                    t.id,
                    t.vessel_name,
                    e.severity
                ORDER BY
                    t.vessel_name,
                    e.severity
                LIMIT :limit
            """),
            {
                "tenant_id": tenant_id,
                "limit": limit,
            },
        ).mappings().all()

    groups = [
        twin_serialize_row(row)
        for row in rows
    ]

    return {
        "module": "Fleet Digital Twin Event Intelligence",
        "group_count": len(groups),
        "groups": groups,
        "generated_at": datetime.now(UTC).isoformat(),
    }


# ============================================================
# END OF DIGITAL TWIN BLOK 4
# ============================================================

# ============================================================
# MQEIP - MARITIME DIGITAL TWIN INTELLIGENCE
# BLOK 5 - EXECUTIVE DASHBOARD, AUDIT & SYSTEM STATUS
# ============================================================


# ============================================================
# DIGITAL TWIN EXECUTIVE DASHBOARD
# ============================================================

@router.get(
    "/executive/dashboard",
    summary="Digital Twin Executive Dashboard",
)
def digital_twin_executive_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)
    now = datetime.now(UTC)

    with get_engine().connect() as conn:

        fleet = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_twins,
                    COUNT(*) FILTER (
                        WHERE twin_status = 'active'
                    ) AS active_twins,
                    COUNT(*) FILTER (
                        WHERE twin_status = 'registered'
                    ) AS registered_twins,
                    COUNT(*) FILTER (
                        WHERE twin_status = 'maintenance'
                    ) AS maintenance_twins,
                    COUNT(*) FILTER (
                        WHERE twin_status = 'inactive'
                    ) AS inactive_twins
                FROM digital_twins
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": tenant_id},
        ).mappings().one()

        snapshots = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_snapshots,
                    COUNT(DISTINCT twin_id)
                        AS vessels_with_snapshots,
                    MAX(recorded_at) AS latest_recorded_at
                FROM digital_twin_snapshots
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": tenant_id},
        ).mappings().one()

        events = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_events,
                    COUNT(*) FILTER (
                        WHERE severity = 'critical'
                    ) AS critical_events,
                    COUNT(*) FILTER (
                        WHERE severity = 'high'
                    ) AS high_events,
                    COUNT(*) FILTER (
                        WHERE severity = 'medium'
                    ) AS medium_events
                FROM digital_twin_events
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": tenant_id},
        ).mappings().one()

        recent = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS recent_vessels
                FROM digital_twins t
                WHERE t.tenant_id = :tenant_id
                  AND EXISTS (
                      SELECT 1
                      FROM digital_twin_snapshots s
                      WHERE s.tenant_id = t.tenant_id
                        AND s.twin_id = t.id
                        AND s.recorded_at >= :cutoff
                  )
            """),
            {
                "tenant_id": tenant_id,
                "cutoff": now - __import__(
                    "datetime"
                ).timedelta(hours=24),
            },
        ).mappings().one()

    total_twins = fleet["total_twins"]
    recent_vessels = recent["recent_vessels"]

    coverage_percent = (
        round(
            recent_vessels / total_twins * 100,
            2,
        )
        if total_twins
        else 0.0
    )

    return {
        "platform": "MQEIP",
        "module": "Maritime Digital Twin Intelligence",
        "fleet_registry": twin_serialize_row(fleet),
        "operational_snapshots":
            twin_serialize_row(snapshots),
        "recorded_events": twin_serialize_row(events),
        "data_freshness": {
            "vessels_with_recent_snapshots":
                recent_vessels,
            "recent_snapshot_coverage_percent":
                coverage_percent,
            "freshness_window_hours": 24,
        },
        "generated_at": now.isoformat(),
        "note": (
            "Dashboard summarizes registered Digital Twins "
            "and submitted operational records. "
            "It does not establish real-time connectivity "
            "or verified vessel condition."
        ),
    }


# ============================================================
# DIGITAL TWIN DATA QUALITY INTELLIGENCE
# ============================================================

@router.get(
    "/intelligence/data-quality",
    summary="Digital Twin Data Quality Intelligence",
)
def digital_twin_data_quality(
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)
    now = datetime.now(UTC)

    with get_engine().connect() as conn:

        rows = conn.execute(
            text("""
                SELECT
                    t.id AS twin_id,
                    t.vessel_name,
                    t.twin_status,
                    s.recorded_at,
                    s.data_source,
                    s.main_engine_rpm,
                    s.main_engine_load_percent,
                    s.fuel_remaining_liters,
                    s.fuel_consumption_lph,
                    s.latitude,
                    s.longitude
                FROM digital_twins t
                LEFT JOIN LATERAL (
                    SELECT
                        recorded_at,
                        data_source,
                        main_engine_rpm,
                        main_engine_load_percent,
                        fuel_remaining_liters,
                        fuel_consumption_lph,
                        latitude,
                        longitude
                    FROM digital_twin_snapshots
                    WHERE tenant_id = t.tenant_id
                      AND twin_id = t.id
                    ORDER BY recorded_at DESC, id DESC
                    LIMIT 1
                ) s ON TRUE
                WHERE t.tenant_id = :tenant_id
                ORDER BY t.vessel_name, t.id
                LIMIT :limit
            """),
            {
                "tenant_id": tenant_id,
                "limit": limit,
            },
        ).mappings().all()

    results = []

    required_fields = (
        "main_engine_rpm",
        "main_engine_load_percent",
        "fuel_remaining_liters",
        "fuel_consumption_lph",
        "latitude",
        "longitude",
    )

    for row in rows:

        if row["recorded_at"] is None:
            results.append({
                "twin_id": str(row["twin_id"]),
                "vessel_name": row["vessel_name"],
                "data_quality_status": "no_data",
                "completeness_percent": 0.0,
                "missing_fields": list(required_fields),
                "snapshot_age_hours": None,
            })
            continue

        missing_fields = [
            field
            for field in required_fields
            if row[field] is None
        ]

        completeness = round(
            (
                len(required_fields) - len(missing_fields)
            ) / len(required_fields) * 100,
            2,
        )

        recorded_at = row["recorded_at"]

        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=UTC)

        age_hours = max(
            0.0,
            (now - recorded_at).total_seconds() / 3600.0,
        )

        if age_hours > 24:
            quality_status = "stale"
        elif missing_fields:
            quality_status = "incomplete"
        else:
            quality_status = "complete"

        results.append({
            "twin_id": str(row["twin_id"]),
            "vessel_name": row["vessel_name"],
            "data_quality_status": quality_status,
            "completeness_percent": completeness,
            "missing_fields": missing_fields,
            "snapshot_age_hours": round(age_hours, 2),
            "data_source": row["data_source"],
        })

    return {
        "module": "Digital Twin Data Quality Intelligence",
        "count": len(results),
        "vessels": results,
        "generated_at": now.isoformat(),
        "note": (
            "Completeness measures field availability, "
            "not accuracy or sensor verification."
        ),
    }


# ============================================================
# DIGITAL TWIN AUDIT TRAIL
# ============================================================

@router.get(
    "/audit",
    summary="Digital Twin Audit Trail",
)
def list_digital_twin_audit(
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    identity: TokenIdentity = Depends(verify_access_token),
):
    allowed_roles = {
        "admin",
        "superadmin",
        "administrator",
        "auditor",
        "compliance",
    }

    role = str(
        getattr(identity, "role", "") or ""
    ).strip().lower()

    if role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail="Audit access requires an authorized role.",
        )

    with get_engine().connect() as conn:

        rows = conn.execute(
            text("""
                SELECT
                    id,
                    tenant_id,
                    entity_type,
                    entity_id,
                    action,
                    details,
                    actor_id,
                    created_at
                FROM digital_twin_audit_log
                WHERE tenant_id = :tenant_id
                  AND (
                      :entity_type IS NULL
                      OR entity_type = :entity_type
                  )
                  AND (
                      :entity_id IS NULL
                      OR entity_id = :entity_id
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
            """),
            {
                "tenant_id": str(identity.tenant_id),
                "entity_type": entity_type,
                "entity_id": entity_id,
                "limit": limit,
            },
        ).mappings().all()

    return {
        "module": "Digital Twin Audit Trail",
        "count": len(rows),
        "records": [
            twin_serialize_row(row)
            for row in rows
        ],
    }


# ============================================================
# DIGITAL TWIN SYSTEM STATUS
# ============================================================

@router.get(
    "/system/status",
    summary="Digital Twin System Status",
)
def digital_twin_system_status(
    identity: TokenIdentity = Depends(verify_access_token),
):
    tenant_id = str(identity.tenant_id)

    with get_engine().connect() as conn:

        result = conn.execute(
            text("""
                SELECT
                    (SELECT COUNT(*)
                     FROM digital_twins
                     WHERE tenant_id = :tenant_id)
                        AS twin_count,

                    (SELECT COUNT(*)
                     FROM digital_twin_snapshots
                     WHERE tenant_id = :tenant_id)
                        AS snapshot_count,

                    (SELECT COUNT(*)
                     FROM digital_twin_events
                     WHERE tenant_id = :tenant_id)
                        AS event_count
            """),
            {
                "tenant_id": tenant_id,
            },
        ).mappings().one()

    return {
        "platform": "MQEIP",
        "module": "Maritime Digital Twin Intelligence",
        "database_status": "connected",
        "registry": twin_serialize_row(result),
        "capabilities": {
            "vessel_twin_registry": True,
            "operational_snapshots": True,
            "engine_and_fuel_reporting": True,
            "event_registry": True,
            "preliminary_risk_scoring": True,
            "data_quality_monitoring": True,
            "audit_trail": True,
            "live_sensor_connection": False,
            "validated_physics_simulation": False,
            "autonomous_vessel_control": False,
        },
        "generated_at": datetime.now(UTC).isoformat(),
    }


# ============================================================
# INITIALIZE DIGITAL TWIN DATABASE TABLES
# ============================================================

init_digital_twin_tables()


# ============================================================
# END OF DIGITAL TWIN BLOK 5
# ============================================================

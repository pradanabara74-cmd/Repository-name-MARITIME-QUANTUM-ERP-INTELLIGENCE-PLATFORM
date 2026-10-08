# ============================================================
# MARITIME QUANTUM ERP & INTELLIGENCE PLATFORM
# ESG & SUSTAINABILITY INTELLIGENCE
# BLOK 1 - CORE MODELS & DATABASE
# ============================================================

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..core.database import get_engine
from ..core.security import (
    TokenIdentity,
    verify_access_token,
)


router = APIRouter(
    prefix="/api/v1/esg",
    tags=["ESG & Sustainability Intelligence"],
)


# ============================================================
# DATA MODELS
# ============================================================

class EmissionRecordCreate(BaseModel):
    vessel_id: str = Field(min_length=1, max_length=100)
    vessel_name: str = Field(min_length=1, max_length=255)
    report_date: date
    fuel_type: str = Field(min_length=1, max_length=100)
    fuel_consumed_mt: Decimal = Field(ge=0)
    co2_emission_mt: Decimal = Field(ge=0)
    distance_nm: Decimal = Field(ge=0)
    cargo_carried_mt: Optional[Decimal] = Field(
        default=None,
        ge=0,
    )
    voyage_reference: Optional[str] = Field(
        default=None,
        max_length=100,
    )
    remarks: Optional[str] = None


class EnergyRecordCreate(BaseModel):
    vessel_id: str = Field(min_length=1, max_length=100)
    vessel_name: str = Field(min_length=1, max_length=255)
    report_date: date
    energy_source: str = Field(min_length=1, max_length=100)
    energy_consumed_kwh: Decimal = Field(ge=0)
    renewable_energy_kwh: Decimal = Field(ge=0)
    remarks: Optional[str] = None

    @model_validator(mode="after")
    def validate_energy(self):
        if self.renewable_energy_kwh > self.energy_consumed_kwh:
            raise ValueError(
                "Renewable energy cannot exceed total energy."
            )
        return self


class EnvironmentalIncidentCreate(BaseModel):
    vessel_id: Optional[str] = Field(
        default=None,
        max_length=100,
    )
    vessel_name: Optional[str] = Field(
        default=None,
        max_length=255,
    )
    incident_date: date
    incident_type: str = Field(min_length=1, max_length=100)
    severity: str = Field(min_length=1, max_length=30)
    description: str = Field(min_length=1)
    estimated_spill_liters: Decimal = Field(
        default=Decimal("0"),
        ge=0,
    )
    corrective_action: Optional[str] = None


class ESGTargetCreate(BaseModel):
    target_name: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=100)
    metric_name: str = Field(min_length=1, max_length=100)
    baseline_value: Decimal
    target_value: Decimal
    target_year: int = Field(ge=2000, le=2200)
    unit: str = Field(min_length=1, max_length=50)
    responsible_department: Optional[str] = Field(
        default=None,
        max_length=255,
    )
    remarks: Optional[str] = None


class ESGTargetProgressUpdate(BaseModel):
    current_value: Decimal
    remarks: Optional[str] = None


# ============================================================
# SERIALIZATION
# ============================================================

def serialize_esg_row(row):
    if row is None:
        return None

    result = dict(row)

    for key, value in result.items():
        if isinstance(value, UUID):
            result[key] = str(value)
        elif isinstance(value, Decimal):
            result[key] = float(value)
        elif isinstance(value, (date, datetime)):
            result[key] = value.isoformat()

    return result


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_esg_tables():
    engine = get_engine()

    with engine.begin() as conn:

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS esg_emissions (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(100) NOT NULL,
                vessel_id VARCHAR(100) NOT NULL,
                vessel_name VARCHAR(255) NOT NULL,
                report_date DATE NOT NULL,
                fuel_type VARCHAR(100) NOT NULL,
                fuel_consumed_mt NUMERIC(18,4) NOT NULL,
                co2_emission_mt NUMERIC(18,4) NOT NULL,
                distance_nm NUMERIC(18,4) NOT NULL,
                cargo_carried_mt NUMERIC(18,4),
                voyage_reference VARCHAR(100),
                remarks TEXT,
                created_by VARCHAR(100) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS esg_energy (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(100) NOT NULL,
                vessel_id VARCHAR(100) NOT NULL,
                vessel_name VARCHAR(255) NOT NULL,
                report_date DATE NOT NULL,
                energy_source VARCHAR(100) NOT NULL,
                energy_consumed_kwh NUMERIC(18,4) NOT NULL,
                renewable_energy_kwh NUMERIC(18,4) NOT NULL,
                remarks TEXT,
                created_by VARCHAR(100) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS esg_environmental_incidents (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(100) NOT NULL,
                vessel_id VARCHAR(100),
                vessel_name VARCHAR(255),
                incident_date DATE NOT NULL,
                incident_type VARCHAR(100) NOT NULL,
                severity VARCHAR(30) NOT NULL,
                description TEXT NOT NULL,
                estimated_spill_liters NUMERIC(18,4)
                    NOT NULL DEFAULT 0,
                corrective_action TEXT,
                status VARCHAR(30) NOT NULL DEFAULT 'open',
                created_by VARCHAR(100) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS esg_targets (
                id UUID PRIMARY KEY,
                tenant_id VARCHAR(100) NOT NULL,
                target_name VARCHAR(255) NOT NULL,
                category VARCHAR(100) NOT NULL,
                metric_name VARCHAR(100) NOT NULL,
                baseline_value NUMERIC(18,4) NOT NULL,
                target_value NUMERIC(18,4) NOT NULL,
                current_value NUMERIC(18,4),
                target_year INTEGER NOT NULL,
                unit VARCHAR(50) NOT NULL,
                responsible_department VARCHAR(255),
                remarks TEXT,
                created_by VARCHAR(100) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_esg_emissions
            ON esg_emissions (
                tenant_id, vessel_id, report_date
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_esg_energy
            ON esg_energy (
                tenant_id, vessel_id, report_date
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_esg_incidents
            ON esg_environmental_incidents (
                tenant_id, status, incident_date
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_esg_targets
            ON esg_targets (
                tenant_id, category, target_year
            )
        """))

  # ============================================================
# ESG BLOK 2 - VESSEL EMISSIONS & ENERGY INTELLIGENCE
# ============================================================


# ============================================================
# EMISSION RECORDS
# ============================================================

@router.get(
    "/emissions",
    summary="List Vessel Emission Records",
)
def list_emission_records(
    vessel_id: Optional[str] = None,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM esg_emissions
                WHERE tenant_id = :tenant_id
                  AND (
                      :vessel_id IS NULL
                      OR vessel_id = :vessel_id
                  )
                ORDER BY report_date DESC, created_at DESC
                LIMIT 1000
            """),
            {
                "tenant_id": identity.tenant_id,
                "vessel_id": vessel_id,
            },
        ).mappings().all()

    return [
        serialize_esg_row(row)
        for row in rows
    ]


@router.post(
    "/emissions",
    status_code=status.HTTP_201_CREATED,
    summary="Register Vessel Emission Record",
)
def create_emission_record(
    payload: EmissionRecordCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    record_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO esg_emissions (
                    id,
                    tenant_id,
                    vessel_id,
                    vessel_name,
                    report_date,
                    fuel_type,
                    fuel_consumed_mt,
                    co2_emission_mt,
                    distance_nm,
                    cargo_carried_mt,
                    voyage_reference,
                    remarks,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :vessel_id,
                    :vessel_name,
                    :report_date,
                    :fuel_type,
                    :fuel_consumed_mt,
                    :co2_emission_mt,
                    :distance_nm,
                    :cargo_carried_mt,
                    :voyage_reference,
                    :remarks,
                    :created_by,
                    :created_at
                )
            """),
            {
                "id": record_id,
                "tenant_id": identity.tenant_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "report_date": payload.report_date,
                "fuel_type": payload.fuel_type,
                "fuel_consumed_mt": payload.fuel_consumed_mt,
                "co2_emission_mt": payload.co2_emission_mt,
                "distance_nm": payload.distance_nm,
                "cargo_carried_mt": payload.cargo_carried_mt,
                "voyage_reference": payload.voyage_reference,
                "remarks": payload.remarks,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

    return {
        "id": str(record_id),
        "vessel_id": payload.vessel_id,
        "report_date": payload.report_date.isoformat(),
        "co2_emission_mt": float(payload.co2_emission_mt),
        "status": "recorded",
    }


@router.get(
    "/emissions/intelligence",
    summary="Vessel Emission Performance Intelligence",
)
def emission_intelligence(
    vessel_id: Optional[str] = None,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    vessel_id,
                    MAX(vessel_name) AS vessel_name,
                    COUNT(*) AS report_count,
                    SUM(fuel_consumed_mt)
                        AS total_fuel_mt,
                    SUM(co2_emission_mt)
                        AS total_co2_mt,
                    SUM(distance_nm)
                        AS total_distance_nm,
                    SUM(cargo_carried_mt)
                        AS total_cargo_mt,
                    SUM(
                        CASE
                            WHEN cargo_carried_mt > 0
                             AND distance_nm > 0
                            THEN co2_emission_mt
                            ELSE 0
                        END
                    ) AS cargo_voyage_co2_mt,
                    SUM(
                        CASE
                            WHEN cargo_carried_mt > 0
                             AND distance_nm > 0
                            THEN cargo_carried_mt * distance_nm
                            ELSE 0
                        END
                    ) AS transport_work_mt_nm
                FROM esg_emissions
                WHERE tenant_id = :tenant_id
                  AND (
                      :vessel_id IS NULL
                      OR vessel_id = :vessel_id
                  )
                GROUP BY vessel_id
                ORDER BY total_co2_mt DESC
            """),
            {
                "tenant_id": identity.tenant_id,
                "vessel_id": vessel_id,
            },
        ).mappings().all()

    results = []

    for row in rows:
        data = serialize_esg_row(row)

        fuel = float(data["total_fuel_mt"] or 0)
        co2 = float(data["total_co2_mt"] or 0)
        distance = float(data["total_distance_nm"] or 0)
        transport_work = float(
            data["transport_work_mt_nm"] or 0
        )
        cargo_co2 = float(
            data["cargo_voyage_co2_mt"] or 0
        )

        data["co2_per_nm"] = (
            round(co2 / distance, 6)
            if distance > 0
            else None
        )

        data["fuel_per_nm"] = (
            round(fuel / distance, 6)
            if distance > 0
            else None
        )

        data["co2_per_tonne_nm"] = (
            round(cargo_co2 / transport_work, 8)
            if transport_work > 0
            else None
        )

        data["intensity_unit"] = (
            "metric tonnes CO2 per tonne-nautical-mile"
        )

        results.append(data)

    return {
        "module": "Vessel Emission Intelligence",
        "vessel_count": len(results),
        "vessels": results,
        "note": (
            "Calculated from reported records. "
            "These indicators are not certified "
            "IMO CII or EU MRV compliance results."
        ),
    }


# ============================================================
# ENERGY RECORDS
# ============================================================

@router.get(
    "/energy",
    summary="List Vessel Energy Records",
)
def list_energy_records(
    vessel_id: Optional[str] = None,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM esg_energy
                WHERE tenant_id = :tenant_id
                  AND (
                      :vessel_id IS NULL
                      OR vessel_id = :vessel_id
                  )
                ORDER BY report_date DESC, created_at DESC
                LIMIT 1000
            """),
            {
                "tenant_id": identity.tenant_id,
                "vessel_id": vessel_id,
            },
        ).mappings().all()

    return [
        serialize_esg_row(row)
        for row in rows
    ]


@router.post(
    "/energy",
    status_code=status.HTTP_201_CREATED,
    summary="Register Vessel Energy Record",
)
def create_energy_record(
    payload: EnergyRecordCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    record_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO esg_energy (
                    id,
                    tenant_id,
                    vessel_id,
                    vessel_name,
                    report_date,
                    energy_source,
                    energy_consumed_kwh,
                    renewable_energy_kwh,
                    remarks,
                    created_by,
                    created_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :vessel_id,
                    :vessel_name,
                    :report_date,
                    :energy_source,
                    :energy_consumed_kwh,
                    :renewable_energy_kwh,
                    :remarks,
                    :created_by,
                    :created_at
                )
            """),
            {
                "id": record_id,
                "tenant_id": identity.tenant_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "report_date": payload.report_date,
                "energy_source": payload.energy_source,
                "energy_consumed_kwh":
                    payload.energy_consumed_kwh,
                "renewable_energy_kwh":
                    payload.renewable_energy_kwh,
                "remarks": payload.remarks,
                "created_by": identity.user_id,
                "created_at": now,
            },
        )

    return {
        "id": str(record_id),
        "vessel_id": payload.vessel_id,
        "energy_consumed_kwh": float(
            payload.energy_consumed_kwh
        ),
        "status": "recorded",
    }


@router.get(
    "/energy/intelligence",
    summary="Fleet Energy Efficiency Intelligence",
)
def energy_intelligence(
    vessel_id: Optional[str] = None,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    vessel_id,
                    MAX(vessel_name) AS vessel_name,
                    COUNT(*) AS report_count,
                    SUM(energy_consumed_kwh)
                        AS total_energy_kwh,
                    SUM(renewable_energy_kwh)
                        AS renewable_energy_kwh
                FROM esg_energy
                WHERE tenant_id = :tenant_id
                  AND (
                      :vessel_id IS NULL
                      OR vessel_id = :vessel_id
                  )
                GROUP BY vessel_id
                ORDER BY total_energy_kwh DESC
            """),
            {
                "tenant_id": identity.tenant_id,
                "vessel_id": vessel_id,
            },
        ).mappings().all()

    results = []

    for row in rows:
        data = serialize_esg_row(row)

        total = float(
            data["total_energy_kwh"] or 0
        )
        renewable = float(
            data["renewable_energy_kwh"] or 0
        )

        data["renewable_energy_percentage"] = (
            round((renewable / total) * 100, 2)
            if total > 0
            else None
        )

        results.append(data)

    return {
        "module": "Fleet Energy Intelligence",
        "vessel_count": len(results),
        "vessels": results,
    }

# ============================================================
# ESG BLOK 3 - ENVIRONMENTAL INCIDENTS & ESG TARGETS
# ============================================================

# ============================================================
# ENVIRONMENTAL INCIDENT MANAGEMENT
# ============================================================

@router.get(
    "/incidents",
    summary="List Environmental Incidents",
)
def list_environmental_incidents(
    vessel_id: Optional[str] = None,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM esg_environmental_incidents
                WHERE tenant_id = :tenant_id
                  AND (
                      :vessel_id IS NULL
                      OR vessel_id = :vessel_id
                  )
                ORDER BY incident_date DESC, created_at DESC
                LIMIT 1000
            """),
            {
                "tenant_id": identity.tenant_id,
                "vessel_id": vessel_id,
            },
        ).mappings().all()

    return [
        serialize_esg_row(row)
        for row in rows
    ]


@router.post(
    "/incidents",
    status_code=status.HTTP_201_CREATED,
    summary="Register Environmental Incident",
)
def create_environmental_incident(
    payload: EnvironmentalIncidentCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    severity = payload.severity.strip().lower()

    if severity not in {
        "low", "medium", "high", "critical"
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid environmental incident severity.",
        )

    engine = get_engine()
    incident_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO esg_environmental_incidents (
                    id,
                    tenant_id,
                    vessel_id,
                    vessel_name,
                    incident_date,
                    incident_type,
                    severity,
                    description,
                    estimated_spill_liters,
                    corrective_action,
                    status,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :vessel_id,
                    :vessel_name,
                    :incident_date,
                    :incident_type,
                    :severity,
                    :description,
                    :estimated_spill_liters,
                    :corrective_action,
                    'open',
                    :created_by,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "id": incident_id,
                "tenant_id": identity.tenant_id,
                "vessel_id": payload.vessel_id,
                "vessel_name": payload.vessel_name,
                "incident_date": payload.incident_date,
                "incident_type": payload.incident_type,
                "severity": severity,
                "description": payload.description,
                "estimated_spill_liters":
                    payload.estimated_spill_liters,
                "corrective_action":
                    payload.corrective_action,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(incident_id),
        "incident_type": payload.incident_type,
        "severity": severity,
        "status": "open",
        "created_at": now.isoformat(),
    }


@router.get(
    "/incidents/{incident_id}",
    summary="Get Environmental Incident Details",
)
def get_environmental_incident(
    incident_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text("""
                SELECT *
                FROM esg_environmental_incidents
                WHERE id = :incident_id
                  AND tenant_id = :tenant_id
            """),
            {
                "incident_id": incident_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Environmental incident not found.",
        )

    return serialize_esg_row(row)


# ============================================================
# ESG TARGET MANAGEMENT
# ============================================================

@router.get(
    "/targets",
    summary="List ESG Sustainability Targets",
)
def list_esg_targets(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM esg_targets
                WHERE tenant_id = :tenant_id
                ORDER BY target_year ASC, created_at DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    return [
        serialize_esg_row(row)
        for row in rows
    ]


@router.post(
    "/targets",
    status_code=status.HTTP_201_CREATED,
    summary="Create ESG Sustainability Target",
)
def create_esg_target(
    payload: ESGTargetCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()
    target_id = uuid4()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO esg_targets (
                    id,
                    tenant_id,
                    target_name,
                    category,
                    metric_name,
                    baseline_value,
                    target_value,
                    current_value,
                    target_year,
                    unit,
                    responsible_department,
                    remarks,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :tenant_id,
                    :target_name,
                    :category,
                    :metric_name,
                    :baseline_value,
                    :target_value,
                    :current_value,
                    :target_year,
                    :unit,
                    :responsible_department,
                    :remarks,
                    :created_by,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "id": target_id,
                "tenant_id": identity.tenant_id,
                "target_name": payload.target_name,
                "category": payload.category,
                "metric_name": payload.metric_name,
                "baseline_value": payload.baseline_value,
                "target_value": payload.target_value,
                "current_value": payload.baseline_value,
                "target_year": payload.target_year,
                "unit": payload.unit,
                "responsible_department":
                    payload.responsible_department,
                "remarks": payload.remarks,
                "created_by": identity.user_id,
                "created_at": now,
                "updated_at": now,
            },
        )

    return {
        "id": str(target_id),
        "target_name": payload.target_name,
        "baseline_value": float(payload.baseline_value),
        "target_value": float(payload.target_value),
        "current_value": float(payload.baseline_value),
        "target_year": payload.target_year,
        "status": "created",
    }


@router.get(
    "/targets/{target_id}",
    summary="Get ESG Target Details",
)
def get_esg_target(
    target_id: UUID,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text("""
                SELECT *
                FROM esg_targets
                WHERE id = :target_id
                  AND tenant_id = :tenant_id
            """),
            {
                "target_id": target_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="ESG target not found.",
        )

    return serialize_esg_row(row)


@router.post(
    "/targets/{target_id}/progress",
    summary="Update ESG Target Progress",
)
def update_esg_target_progress(
    target_id: UUID,
    payload: ESGTargetProgressUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        existing = conn.execute(
            text("""
                SELECT
                    baseline_value,
                    target_value,
                    current_value,
                    target_year
                FROM esg_targets
                WHERE id = :target_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "target_id": target_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="ESG target not found.",
            )

        conn.execute(
            text("""
                UPDATE esg_targets
                SET current_value = :current_value,
                    remarks = COALESCE(:remarks, remarks),
                    updated_at = :updated_at
                WHERE id = :target_id
                  AND tenant_id = :tenant_id
            """),
            {
                "current_value": payload.current_value,
                "remarks": payload.remarks,
                "updated_at": now,
                "target_id": target_id,
                "tenant_id": identity.tenant_id,
            },
        )

    baseline = existing["baseline_value"]
    target = existing["target_value"]
    current = payload.current_value

    if target == baseline:
        progress_percentage = (
            Decimal("100")
            if current == target
            else Decimal("0")
        )
    else:
        progress_percentage = (
            (current - baseline)
            / (target - baseline)
            * Decimal("100")
        )

    return {
        "id": str(target_id),
        "current_value": float(current),
        "target_value": float(target),
        "progress_percentage": round(
            float(progress_percentage), 2
        ),
        "updated_at": now.isoformat(),
    }

# ============================================================
# ESG BLOK 4 - EXECUTIVE SUSTAINABILITY INTELLIGENCE
# ============================================================


# ============================================================
# EXECUTIVE SUSTAINABILITY DASHBOARD
# ============================================================

@router.get(
    "/dashboard",
    summary="Executive Sustainability Dashboard",
)
def esg_executive_dashboard(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()
    params = {"tenant_id": identity.tenant_id}

    with engine.begin() as conn:
        emissions = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS report_count,
                    COUNT(DISTINCT vessel_id) AS vessel_count,
                    COALESCE(SUM(fuel_consumed_mt), 0)
                        AS total_fuel_mt,
                    COALESCE(SUM(co2_emission_mt), 0)
                        AS total_co2_mt,
                    COALESCE(SUM(distance_nm), 0)
                        AS total_distance_nm
                FROM esg_emissions
                WHERE tenant_id = :tenant_id
            """),
            params,
        ).mappings().one()

        energy = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS report_count,
                    COALESCE(SUM(energy_consumed_kwh), 0)
                        AS total_energy_kwh,
                    COALESCE(SUM(renewable_energy_kwh), 0)
                        AS renewable_energy_kwh
                FROM esg_energy
                WHERE tenant_id = :tenant_id
            """),
            params,
        ).mappings().one()

        incidents = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_incidents,
                    COUNT(*) FILTER (
                        WHERE status = 'open'
                    ) AS open_incidents,
                    COUNT(*) FILTER (
                        WHERE severity = 'critical'
                          AND status = 'open'
                    ) AS critical_open_incidents,
                    COALESCE(
                        SUM(estimated_spill_liters), 0
                    ) AS reported_spill_liters
                FROM esg_environmental_incidents
                WHERE tenant_id = :tenant_id
            """),
            params,
        ).mappings().one()

        targets = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_targets,
                    COUNT(*) FILTER (
                        WHERE current_value IS NOT NULL
                    ) AS targets_with_progress
                FROM esg_targets
                WHERE tenant_id = :tenant_id
            """),
            params,
        ).mappings().one()

    total_energy = Decimal(
        energy["total_energy_kwh"]
    )
    renewable_energy = Decimal(
        energy["renewable_energy_kwh"]
    )

    renewable_percentage = (
        round(
            float(
                renewable_energy
                / total_energy
                * Decimal("100")
            ),
            2,
        )
        if total_energy > 0
        else None
    )

    return {
        "platform": "MQEIP",
        "module": "Executive Sustainability Dashboard",
        "emissions": serialize_esg_row(emissions),
        "energy": {
            **serialize_esg_row(energy),
            "renewable_energy_percentage":
                renewable_percentage,
        },
        "environmental_incidents":
            serialize_esg_row(incidents),
        "sustainability_targets":
            serialize_esg_row(targets),
        "data_scope": "All available tenant ESG records",
        "generated_at":
            datetime.now(timezone.utc).isoformat(),
        "disclaimer": (
            "Dashboard uses reported operational data. "
            "Regulatory compliance and carbon accounting "
            "require independent validation."
        ),
    }


# ============================================================
# ENVIRONMENTAL RISK INTELLIGENCE
# ============================================================

@router.get(
    "/risk-intelligence",
    summary="Environmental Risk Intelligence",
)
def environmental_risk_intelligence(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    vessel_id,
                    MAX(vessel_name) AS vessel_name,
                    COUNT(*) AS total_incidents,
                    COUNT(*) FILTER (
                        WHERE status = 'open'
                    ) AS open_incidents,
                    COUNT(*) FILTER (
                        WHERE severity = 'critical'
                          AND status = 'open'
                    ) AS critical_incidents,
                    COUNT(*) FILTER (
                        WHERE severity = 'high'
                          AND status = 'open'
                    ) AS high_incidents,
                    COALESCE(
                        SUM(estimated_spill_liters), 0
                    ) AS estimated_spill_liters
                FROM esg_environmental_incidents
                WHERE tenant_id = :tenant_id
                GROUP BY vessel_id
                ORDER BY
                    critical_incidents DESC,
                    high_incidents DESC,
                    open_incidents DESC
            """),
            {"tenant_id": identity.tenant_id},
        ).mappings().all()

    results = []

    for row in rows:
        data = serialize_esg_row(row)

        critical = int(data["critical_incidents"])
        high = int(data["high_incidents"])
        open_count = int(data["open_incidents"])

        if critical > 0:
            risk_level = "CRITICAL"
        elif high > 0:
            risk_level = "HIGH"
        elif open_count > 0:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        data["risk_level"] = risk_level
        results.append(data)

    return {
        "module": "Environmental Risk Intelligence",
        "assessment_type": "Rule-Based",
        "vessel_risk_profiles": results,
        "note": (
            "Risk levels are operational indicators "
            "based on recorded incidents, not a "
            "certified environmental risk assessment."
        ),
    }


# ============================================================
# ESG AUDIT TRAIL
# ============================================================

def init_esg_audit_table():
    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(
            text("""
                CREATE TABLE IF NOT EXISTS esg_audit_log (
                    id UUID PRIMARY KEY,
                    tenant_id VARCHAR(100) NOT NULL,
                    entity_type VARCHAR(100) NOT NULL,
                    entity_id UUID NOT NULL,
                    action VARCHAR(100) NOT NULL,
                    details TEXT,
                    performed_by VARCHAR(100) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL
                        DEFAULT CURRENT_TIMESTAMP
                )
            """)
        )

        conn.execute(
            text("""
                CREATE INDEX IF NOT EXISTS
                    idx_esg_audit_tenant_date
                ON esg_audit_log (
                    tenant_id, created_at
                )
            """)
        )


def record_esg_audit(
    conn,
    tenant_id,
    entity_type,
    entity_id,
    action,
    performed_by,
    details=None,
):
    conn.execute(
        text("""
            INSERT INTO esg_audit_log (
                id,
                tenant_id,
                entity_type,
                entity_id,
                action,
                details,
                performed_by,
                created_at
            )
            VALUES (
                :id,
                :tenant_id,
                :entity_type,
                :entity_id,
                :action,
                :details,
                :performed_by,
                :created_at
            )
        """),
        {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "details": details,
            "performed_by": performed_by,
            "created_at": datetime.now(timezone.utc),
        },
    )


@router.get(
    "/audit-trail",
    summary="View ESG Audit Trail",
)
def get_esg_audit_trail(
    entity_type: Optional[str] = None,
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *
                FROM esg_audit_log
                WHERE tenant_id = :tenant_id
                  AND (
                      :entity_type IS NULL
                      OR entity_type = :entity_type
                  )
                ORDER BY created_at DESC
                LIMIT 1000
            """),
            {
                "tenant_id": identity.tenant_id,
                "entity_type": entity_type,
            },
        ).mappings().all()

    return {
        "module": "ESG Audit Trail",
        "record_count": len(rows),
        "records": [
            serialize_esg_row(row)
            for row in rows
        ],
    }

# ============================================================
# ESG BLOK 5 - FINAL INTEGRATION
# ============================================================

# ============================================================
# ENVIRONMENTAL INCIDENT STATUS MODEL
# ============================================================

class ESGIncidentStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=30)
    corrective_action: Optional[str] = None


# ============================================================
# ENVIRONMENTAL INCIDENT STATUS UPDATE
# ============================================================

@router.post(
    "/incidents/{incident_id}/status",
    summary="Update Environmental Incident Status",
)
def update_environmental_incident_status(
    incident_id: UUID,
    payload: ESGIncidentStatusUpdate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    allowed_statuses = {
        "open",
        "under_investigation",
        "corrective_action",
        "closed",
    }

    new_status = payload.status.strip().lower()

    if new_status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail="Invalid environmental incident status.",
        )

    engine = get_engine()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        existing = conn.execute(
            text("""
                SELECT status, corrective_action
                FROM esg_environmental_incidents
                WHERE id = :incident_id
                  AND tenant_id = :tenant_id
                FOR UPDATE
            """),
            {
                "incident_id": incident_id,
                "tenant_id": identity.tenant_id,
            },
        ).mappings().first()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="Environmental incident not found.",
            )

        conn.execute(
            text("""
                UPDATE esg_environmental_incidents
                SET status = :status,
                    corrective_action = COALESCE(
                        :corrective_action,
                        corrective_action
                    ),
                    updated_at = :updated_at
                WHERE id = :incident_id
                  AND tenant_id = :tenant_id
            """),
            {
                "status": new_status,
                "corrective_action": payload.corrective_action,
                "updated_at": now,
                "incident_id": incident_id,
                "tenant_id": identity.tenant_id,
            },
        )

        record_esg_audit(
            conn=conn,
            tenant_id=identity.tenant_id,
            entity_type="environmental_incident",
            entity_id=incident_id,
            action="status_updated",
            performed_by=identity.user_id,
            details=(
                f"Status changed from "
                f"{existing['status']} to {new_status}"
            ),
        )

    return {
        "id": str(incident_id),
        "previous_status": existing["status"],
        "current_status": new_status,
        "updated_at": now.isoformat(),
    }


# ============================================================
# ESG DATA COMPLETENESS INTELLIGENCE
# ============================================================

@router.get(
    "/data-quality",
    summary="ESG Data Completeness Intelligence",
)
def esg_data_quality(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()
    params = {"tenant_id": identity.tenant_id}

    with engine.begin() as conn:
        emissions = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_records,
                    COUNT(*) FILTER (
                        WHERE voyage_reference IS NULL
                    ) AS missing_voyage_reference,
                    COUNT(*) FILTER (
                        WHERE distance_nm = 0
                    ) AS zero_distance_records,
                    COUNT(*) FILTER (
                        WHERE cargo_carried_mt IS NULL
                    ) AS missing_cargo_data
                FROM esg_emissions
                WHERE tenant_id = :tenant_id
            """),
            params,
        ).mappings().one()

        energy = conn.execute(
            text("""
                SELECT COUNT(*) AS total_records
                FROM esg_energy
                WHERE tenant_id = :tenant_id
            """),
            params,
        ).mappings().one()

        incidents = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS total_records,
                    COUNT(*) FILTER (
                        WHERE status <> 'closed'
                    ) AS unresolved_incidents
                FROM esg_environmental_incidents
                WHERE tenant_id = :tenant_id
            """),
            params,
        ).mappings().one()

    return {
        "module": "ESG Data Quality Intelligence",
        "emissions": serialize_esg_row(emissions),
        "energy": serialize_esg_row(energy),
        "environmental_incidents":
            serialize_esg_row(incidents),
        "generated_at":
            datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# ESG SYSTEM HEALTH
# ============================================================

@router.get(
    "/system/status",
    summary="ESG Module System Status",
)
def esg_system_status(
    identity: TokenIdentity = Depends(verify_access_token),
):
    engine = get_engine()

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    return {
        "platform": "MQEIP",
        "module": "ESG & Sustainability Intelligence",
        "database": "connected",
        "authentication": "required",
        "tenant_id": str(identity.tenant_id),
        "status": "operational",
        "timestamp":
            datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# INITIALIZE ESG DATABASE TABLES
# ============================================================

init_esg_tables()
init_esg_audit_table()

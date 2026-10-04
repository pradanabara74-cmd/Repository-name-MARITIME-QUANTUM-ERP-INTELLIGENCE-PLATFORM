from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..core.security import TokenIdentity, verify_access_token


router = APIRouter(
    prefix="/api/v1/fleet",
    tags=["Fleet"],
)


class VesselCreate(BaseModel):
    vessel_name: str = Field(..., min_length=2, max_length=120)
    imo_number: Optional[str] = Field(default=None, max_length=20)
    vessel_type: str = Field(..., min_length=2, max_length=80)
    flag: Optional[str] = Field(default=None, max_length=80)
    status: str = Field(default="active", max_length=30)


class VesselResponse(BaseModel):
    vessel_id: str
    vessel_name: str
    imo_number: Optional[str]
    vessel_type: str
    flag: Optional[str]
    status: str
    tenant_id: str
    created_at: str


# Temporary in-memory storage.
# This will later be replaced by the MQEIP database layer.
_fleet_store: dict[str, dict] = {}


@router.get("")
def list_vessels(
    identity: TokenIdentity = Depends(verify_access_token),
):
    vessels = [
        vessel
        for vessel in _fleet_store.values()
        if vessel["tenant_id"] == identity.tenant_id
    ]

    return {
        "platform": "MQEIP",
        "module": "Fleet Management",
        "tenant_id": identity.tenant_id,
        "total_vessels": len(vessels),
        "vessels": vessels,
    }


@router.post(
    "",
    response_model=VesselResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_vessel(
    vessel: VesselCreate,
    identity: TokenIdentity = Depends(verify_access_token),
):
    vessel_name = vessel.vessel_name.strip()

    for existing in _fleet_store.values():
        if (
            existing["tenant_id"] == identity.tenant_id
            and existing["vessel_name"].lower() == vessel_name.lower()
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Vessel already exists for this tenant.",
            )

    vessel_id = f"{identity.tenant_id}-vessel-{len(_fleet_store) + 1:04d}"

    record = {
        "vessel_id": vessel_id,
        "vessel_name": vessel_name,
        "imo_number": vessel.imo_number,
        "vessel_type": vessel.vessel_type.strip(),
        "flag": vessel.flag,
        "status": vessel.status.strip().lower(),
        "tenant_id": identity.tenant_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    _fleet_store[vessel_id] = record

    return record


@router.get("/{vessel_id}", response_model=VesselResponse)
def get_vessel(
    vessel_id: str,
    identity: TokenIdentity = Depends(verify_access_token),
):
    vessel = _fleet_store.get(vessel_id)

    if vessel is None or vessel["tenant_id"] != identity.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vessel not found.",
        )

    return vessel

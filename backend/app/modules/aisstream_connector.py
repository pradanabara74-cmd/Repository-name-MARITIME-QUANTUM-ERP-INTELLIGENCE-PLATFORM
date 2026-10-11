"""
AISStream Connector — MQEIP

Features:
- Non-blocking asynchronous WebSocket connection
- Automatic reconnect with exponential backoff and jitter
- HTTP 429 rate-limit handling
- Configurable geographic bounding box and MMSI filters
- In-memory vessel position cache
- JWT-protected monitoring endpoints

Environment:
    AISSTREAM_API_KEY
    AISSTREAM_BBOX (optional): lat1,lon1,lat2,lon2
    AISSTREAM_MMSI (optional): comma-separated MMSI values
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.security import TokenIdentity, verify_access_token


logger = logging.getLogger("mqeip.ais")

AIS_URL = "wss://stream.aisstream.io/v0/stream"

# Default geographic coverage: Batam and Singapore waters.
DEFAULT_BBOX = [[0.5, 103.0], [2.0, 105.0]]

MAX_VESSELS = 5000
STALE_SECONDS = 6 * 3600
IDLE_TIMEOUT = 120

INITIAL_BACKOFF = 30
MAX_BACKOFF = 900
RATE_LIMIT_BACKOFF = 300


# ============================================================
# RUNTIME STATE
# ============================================================

_positions: dict[int, dict[str, Any]] = {}

_status: dict[str, Any] = {
    "enabled": False,
    "connected": False,
    "messages_received": 0,
    "last_message_at": None,
    "last_error": None,
    "reconnects": 0,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# GEOGRAPHIC FILTER
# ============================================================

def _parse_bbox() -> list[list[list[float]]]:
    raw = os.getenv("AISSTREAM_BBOX", "").strip()

    if raw:
        try:
            values = [float(x.strip()) for x in raw.split(",")]

            if len(values) != 4:
                raise ValueError("Expected four coordinates")

            lat1, lon1, lat2, lon2 = values

            if not (
                -90 <= lat1 <= 90
                and -90 <= lat2 <= 90
                and -180 <= lon1 <= 180
                and -180 <= lon2 <= 180
                and lat1 < lat2
                and lon1 < lon2
            ):
                raise ValueError("Invalid bounding box")

            return [[[lat1, lon1], [lat2, lon2]]]

        except ValueError:
            logger.warning(
                "Invalid AISSTREAM_BBOX; using default."
            )

    return [DEFAULT_BBOX]


# ============================================================
# SUBSCRIPTION
# ============================================================

def _subscription(api_key: str) -> dict[str, Any]:

    subscription: dict[str, Any] = {
        "APIKey": api_key,
        "BoundingBoxes": _parse_bbox(),
        "FilterMessageTypes": ["PositionReport"],
    }

    raw_mmsi = os.getenv("AISSTREAM_MMSI", "")

    mmsi = [
        value.strip()
        for value in raw_mmsi.split(",")
        if value.strip().isdigit()
    ]

    if mmsi:
        subscription["FiltersShipMMSI"] = mmsi[:50]

    return subscription


# ============================================================
# POSITION STORAGE
# ============================================================

def _store(msg: dict[str, Any]) -> None:

    metadata = msg.get("MetaData") or {}

    report = (
        (msg.get("Message") or {}).get("PositionReport")
        or {}
    )

    mmsi = metadata.get("MMSI") or report.get("UserID")

    if not mmsi:
        return

    latitude = metadata.get(
        "latitude",
        report.get("Latitude")
    )

    longitude = metadata.get(
        "longitude",
        report.get("Longitude")
    )

    if latitude is None or longitude is None:
        return

    try:
        mmsi = int(mmsi)
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return

    if not (-90 <= latitude <= 90):
        return

    if not (-180 <= longitude <= 180):
        return

    _positions[mmsi] = {
        "mmsi": mmsi,
        "name": (
            (metadata.get("ShipName") or "").strip()
            or None
        ),
        "latitude": latitude,
        "longitude": longitude,
        "sog": report.get("Sog"),
        "cog": report.get("Cog"),
        "heading": report.get("TrueHeading"),
        "nav_status": report.get("NavigationalStatus"),
        "time_utc": metadata.get("time_utc"),
        "received_at": time.time(),
    }

    # Remove stale positions and enforce memory limit.
    if len(_positions) > MAX_VESSELS:

        cutoff = time.time() - STALE_SECONDS

        stale_keys = [
            key
            for key, value in _positions.items()
            if value["received_at"] < cutoff
        ]

        for key in stale_keys:
            _positions.pop(key, None)

        while len(_positions) > MAX_VESSELS:

            oldest = min(
                _positions,
                key=lambda key: _positions[key]["received_at"]
            )

            _positions.pop(oldest, None)


# ============================================================
# AISSTREAM SESSION
# ============================================================

async def _session(api_key: str) -> None:

    import websockets

    async with websockets.connect(
        AIS_URL,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=5,
        max_queue=64,
    ) as websocket:

        # Send subscription immediately after connection.
        await websocket.send(
            json.dumps(_subscription(api_key))
        )

        _status["connected"] = True
        _status["last_error"] = None

        logger.info("AIS Stream subscription sent")

        while True:

            raw = await asyncio.wait_for(
                websocket.recv(),
                timeout=IDLE_TIMEOUT
            )

            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")

            try:
                message = json.loads(raw)
            except (ValueError, TypeError):
                continue

            if not isinstance(message, dict):
                continue

            if "error" in message:
                raise RuntimeError(
                    f"AISStream error: {message['error']}"
                )

            if message.get("MessageType") != "PositionReport":
                continue

            _store(message)

            _status["messages_received"] += 1
            _status["last_message_at"] = _now_iso()

            await asyncio.sleep(0)


# ============================================================
# RECONNECT MANAGER
# ============================================================

async def stream_ais() -> None:

    api_key = os.getenv(
        "AISSTREAM_API_KEY",
        ""
    ).strip()

    if not api_key:
        logger.info(
            "AISSTREAM_API_KEY missing; AIS disabled."
        )
        return

    try:
        import websockets  # noqa: F401

    except ImportError:

        _status["last_error"] = (
            "Python package 'websockets' is not installed"
        )

        logger.error(_status["last_error"])
        return

    _status["enabled"] = True

    backoff = INITIAL_BACKOFF

    while True:

        previous_messages = _status["messages_received"]

        retry_after = None

        try:
            await _session(api_key)

        except asyncio.CancelledError:
            _status["connected"] = False
            raise

        except Exception as exc:

            error_text = f"{type(exc).__name__}: {exc}"

            _status["last_error"] = error_text[:300]

            logger.warning(
                "AIS connection failed: %s",
                _status["last_error"]
            )

            # Respect upstream rate limiting.
            if (
                "429" in error_text
                or "rate limit" in error_text.lower()
            ):
                retry_after = RATE_LIMIT_BACKOFF

        finally:
            _status["connected"] = False

        _status["reconnects"] += 1

        if _status["messages_received"] > previous_messages:
            backoff = INITIAL_BACKOFF
        else:
            backoff = min(
                backoff * 2,
                MAX_BACKOFF
            )

        delay = max(
            backoff,
            retry_after or 0
        )

        delay += random.uniform(0, 15)

        logger.info(
            "AIS reconnect scheduled in %.1f seconds",
            delay
        )

        await asyncio.sleep(delay)


# ============================================================
# AIS API ROUTER
# ============================================================

router = APIRouter(
    prefix="/api/v1/ais",
    tags=["AIS Intelligence"]
)


@router.get("/status")
async def ais_status(
    identity: TokenIdentity = Depends(verify_access_token)
):
    return {
        **_status,
        "vessels_tracked": len(_positions),
    }


@router.get("/positions")
async def ais_positions(
    limit: int = Query(200, ge=1, le=1000),
    identity: TokenIdentity = Depends(verify_access_token),
):

    items = sorted(
        _positions.values(),
        key=lambda value: value["received_at"],
        reverse=True
    )

    return {
        "count": len(items),
        "items": items[:limit],
    }


@router.get("/positions/{mmsi}")
async def ais_position(
    mmsi: int,
    identity: TokenIdentity = Depends(verify_access_token),
):

    item = _positions.get(mmsi)

    if not item:
        raise HTTPException(
            status_code=404,
            detail="MMSI belum terpantau."
        )

    return item

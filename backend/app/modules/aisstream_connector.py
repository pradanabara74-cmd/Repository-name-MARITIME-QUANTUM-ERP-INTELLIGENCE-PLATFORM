"""
MQEIP AIS INTELLIGENCE
Production-oriented AISStream connector.

Features:
- Non-blocking asyncio WebSocket
- Automatic reconnect with HTTP 429 backoff
- Indonesia bounding box and MMSI filtering
- Bounded in-memory vessel positions
- Secure JWT-protected AIS endpoints
- Real-time connection diagnostics
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
from app.core.security import TokenIdentity, verify_access_token

logger = logging.getLogger("mqeip.ais")

AIS_URL = "wss://stream.aisstream.io/v0/stream"

DEFAULT_BBOX = [[-12.0, 94.0], [8.0, 142.0]]

MAX_VESSELS = 5000
STALE_SECONDS = 6 * 3600
IDLE_TIMEOUT = 180
MAX_BACKOFF = 300

_positions: dict[int, dict[str, Any]] = {}

_status: dict[str, Any] = {
    "enabled": False,
    "task_started": False,
    "api_key_configured": False,
    "connected": False,
    "messages_received": 0,
    "last_message_at": None,
    "last_error": None,
    "reconnects": 0,
    "vessels_tracked": 0,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_bbox() -> list[list[list[float]]]:
    raw = os.getenv("AISSTREAM_BBOX", "").strip()

    if not raw:
        return [DEFAULT_BBOX]

    try:
        values = [
            float(part.strip())
            for part in raw.split(",")
        ]

        if len(values) != 4:
            raise ValueError("Expected four coordinates")

        lat1, lon1, lat2, lon2 = values

        if not (
            -90 <= lat1 < lat2 <= 90
            and -180 <= lon1 < lon2 <= 180
        ):
            raise ValueError("Invalid bounding box")

        return [[[lat1, lon1], [lat2, lon2]]]

    except (ValueError, TypeError):
        logger.warning(
            "Invalid AISSTREAM_BBOX; using default"
        )
        return [DEFAULT_BBOX]


def _subscription(api_key: str) -> dict[str, Any]:
    subscription: dict[str, Any] = {
        "APIKey": api_key,
        "BoundingBoxes": _parse_bbox(),
        "FilterMessageTypes": ["PositionReport"],
    }

    raw = os.getenv("AISSTREAM_MMSI", "")

    mmsi_list = []

    for item in raw.split(","):
        item = item.strip()

        if item.isdigit() and len(item) == 9:
            mmsi_list.append(item)

    if mmsi_list:
        subscription["FiltersShipMMSI"] = list(
            dict.fromkeys(mmsi_list)
        )[:50]

    return subscription


def _cleanup_positions() -> None:
    cutoff = time.time() - STALE_SECONDS

    expired = [
        mmsi
        for mmsi, vessel in _positions.items()
        if vessel["received_at"] < cutoff
    ]

    for mmsi in expired:
        _positions.pop(mmsi, None)

    if len(_positions) > MAX_VESSELS:
        ordered = sorted(
            _positions,
            key=lambda m: _positions[m]["received_at"],
        )

        excess = len(_positions) - MAX_VESSELS

        for mmsi in ordered[:excess]:
            _positions.pop(mmsi, None)

    _status["vessels_tracked"] = len(_positions)


def _store(message: dict[str, Any]) -> None:
    if message.get("MessageType") != "PositionReport":
        return

    metadata = message.get("MetaData") or {}
    payload = message.get("Message") or {}
    report = payload.get("PositionReport") or {}

    if not isinstance(metadata, dict):
        return

    if not isinstance(report, dict):
        return

    raw_mmsi = (
        metadata.get("MMSI")
        or report.get("UserID")
    )

    try:
        mmsi = int(raw_mmsi)

        if not 100000000 <= mmsi <= 999999999:
            return

        latitude = report.get("Latitude")
        longitude = report.get("Longitude")

        if latitude is None:
            latitude = metadata.get("latitude")

        if longitude is None:
            longitude = metadata.get("longitude")

        latitude = float(latitude)
        longitude = float(longitude)

        if not (
            -90 <= latitude <= 90
            and -180 <= longitude <= 180
        ):
            return

    except (ValueError, TypeError, OverflowError):
        return

    _positions[mmsi] = {
        "mmsi": mmsi,
        "name": (
            str(metadata.get("ShipName") or "").strip()
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

    _cleanup_positions()


async def _session(api_key: str) -> None:
    import websockets

    async with websockets.connect(
        AIS_URL,
        open_timeout=20,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=5,
        max_queue=64,
    ) as websocket:

        await websocket.send(
            json.dumps(_subscription(api_key))
        )

        _status["connected"] = True
        _status["last_error"] = None

        logger.info("MQEIP AISStream connected")

        while True:
            raw = await asyncio.wait_for(
                websocket.recv(),
                timeout=IDLE_TIMEOUT,
            )

            try:
                message = json.loads(raw)
            except (ValueError, TypeError):
                continue

            if not isinstance(message, dict):
                continue

            if "error" in message:
                raise RuntimeError(
                    str(message["error"])[:200]
                )

            _status["messages_received"] += 1
            _status["last_message_at"] = _now_iso()

            try:
                _store(message)
            except Exception:
                logger.exception(
                    "AIS position processing failed"
                )

            await asyncio.sleep(0)


async def stream_ais() -> None:
    """
    Start through the existing FastAPI lifespan.
    Do not create a second FastAPI application.
    """

    _status["task_started"] = True
    _status["connected"] = False

    api_key = os.getenv(
        "AISSTREAM_API_KEY", ""
    ).strip()

    _status["api_key_configured"] = bool(api_key)

    enabled_setting = os.getenv(
        "AISSTREAM_ENABLED", "true"
    ).strip().lower()

    if enabled_setting not in ("true", "1", "yes"):
        _status["enabled"] = False
        _status["last_error"] = (
            "AISSTREAM_ENABLED is disabled"
        )
        _status["task_started"] = False
        return

    if not api_key:
        _status["enabled"] = False
        _status["last_error"] = (
            "AISSTREAM_API_KEY is missing"
        )
        _status["task_started"] = False
        return

    try:
        import websockets  # noqa: F401
    except ImportError:
        _status["enabled"] = False
        _status["last_error"] = (
            "Python package websockets is missing"
        )
        _status["task_started"] = False
        return

    _status["enabled"] = True

    logger.info("MQEIP AIS STREAM TASK STARTED")

    backoff = 5

    try:
        while True:
            previous_messages = _status[
                "messages_received"
            ]

            try:
                await _session(api_key)

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                error = (
                    f"{type(exc).__name__}: {exc}"
                )[:300]

                _status["last_error"] = error

                logger.warning(
                    "AISStream connection failed: %s",
                    error,
                )

                if (
                    "429" in error
                    or "rate limit" in error.lower()
                    or "too many requests" in error.lower()
                ):
                    backoff = max(backoff, 120)

            finally:
                _status["connected"] = False

            _status["reconnects"] += 1

            if (
                _status["messages_received"]
                > previous_messages
            ):
                backoff = 5

            delay = min(
                backoff + random.uniform(0, 5),
                MAX_BACKOFF,
            )

            logger.info(
                "AIS reconnect after %.1f seconds",
                delay,
            )

            await asyncio.sleep(delay)

            backoff = min(
                backoff * 2,
                MAX_BACKOFF,
            )

    except asyncio.CancelledError:
        logger.info("AIS background task stopped")
        raise

    finally:
        _status["connected"] = False
        _status["enabled"] = False
        _status["task_started"] = False


router = APIRouter(
    prefix="/api/v1/ais",
    tags=["AIS Intelligence"],
)


@router.get("/status")
async def ais_status(
    identity: TokenIdentity = Depends(
        verify_access_token
    ),
):
    _cleanup_positions()
    return dict(_status)


@router.get("/positions")
async def ais_positions(
    limit: int = Query(
        default=200,
        ge=1,
        le=1000,
    ),
    identity: TokenIdentity = Depends(
        verify_access_token
    ),
):
    _cleanup_positions()

    items = sorted(
        _positions.values(),
        key=lambda item: item["received_at"],
        reverse=True,
    )

    return {
        "count": len(items),
        "items": items[:limit],
    }


@router.get("/positions/{mmsi}")
async def ais_position(
    mmsi: int,
    identity: TokenIdentity = Depends(
        verify_access_token
    ),
):
    _cleanup_positions()

    vessel = _positions.get(mmsi)

    if vessel is None:
        raise HTTPException(
            status_code=404,
            detail="MMSI belum terpantau",
        )

    return vessel

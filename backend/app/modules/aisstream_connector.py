"""MQEIP AIS Intelligence — Open Waters anonymous live stream. Uses Open Waters native v1 WebSocket API. Anonymous access requires no API token and is limited to 100 square degrees per subscription; the default test area is around Batam, Indonesia. This module preserves the existing JWT-protected MQEIP AIS endpoints and in-memory vessel schema. Environment variables: AISSTREAM_ENABLED=true OPENWATERS_BBOX=0.5,103,2,105 # south,west,north,east; max 100 sq degrees AISSTREAM_MMSI=123456789 # optional, max 10 MMSIs for anonymous tier Run stream_ais() from the existing FastAPI lifespan. Do not create another app. Requires the websockets package. """

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.security import TokenIdentity, verify_access_token

logger = logging.getLogger("mqeip.ais")

AIS_URL = "wss://ais.openwaters.io/v1/stream"
DEFAULT_BBOX = (0.5, 103.0, 2.0, 105.0)  # Batam test area; ~3 square degrees
MAX_ANONYMOUS_AREA_SQ_DEGREES = 100.0
MAX_ANONYMOUS_MMSI = 10
MAX_VESSELS = 5000
STALE_SECONDS = 6 * 3600
IDLE_TIMEOUT = 180
MAX_BACKOFF = 1800
RATE_LIMIT_COOLDOWN = 60
RATE_LIMIT_MAX_COOLDOWN = 900

_stream_running = False
_positions: dict[int, dict[str, Any]] = {}

_status: dict[str, Any] = {
    "provider": "Open Waters",
    "mode": "anonymous",
    "enabled": False,
    "task_started": False,
    "api_key_configured": False,
    "connected": False,
    "messages_received": 0,
    "last_message_at": None,
    "last_error": None,
    "reconnects": 0,
    "vessels_tracked": 0,
    "retry_after_seconds": None,
    "rate_limited": False,
    "bbox": None,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_bbox() -> tuple[float, float, float, float]:
    """Read OPENWATERS_BBOX=south,west,north,east, enforcing anonymous limits. The old AISSTREAM_BBOX environment variable is deliberately not used because it commonly contains the full Indonesia box, which exceeds the anonymous tier. """
    raw = os.getenv("OPENWATERS_BBOX", "").strip()
    if not raw:
        return DEFAULT_BBOX
    try:
        values = tuple(float(part.strip()) for part in raw.split(","))
        if len(values) != 4:
            raise ValueError("Expected south,west,north,east")
        south, west, north, east = values
        if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
            raise ValueError("Invalid bounding box coordinates")
        area = (north - south) * (east - west)
        if area > MAX_ANONYMOUS_AREA_SQ_DEGREES:
            logger.warning(
                "OPENWATERS_BBOX area %.2f exceeds anonymous limit; using Batam test box",
                area,
            )
            return DEFAULT_BBOX
        return south, west, north, east
    except (ValueError, TypeError):
        logger.warning("Invalid OPENWATERS_BBOX; using Batam test box")
        return DEFAULT_BBOX


def _subscription() -> dict[str, Any]:
    """Build the Open Waters native v1 anonymous subscription."""
    south, west, north, east = _parse_bbox()
    subscription: dict[str, Any] = {
        "type": "subscribe",
        "bbox": [[south, west, north, east]],
        "snapshot": True,
    }
    raw = os.getenv("AISSTREAM_MMSI", "")
    mmsi_list: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if item.isdigit() and len(item) == 9:
            value = int(item)
            if value not in mmsi_list:
                mmsi_list.append(value)
    if mmsi_list:
        subscription["mmsi"] = mmsi_list[:MAX_ANONYMOUS_MMSI]
    return subscription


def _cleanup_positions() -> None:
    cutoff = time.time() - STALE_SECONDS
    expired = [
        mmsi for mmsi, vessel in _positions.items()
        if vessel.get("received_at", 0) < cutoff
    ]
    for mmsi in expired:
        _positions.pop(mmsi, None)
    if len(_positions) > MAX_VESSELS:
        ordered = sorted(
            _positions,
            key=lambda mmsi: _positions[mmsi].get("received_at", 0),
        )
        for mmsi in ordered[: len(_positions) - MAX_VESSELS]:
            _positions.pop(mmsi, None)
    _status["vessels_tracked"] = len(_positions)


def _numeric_or_none(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        number = float(value)
        if math.isfinite(number):
            return number
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def _store(message: dict[str, Any]) -> None:
    """Validate and store a native Open Waters v1 event with a known position."""
    if not isinstance(message, dict) or message.get("type") != "event":
        return

    raw_mmsi = message.get("mmsi")
    report = message.get("message") or {}
    if not isinstance(report, dict):
        report = {}

    try:
        mmsi = int(raw_mmsi if raw_mmsi is not None else report.get("UserID"))
        latitude = float(message.get("lat"))
        longitude = float(message.get("lon"))
    except (ValueError, TypeError, OverflowError):
        return

    if not 100000000 <= mmsi <= 999999999:
        return
    if not (
        math.isfinite(latitude) and math.isfinite(longitude)
        and -90 <= latitude <= 90 and -180 <= longitude <= 180
    ):
        return

    name = message.get("name") or report.get("ShipName") or report.get("Name") or ""
    _positions[mmsi] = {
        "mmsi": mmsi,
        "name": str(name).strip() or None,
        "latitude": latitude,
        "longitude": longitude,
        "sog": _numeric_or_none(report.get("Sog", message.get("sog"))),
        "cog": _numeric_or_none(report.get("Cog", message.get("cog"))),
        "heading": _numeric_or_none(
            report.get("TrueHeading", message.get("heading"))
        ),
        "nav_status": report.get(
            "NavigationalStatus", message.get("nav_status")
        ),
        "time_utc": message.get("time"),
        "received_at": time.time(),
    }
    _cleanup_positions()


async def _session() -> None:
    """Connect anonymously, subscribe to Batam (or a configured small area)."""
    import websockets

    async with websockets.connect(
        AIS_URL,
        open_timeout=20,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=5,
        max_queue=256,
    ) as websocket:
        await websocket.send(json.dumps(_subscription()))
        _status["connected"] = True
        _status["last_error"] = None
        _status["rate_limited"] = False
        _status["retry_after_seconds"] = None
        logger.info("MQEIP connected to Open Waters anonymous AIS stream")

        while True:
            raw = await asyncio.wait_for(websocket.recv(), timeout=IDLE_TIMEOUT)
            try:
                message = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(message, dict):
                continue
            if message.get("type") == "error":
                raise RuntimeError(str(message.get("error", "Open Waters stream error"))[:200])
            if message.get("type") != "event":
                # Ignore welcome/ack frames; only AIS events are vessel messages.
                continue
            _status["messages_received"] += 1
            _status["last_message_at"] = _now_iso()
            try:
                _store(message)
            except Exception:
                logger.exception("AIS position processing failed")
            await asyncio.sleep(0)


async def stream_ais() -> None:
    """Run once as a background task from the existing FastAPI lifespan."""
    global _stream_running
    if _stream_running:
        logger.warning("AIS task already running; skipping duplicate")
        return

    _stream_running = True
    _status["task_started"] = True
    _status["connected"] = False
    _status["api_key_configured"] = False  # anonymous access; no token required
    enabled_setting = os.getenv("AISSTREAM_ENABLED", "true").strip().lower()
    if enabled_setting not in ("true", "1", "yes"):
        _status.update(
            enabled=False,
            last_error="AISSTREAM_ENABLED is disabled",
            task_started=False,
        )
        _stream_running = False
        return

    try:
        import websockets  # noqa: F401
    except ImportError:
        _status.update(
            enabled=False,
            last_error="Python package websockets is missing",
            task_started=False,
        )
        _stream_running = False
        return

    bbox = _parse_bbox()
    _status["bbox"] = list(bbox)
    _status["enabled"] = True
    _status["last_error"] = None
    logger.info("MQEIP Open Waters AIS task started; anonymous area bbox=%s", bbox)

    backoff = 5.0
    rate_limit_until = 0.0
    try:
        while True:
            remaining = rate_limit_until - time.monotonic()
            while remaining > 0:
                _status["retry_after_seconds"] = int(math.ceil(remaining))
                await asyncio.sleep(min(remaining, 30))
                remaining = rate_limit_until - time.monotonic()
            _status["retry_after_seconds"] = None
            previous_messages = _status["messages_received"]
            rate_limited_this_attempt = False
            try:
                await _session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:300]
                _status["last_error"] = error
                logger.warning("Open Waters AIS connection failed: %s", error)
                error_lower = error.lower()
                rate_limited_this_attempt = (
                    "429" in error_lower
                    or "rate limit" in error_lower
                    or "too many requests" in error_lower
                    or "too many concurrent" in error_lower
                )
                if rate_limited_this_attempt:
                    cooldown = min(RATE_LIMIT_COOLDOWN, RATE_LIMIT_MAX_COOLDOWN)
                    rate_limit_until = max(rate_limit_until, time.monotonic() + cooldown)
                    _status["rate_limited"] = True
                    _status["retry_after_seconds"] = cooldown
                    logger.warning("Open Waters rate limit: pausing for %s seconds", cooldown)
            finally:
                _status["connected"] = False

            _status["reconnects"] += 1
            if rate_limited_this_attempt:
                continue
            if _status["messages_received"] > previous_messages:
                backoff = 5.0
                _status["rate_limited"] = False
            delay = min(backoff + random.uniform(0, 5), MAX_BACKOFF)
            _status["retry_after_seconds"] = int(math.ceil(delay))
            await asyncio.sleep(delay)
            _status["retry_after_seconds"] = None
            backoff = min(backoff * 2, MAX_BACKOFF)
    except asyncio.CancelledError:
        logger.info("AIS background task stopped")
        raise
    finally:
        _status["connected"] = False
        _status["enabled"] = False
        _status["task_started"] = False
        _status["retry_after_seconds"] = None
        _stream_running = False


router = APIRouter(prefix="/api/v1/ais", tags=["AIS Intelligence"])


@router.get("/status")
async def ais_status(identity: TokenIdentity = Depends(verify_access_token)):
    """Return AIS connector status. Requires a valid access token."""
    _cleanup_positions()
    return dict(_status)


@router.get("/positions")
async def ais_positions( limit: int = Query(default=200, ge=1, le=1000), identity: TokenIdentity = Depends(verify_access_token), ):
    """Return the most recently updated vessel positions."""
    _cleanup_positions()
    items = sorted(
        _positions.values(),
        key=lambda item: item["received_at"],
        reverse=True,
    )
    return {"count": len(items), "items": items[:limit]}


@router.get("/positions/{mmsi}")
async def ais_position( mmsi: int, identity: TokenIdentity = Depends(verify_access_token), ):
    """Return the latest position for one MMSI."""
    _cleanup_positions()
    vessel = _positions.get(mmsi)
    if vessel is None:
        raise HTTPException(status_code=404, detail="MMSI belum terpantau")
    return vessel

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
import random

import websockets

logger = logging.getLogger(__name__)

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"

# Configure these in Render Environment.
AISSTREAM_API_KEY = os.getenv("AISSTREAM_API_KEY", "")
MQEIP_AIS_INGEST_URL = os.getenv(
    "MQEIP_AIS_INGEST_URL",
    "http://127.0.0.1:8000/api/v1/marine-operations/ais/positions",
)
MQEIP_ACCESS_TOKEN = os.getenv("MQEIP_ACCESS_TOKEN", "")

# Geographic coverage: Singapore, Batam and surrounding waters.
BOUNDING_BOXES = [
    [[0.5, 103.0], [2.0, 105.0]]
]


def build_subscription():
    if not AISSTREAM_API_KEY:
        raise RuntimeError("AISSTREAM_API_KEY is missing")

    return {
        "APIKey": AISSTREAM_API_KEY,
        "BoundingBoxes": BOUNDING_BOXES,
        "FilterMessageTypes": ["PositionReport"],
    }


def parse_position(message):
    if message.get("MessageType") != "PositionReport":
        return None

    metadata = message.get("MetaData") or {}
    report = (message.get("Message") or {}).get("PositionReport") or {}

    mmsi = metadata.get("MMSI") or report.get("UserID")
    latitude = report.get("Latitude")
    longitude = report.get("Longitude")

    if not mmsi or latitude is None or longitude is None:
        return None

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None

    observed_at = metadata.get("time_utc")
    if not observed_at:
        return None

    observed_at = observed_at.replace("Z", "+00:00")

    try:
        timestamp = datetime.fromisoformat(observed_at)
    except ValueError:
        return None

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    return {
        "vessel_name": metadata.get("ShipName") or f"MMSI-{mmsi}",
        "mmsi": str(mmsi),
        "latitude": latitude,
        "longitude": longitude,
        "sog_knots": report.get("Sog"),
        "cog_degrees": report.get("Cog"),
        "heading_degrees": report.get("TrueHeading"),
        "source": "AISSTREAM",
        "source_type": "AIS",
        "observed_at": timestamp.isoformat(),
    }


async def send_to_mqeip(position):
    import httpx

    if not MQEIP_ACCESS_TOKEN:
        raise RuntimeError("MQEIP_ACCESS_TOKEN is missing")

    headers = {
        "Authorization": f"Bearer {MQEIP_ACCESS_TOKEN}"
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            MQEIP_AIS_INGEST_URL,
            json=position,
            headers=headers,
        )
        response.raise_for_status()


async def stream_ais():
    retry_delay = 30
    while True:
        try:
            async with websockets.connect(
                AISSTREAM_URL,
                ping_interval=20,
                ping_timeout=20,
            ) as websocket:

                await websocket.send(
                    json.dumps(build_subscription())
                )

                logger.info("AIS Stream subscription sent")
                retry_delay = 30

                async for raw_message in websocket:
                    message = json.loads(raw_message)
                    position = parse_position(message)

                    if position:
                        try:
                            await send_to_mqeip(position)
                            logger.info(
                                "AIS position stored: %s",
                                position["mmsi"],
                            )
                        except Exception:
                            logger.exception(
                                "Failed to ingest AIS position"
                            )

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("AIS Stream connection failed")
            await asyncio.sleep(retry_delay + random.uniform(0, 10))
            retry_delay = min(retry_delay * 2, 900)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(stream_ais())

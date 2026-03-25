#!/usr/bin/env python3
"""
Historical statistics importer for Home Assistant.

Fetches all available monthly water consumption data from the Eaux de
Marseille portal and injects it into Home Assistant's long-term statistics
database via the WebSocket API (same method used by MyElectricalData).

Run this script once after initial setup to backfill historical data.
Subsequent updates are handled automatically by fetch.py every hour.

Required additional environment variables
-----------------------------------------
HA_URL      : Home Assistant base URL, e.g. http://192.168.1.236:8123
HA_TOKEN    : Long-lived access token generated in HA user profile

Usage
-----
    python3 import_history.py
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import websocket
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eaux_marseille import ApiError, AuthenticationError, Config, EauxDeMarseilleClient

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_STATISTIC_ID = "sensor.eau_marseille_mois_m3"
_UNIT = "m³"
_START_YEAR = 2024


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {key}")
    return value


def fetch_monthly_range(
    client: EauxDeMarseilleClient, year: int, contract_id: str
) -> list[dict]:
    """Fetch monthly consumption entries for a given calendar year."""
    start = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
    end = int(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())
    path = (
        f"/Consommation/listeConsommationsInstanceAlerteChart"
        f"/{contract_id}/{start}/{end}/MOIS/true"
    )
    data = client._get(path)  # noqa: SLF001
    return data.get("consommations", [])


def build_statistics(entries: list[dict]) -> list[dict]:
    """Convert API entries to HA statistics format."""
    stats = []
    for entry in entries:
        value = entry.get("volumeConsoEnM3")
        date_str = entry.get("dateReleve")
        if value is None or not date_str:
            continue
        stats.append(
            {
                "start": date_str,
                "mean": round(float(value), 3),
                "min": round(float(value), 3),
                "max": round(float(value), 3),
            }
        )
    return stats


def import_via_websocket(ha_url: str, ha_token: str, stats: list[dict]) -> None:
    """Push statistics to Home Assistant via the WebSocket API."""
    ws_url = ha_url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
    ws_url += "/api/websocket"

    logger.info("Connecting to %s ...", ws_url)

    ws = websocket.create_connection(ws_url, timeout=30)

    try:
        # Step 1 — receive auth_required
        msg = json.loads(ws.recv())
        assert msg["type"] == "auth_required", f"Unexpected: {msg}"

        # Step 2 — authenticate
        ws.send(json.dumps({"type": "auth", "access_token": ha_token}))
        msg = json.loads(ws.recv())
        if msg["type"] != "auth_ok":
            raise RuntimeError(f"Authentication failed: {msg}")
        logger.info("WebSocket authenticated.")

        # Step 3 — import statistics
        payload = {
            "id": 1,
            "type": "recorder/import_statistics",
            "metadata": {
                "has_mean": True,
                "has_sum": False,
                "name": "Eau Marseille Mois M3",
                "source": "recorder",
                "statistic_id": _STATISTIC_ID,
                "unit_of_measurement": _UNIT,
            },
            "stats": stats,
        }
        ws.send(json.dumps(payload))
        msg = json.loads(ws.recv())

        if msg.get("success"):
            logger.info("Successfully imported %d monthly statistics.", len(stats))
        else:
            logger.error("Import failed: %s", msg)
            sys.exit(1)

    finally:
        ws.close()


def main() -> int:
    try:
        config = Config.from_env()
        ha_url = _require_env("HA_URL")
        ha_token = _require_env("HA_TOKEN")
    except EnvironmentError as exc:
        logger.error(str(exc))
        return 1

    all_stats: list[dict] = []
    current_year = datetime.now(timezone.utc).year

    try:
        with EauxDeMarseilleClient(config) as client:
            client.authenticate()
            for year in range(_START_YEAR, current_year + 1):
                logger.info("Fetching monthly data for %d...", year)
                entries = fetch_monthly_range(client, year, config.contract_id)
                year_stats = build_statistics(entries)
                logger.info("  -> %d entries found.", len(year_stats))
                all_stats.extend(year_stats)

    except (AuthenticationError, ApiError) as exc:
        logger.error("API error: %s", exc)
        return 1

    if not all_stats:
        logger.warning("No data retrieved from the portal.")
        return 1

    logger.info("Importing %d total entries into Home Assistant...", len(all_stats))
    import_via_websocket(ha_url, ha_token, all_stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
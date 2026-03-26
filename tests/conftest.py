"""Shared fixtures for Eaux de Marseille tests."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# ------------------------------------------------------------------
# Detect whether a real Home Assistant installation is available.
# When it is not (local dev on Windows), we stub the modules so that
# the API client can still be imported and tested standalone.
# CI (GitHub Actions) installs the real package via
# pytest-homeassistant-custom-component.
# ------------------------------------------------------------------
try:
    from homeassistant.core import HomeAssistant  # noqa: F401

    HAS_HA = True
except (ImportError, ModuleNotFoundError):
    HAS_HA = False
    _ha = MagicMock()
    for mod in (
        "homeassistant",
        "homeassistant.config_entries",
        "homeassistant.const",
        "homeassistant.core",
        "homeassistant.components",
        "homeassistant.components.sensor",
        "homeassistant.components.recorder",
        "homeassistant.components.recorder.models",
        "homeassistant.components.recorder.statistics",
        "homeassistant.helpers",
        "homeassistant.helpers.device_registry",
        "homeassistant.helpers.entity_platform",
        "homeassistant.helpers.update_coordinator",
        "homeassistant.data_entry_flow",
        "voluptuous",
    ):
        sys.modules[mod] = _ha

from custom_components.eaux_marseille.api import ConsumptionData, EauxDeMarseilleClient


def pytest_collection_modifyitems(config, items):
    """Skip tests marked ha_required when HA is not installed."""
    if HAS_HA:
        return
    skip_ha = pytest.mark.skip(reason="Requires Home Assistant (CI only)")
    for item in items:
        if "ha_required" in item.keywords:
            item.add_marker(skip_ha)

MOCK_USERNAME = "user@example.com"
MOCK_PASSWORD = "s3cret"  # noqa: S105
MOCK_CONTRACT_ID = "1234567"

MOCK_CONFIG_ENTRY_DATA = {
    "username": MOCK_USERNAME,
    "password": MOCK_PASSWORD,
    "contract_id": MOCK_CONTRACT_ID,
}

MOCK_CONSUMPTION = ConsumptionData(
    index_m3=193.0,
    last_reading_m3=18.0,
    last_reading_litres=18000,
    last_reading_date="2026-03-05",
    last_reading_days=94,
    daily_average_m3=0.1915,
    previous_reading_m3=18.0,
    previous_reading_date="2025-12-01",
    current_month_m3=5.481,
    current_month_litres=5481,
    current_year_m3=18.443,
    total_readings=10,
)

MOCK_MONTHLY_ENTRIES = [
    {"dateReleve": "2024-07-15T00:00:00+02:00", "volumeConsoEnM3": 3.0, "volumeConsoEnLitres": 3000},
    {"dateReleve": "2024-08-15T00:00:00+02:00", "volumeConsoEnM3": 4.5, "volumeConsoEnLitres": 4500},
    {"dateReleve": "2024-09-15T00:00:00+02:00", "volumeConsoEnM3": 2.0, "volumeConsoEnLitres": 2000},
]


@pytest.fixture
def mock_client() -> MagicMock:
    """Return a mocked EauxDeMarseilleClient."""
    client = MagicMock(spec=EauxDeMarseilleClient)
    client.authenticate.return_value = None
    client.fetch.return_value = MOCK_CONSUMPTION
    client.fetch_monthly_range.return_value = MOCK_MONTHLY_ENTRIES
    client.close.return_value = None
    return client

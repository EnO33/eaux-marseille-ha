"""Shared fixtures for Eaux de Marseille tests."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

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
        "homeassistant.exceptions",
        "homeassistant.components",
        "homeassistant.components.diagnostics",
        "homeassistant.components.sensor",
        "homeassistant.components.recorder",
        "homeassistant.components.recorder.models",
        "homeassistant.components.recorder.statistics",
        "homeassistant.helpers",
        "homeassistant.helpers.device_registry",
        "homeassistant.helpers.entity_platform",
        "homeassistant.helpers.event",
        "homeassistant.helpers.update_coordinator",
        "homeassistant.data_entry_flow",
        "homeassistant.util",
        "homeassistant.util.dt",
        # Stubbed so HA-only test modules (skipped locally via the
        # ha_required marker) still import cleanly during collection.
        "pytest_homeassistant_custom_component",
        "pytest_homeassistant_custom_component.common",
        "voluptuous",
    ):
        sys.modules[mod] = _ha

from custom_components.eaux_marseille.api import ConsumptionData, EauxDeMarseilleClient

MOCK_USERNAME = "user@example.com"
MOCK_PASSWORD = "s3cret"
MOCK_CONTRACT_ID = "1234567"

MOCK_CONFIG_ENTRY_DATA = {
    "provider": "sem",
    "username": MOCK_USERNAME,
    "password": MOCK_PASSWORD,
    "contract_id": MOCK_CONTRACT_ID,
}

MOCK_CONSUMPTION = ConsumptionData(
    index_m3=193.0,
    index_precise_m3=193.842,
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
    {
        "dateReleve": "2024-07-15T00:00:00+02:00",
        "volumeConsoEnM3": 3.0,
        "volumeConsoEnLitres": 3000,
    },
    {
        "dateReleve": "2024-08-15T00:00:00+02:00",
        "volumeConsoEnM3": 4.5,
        "volumeConsoEnLitres": 4500,
    },
    {
        "dateReleve": "2024-09-15T00:00:00+02:00",
        "volumeConsoEnM3": 2.0,
        "volumeConsoEnLitres": 2000,
    },
]


def pytest_collection_modifyitems(config, items):
    """Skip tests marked ha_required when HA is not installed."""
    if HAS_HA:
        return
    skip_ha = pytest.mark.skip(reason="Requires Home Assistant (CI only)")
    for item in items:
        if "ha_required" in item.keywords:
            item.add_marker(skip_ha)


@pytest.fixture
def mock_client() -> MagicMock:
    """Return a mocked EauxDeMarseilleClient with async methods."""
    client = MagicMock(spec=EauxDeMarseilleClient)
    client.authenticate = AsyncMock(return_value=None)
    client.fetch = AsyncMock(return_value=MOCK_CONSUMPTION)
    client.fetch_monthly_range = AsyncMock(return_value=MOCK_MONTHLY_ENTRIES)
    client.fetch_daily_range = AsyncMock(return_value=[])
    client.is_daily_available = AsyncMock(return_value=False)
    client.close = AsyncMock(return_value=None)
    return client


if HAS_HA:
    from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.eaux_marseille.const import (
        CONF_CONTRACT_ID,
        CONF_PROVIDER,
        DOMAIN,
    )

    @pytest.fixture
    def mock_config_entry(hass):
        """Return a MockConfigEntry added to hass."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            title=f"Contrat {MOCK_CONTRACT_ID}",
            data={
                CONF_PROVIDER: "sem",
                CONF_USERNAME: MOCK_USERNAME,
                CONF_PASSWORD: MOCK_PASSWORD,
                CONF_CONTRACT_ID: MOCK_CONTRACT_ID,
            },
            unique_id=f"sem_{MOCK_CONTRACT_ID}",
        )
        entry.add_to_hass(hass)
        return entry

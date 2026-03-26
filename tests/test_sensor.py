"""Tests for the Eaux de Marseille sensor platform.

These tests require a full Home Assistant environment and only run in CI.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.eaux_marseille.const import DOMAIN, ENTRY_CLIENT, ENTRY_COORDINATOR

from .conftest import MOCK_CONFIG_ENTRY_DATA, MOCK_CONTRACT_ID, MOCK_CONSUMPTION

pytestmark = [pytest.mark.ha_required, pytest.mark.usefixtures("enable_custom_integrations")]


async def test_sensors_created(hass: HomeAssistant, mock_client: MagicMock, mock_config_entry) -> None:
    """All expected sensors are created on setup."""
    with patch(
        "custom_components.eaux_marseille.EauxDeMarseilleClient",
        return_value=mock_client,
    ), patch(
        "custom_components.eaux_marseille.coordinator.EauxDeMarseilleCoordinator._async_update_data",
        return_value=MOCK_CONSUMPTION,
    ), patch(
        "custom_components.eaux_marseille.async_import_historical_statistics",
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, mock_config_entry.entry_id)

    expected_keys = {
        "current_month_m3",
        "current_month_litres",
        "current_year_m3",
        "index_m3",
        "daily_average_m3",
        "last_reading_m3",
        "last_reading_litres",
        "last_reading_date",
        "last_reading_days",
        "previous_reading_m3",
        "previous_reading_date",
        "total_readings",
    }

    created_keys = {e.unique_id.removeprefix(f"{MOCK_CONTRACT_ID}_") for e in entries}
    assert expected_keys == created_keys


async def test_sensor_values(hass: HomeAssistant, mock_client: MagicMock, mock_config_entry) -> None:
    """Sensor states reflect the consumption data."""
    with patch(
        "custom_components.eaux_marseille.EauxDeMarseilleClient",
        return_value=mock_client,
    ), patch(
        "custom_components.eaux_marseille.coordinator.EauxDeMarseilleCoordinator._async_update_data",
        return_value=MOCK_CONSUMPTION,
    ), patch(
        "custom_components.eaux_marseille.async_import_historical_statistics",
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, mock_config_entry.entry_id)

    # Find the current_month_m3 entity
    month_entity = next(e for e in entries if e.unique_id == f"{MOCK_CONTRACT_ID}_current_month_m3")
    state = hass.states.get(month_entity.entity_id)
    assert state is not None
    assert float(state.state) == 5.481

    # Find the index entity
    index_entity = next(e for e in entries if e.unique_id == f"{MOCK_CONTRACT_ID}_index_m3")
    state = hass.states.get(index_entity.entity_id)
    assert state is not None
    assert float(state.state) == 193.0

    # Find the total_readings entity
    readings_entity = next(e for e in entries if e.unique_id == f"{MOCK_CONTRACT_ID}_total_readings")
    state = hass.states.get(readings_entity.entity_id)
    assert state is not None
    assert int(float(state.state)) == 10

"""Tests for the Eaux de Marseille integration setup.

These tests require a full Home Assistant environment and only run in CI.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant

from custom_components.eaux_marseille.const import DOMAIN, ENTRY_CLIENT, ENTRY_COORDINATOR

from .conftest import MOCK_CONSUMPTION, MOCK_CONTRACT_ID

pytestmark = [pytest.mark.ha_required, pytest.mark.usefixtures("enable_custom_integrations")]


async def test_setup_entry(hass: HomeAssistant, mock_client: MagicMock, mock_config_entry) -> None:
    """Integration sets up correctly from a config entry."""
    with patch(
        "custom_components.eaux_marseille.EauxDeMarseilleClient",
        return_value=mock_client,
    ), patch(
        "custom_components.eaux_marseille.coordinator.EauxDeMarseilleCoordinator._async_update_data",
        return_value=MOCK_CONSUMPTION,
    ), patch(
        "custom_components.eaux_marseille.async_import_historical_statistics",
    ):
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert result is True
    assert mock_config_entry.entry_id in hass.data[DOMAIN]
    assert ENTRY_CLIENT in hass.data[DOMAIN][mock_config_entry.entry_id]
    assert ENTRY_COORDINATOR in hass.data[DOMAIN][mock_config_entry.entry_id]


async def test_unload_entry(hass: HomeAssistant, mock_client: MagicMock, mock_config_entry) -> None:
    """Integration unloads correctly."""
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

        result = await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert result is True
    assert mock_config_entry.entry_id not in hass.data[DOMAIN]
    mock_client.close.assert_called()

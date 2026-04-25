"""Tests for the Eaux de Marseille coordinator.

These tests require a full Home Assistant environment and only run in CI.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.eaux_marseille.api import (
    EauxDeMarseilleApiError,
    EauxDeMarseilleAuthError,
)
from custom_components.eaux_marseille.coordinator import EauxDeMarseilleCoordinator

from .conftest import MOCK_CONSUMPTION

pytestmark = pytest.mark.ha_required


async def test_coordinator_update_success(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Coordinator fetches data successfully."""
    coordinator = EauxDeMarseilleCoordinator(hass, mock_client)
    await coordinator.async_refresh()

    assert coordinator.data == MOCK_CONSUMPTION
    mock_client.authenticate.assert_called_once()
    mock_client.fetch.assert_called_once()


async def test_coordinator_auth_error_raises_reauth(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """Auth errors raise ConfigEntryAuthFailed so HA triggers the reauth flow."""
    mock_client.authenticate.side_effect = EauxDeMarseilleAuthError("expired")

    coordinator = EauxDeMarseilleCoordinator(hass, mock_client)
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_coordinator_api_error(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Coordinator sets last_update_success=False on API error."""
    mock_client.fetch.side_effect = EauxDeMarseilleApiError("500")

    coordinator = EauxDeMarseilleCoordinator(hass, mock_client)
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False

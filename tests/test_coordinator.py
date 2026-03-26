"""Tests for the Eaux de Marseille coordinator.

These tests require a full Home Assistant environment and only run in CI.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

pytestmark = pytest.mark.ha_required

from custom_components.eaux_marseille.api import (
    EauxDeMarseilleApiError,
    EauxDeMarseilleAuthError,
)
from custom_components.eaux_marseille.coordinator import EauxDeMarseilleCoordinator

from .conftest import MOCK_CONSUMPTION


async def test_coordinator_update_success(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Coordinator fetches data successfully."""
    coordinator = EauxDeMarseilleCoordinator(hass, mock_client)
    await coordinator.async_config_entry_first_refresh()

    assert coordinator.data == MOCK_CONSUMPTION
    mock_client.authenticate.assert_called_once()
    mock_client.fetch.assert_called_once()


async def test_coordinator_auth_error(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Coordinator raises UpdateFailed on auth error."""
    mock_client.authenticate.side_effect = EauxDeMarseilleAuthError("expired")

    coordinator = EauxDeMarseilleCoordinator(hass, mock_client)
    with pytest.raises(UpdateFailed, match="Authentication error"):
        await coordinator.async_config_entry_first_refresh()


async def test_coordinator_api_error(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Coordinator raises UpdateFailed on API error."""
    mock_client.fetch.side_effect = EauxDeMarseilleApiError("500")

    coordinator = EauxDeMarseilleCoordinator(hass, mock_client)
    with pytest.raises(UpdateFailed, match="API error"):
        await coordinator.async_config_entry_first_refresh()

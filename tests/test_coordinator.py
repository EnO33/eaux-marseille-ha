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
from custom_components.eaux_marseille.const import DOMAIN
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
    """Auth errors raise ConfigEntryAuthFailed with a translation key."""
    mock_client.authenticate.side_effect = EauxDeMarseilleAuthError("expired")

    coordinator = EauxDeMarseilleCoordinator(hass, mock_client)
    with pytest.raises(ConfigEntryAuthFailed) as excinfo:
        await coordinator._async_update_data()

    # Verify the exception carries our translation key so HA renders the
    # localised message instead of the raw English exception text.
    assert excinfo.value.translation_domain == DOMAIN
    assert excinfo.value.translation_key == "auth_failed"
    assert excinfo.value.translation_placeholders == {"error": "expired"}


async def test_coordinator_api_error(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Coordinator sets last_update_success=False on API error.

    The wrapped UpdateFailed exception also carries a translation key so
    the integration card displays a localised message.
    """
    mock_client.fetch.side_effect = EauxDeMarseilleApiError("portal 500")

    coordinator = EauxDeMarseilleCoordinator(hass, mock_client)
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert coordinator.last_exception is not None
    assert coordinator.last_exception.translation_domain == DOMAIN
    assert coordinator.last_exception.translation_key == "update_failed"
    assert coordinator.last_exception.translation_placeholders == {"error": "portal 500"}

"""Tests for the Eaux de Marseille custom services.

These tests require a full Home Assistant environment and only run in CI.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.eaux_marseille.const import DOMAIN
from custom_components.eaux_marseille.services import (
    ATTR_CONFIG_ENTRY,
    SERVICE_REFRESH,
    SERVICE_REIMPORT_STATISTICS,
    async_register_services,
)

from .conftest import MOCK_CONSUMPTION

pytestmark = [pytest.mark.ha_required, pytest.mark.usefixtures("enable_custom_integrations")]


async def _setup_loaded_entry(hass: HomeAssistant, mock_client: MagicMock, entry) -> None:
    """Bring the integration up to ``LOADED`` for the given mock entry."""
    with (
        patch(
            "custom_components.eaux_marseille.EauxDeMarseilleClient",
            return_value=mock_client,
        ),
        patch(
            "custom_components.eaux_marseille.coordinator.EauxDeMarseilleCoordinator._async_update_data",
            return_value=MOCK_CONSUMPTION,
        ),
        patch(
            "custom_components.eaux_marseille.async_import_historical_statistics",
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_services_registered_after_setup(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry
) -> None:
    """async_setup registers both refresh and reimport services."""
    await _setup_loaded_entry(hass, mock_client, mock_config_entry)
    assert hass.services.has_service(DOMAIN, SERVICE_REFRESH)
    assert hass.services.has_service(DOMAIN, SERVICE_REIMPORT_STATISTICS)


async def test_register_services_is_idempotent(hass: HomeAssistant) -> None:
    """Calling async_register_services twice is a no-op."""
    async_register_services(hass)
    async_register_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_REFRESH)
    assert hass.services.has_service(DOMAIN, SERVICE_REIMPORT_STATISTICS)


async def test_refresh_calls_coordinator(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry
) -> None:
    """refresh service triggers an immediate coordinator request."""
    await _setup_loaded_entry(hass, mock_client, mock_config_entry)

    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator.async_request_refresh = AsyncMock()

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REFRESH,
        {ATTR_CONFIG_ENTRY: mock_config_entry.entry_id},
        blocking=True,
    )

    coordinator.async_request_refresh.assert_awaited_once()


async def test_reimport_runs_historical_import(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry
) -> None:
    """reimport service spins up a fresh client and runs the import."""
    await _setup_loaded_entry(hass, mock_client, mock_config_entry)

    fresh_client = MagicMock()
    fresh_client.close = AsyncMock()
    with (
        patch(
            "custom_components.eaux_marseille.services.EauxDeMarseilleClient",
            return_value=fresh_client,
        ),
        patch(
            "custom_components.eaux_marseille.services.async_import_historical_statistics",
            new=AsyncMock(),
        ) as mock_import,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REIMPORT_STATISTICS,
            {ATTR_CONFIG_ENTRY: mock_config_entry.entry_id},
            blocking=True,
        )

    mock_import.assert_awaited_once()
    # The fresh client used for the re-import is closed even on success.
    fresh_client.close.assert_awaited_once()


async def test_unknown_entry_id_raises(hass: HomeAssistant) -> None:
    """A bogus config_entry id surfaces as ServiceValidationError."""
    async_register_services(hass)
    with pytest.raises(ServiceValidationError) as excinfo:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REFRESH,
            {ATTR_CONFIG_ENTRY: "does-not-exist"},
            blocking=True,
        )
    assert excinfo.value.translation_key == "service_unknown_entry"


async def test_unloaded_entry_raises(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry
) -> None:
    """An entry that exists but isn't loaded raises ServiceValidationError."""
    # Set up then unload — the entry id stays valid, but state goes back to NOT_LOADED.
    await _setup_loaded_entry(hass, mock_client, mock_config_entry)
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as excinfo:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REFRESH,
            {ATTR_CONFIG_ENTRY: mock_config_entry.entry_id},
            blocking=True,
        )
    assert excinfo.value.translation_key == "service_entry_not_loaded"

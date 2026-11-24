"""Tests for the diagnostics download.

These tests require a full Home Assistant environment and only run in CI.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.eaux_marseille.diagnostics import async_get_config_entry_diagnostics

from .conftest import (
    MOCK_CONTRACT_ID,
    MOCK_METER,
    MOCK_PASSWORD,
    MOCK_USERNAME,
    setup_loaded_entry,
)

pytestmark = [pytest.mark.ha_required, pytest.mark.usefixtures("enable_custom_integrations")]

# What Home Assistant's async_redact_data puts in place of a redacted value.
_REDACTED = "**REDACTED**"


async def test_diagnostics_redact_identifiers(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry
) -> None:
    """Credentials, contract and meter serial never reach the diagnostics file."""
    await setup_loaded_entry(hass, mock_client, mock_config_entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert diagnostics["entry"]["data"]["username"] == _REDACTED
    assert diagnostics["entry"]["data"]["password"] == _REDACTED
    assert diagnostics["entry"]["data"]["contract_id"] == _REDACTED
    assert diagnostics["meter"] == {
        "serial_number": _REDACTED,
        "reading_type": MOCK_METER.reading_type,
    }
    assert diagnostics["data"]["index_m3"] == 193.0
    dumped = repr(diagnostics)
    for secret in (MOCK_USERNAME, MOCK_PASSWORD, MOCK_CONTRACT_ID, MOCK_METER.serial_number):
        assert secret not in dumped

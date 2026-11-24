"""Config entry migrations (#130).

These tests require a full Home Assistant environment and only run in CI.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eaux_marseille._migrate import async_migrate
from custom_components.eaux_marseille.const import DOMAIN
from custom_components.eaux_marseille.statistics import daily_statistic_id

from .conftest import MOCK_CONTRACT_ID

pytestmark = pytest.mark.ha_required

_DAILY_ID = daily_statistic_id(MOCK_CONTRACT_ID)


@pytest.fixture
def recorder() -> MagicMock:
    """A recorder instance standing in for the real one."""
    instance = MagicMock()
    with patch("custom_components.eaux_marseille._migrate.get_instance", return_value=instance):
        yield instance


async def test_entry_1_1_clears_the_daily_statistic_once(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, recorder: MagicMock
) -> None:
    hass.config.components.add("recorder")

    assert await async_migrate(hass, mock_config_entry)
    assert await async_migrate(hass, mock_config_entry)

    recorder.async_clear_statistics.assert_called_once_with([_DAILY_ID])
    assert (mock_config_entry.version, mock_config_entry.minor_version) == (1, 2)


async def test_without_recorder_only_the_version_moves(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, recorder: MagicMock
) -> None:
    assert "recorder" not in hass.config.components

    assert await async_migrate(hass, mock_config_entry)

    recorder.async_clear_statistics.assert_not_called()
    assert mock_config_entry.minor_version == 2


async def test_future_version_is_refused(hass: HomeAssistant, recorder: MagicMock) -> None:
    """A downgrade must not run on an entry it does not understand."""
    entry = MockConfigEntry(domain=DOMAIN, version=2)
    entry.add_to_hass(hass)

    assert not await async_migrate(hass, entry)
    recorder.async_clear_statistics.assert_not_called()

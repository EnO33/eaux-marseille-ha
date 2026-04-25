"""Tests for the Eaux de Marseille config flow.

These tests require a full Home Assistant environment and only run in CI.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.eaux_marseille.api import (
    EauxDeMarseilleAuthError,
    EauxDeMarseilleApiError,
)
from custom_components.eaux_marseille.const import DOMAIN

from .conftest import MOCK_CONFIG_ENTRY_DATA, MOCK_CONTRACT_ID

pytestmark = [pytest.mark.ha_required, pytest.mark.usefixtures("enable_custom_integrations")]


async def test_user_flow_success(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Test a successful config flow from the user step."""
    with patch(
        "custom_components.eaux_marseille.config_flow.EauxDeMarseilleClient",
        return_value=mock_client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=MOCK_CONFIG_ENTRY_DATA
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Contrat {MOCK_CONTRACT_ID}"
    assert result["data"] == MOCK_CONFIG_ENTRY_DATA


async def test_user_flow_invalid_auth(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Test config flow handles invalid credentials."""
    mock_client.authenticate.side_effect = EauxDeMarseilleAuthError("Bad creds")

    with patch(
        "custom_components.eaux_marseille.config_flow.EauxDeMarseilleClient",
        return_value=mock_client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=MOCK_CONFIG_ENTRY_DATA
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Test config flow handles connection errors."""
    mock_client.authenticate.side_effect = Exception("Network error")

    with patch(
        "custom_components.eaux_marseille.config_flow.EauxDeMarseilleClient",
        return_value=mock_client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=MOCK_CONFIG_ENTRY_DATA
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_duplicate(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry
) -> None:
    """Test config flow aborts on duplicate contract."""
    with patch(
        "custom_components.eaux_marseille.config_flow.EauxDeMarseilleClient",
        return_value=mock_client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=MOCK_CONFIG_ENTRY_DATA
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ----------------------------------------------------------------------
# Reauthentication flow
# ----------------------------------------------------------------------


async def test_reauth_flow_success(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry
) -> None:
    """Reauth with valid credentials updates the entry password and reloads."""
    with patch(
        "custom_components.eaux_marseille.config_flow.EauxDeMarseilleClient",
        return_value=mock_client,
    ):
        result = await mock_config_entry.start_reauth_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_PASSWORD: "new-password"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_PASSWORD] == "new-password"


async def test_reauth_flow_invalid_auth(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry
) -> None:
    """Reauth with bad credentials shows the form again with an error."""
    mock_client.authenticate.side_effect = EauxDeMarseilleAuthError("still bad")

    with patch(
        "custom_components.eaux_marseille.config_flow.EauxDeMarseilleClient",
        return_value=mock_client,
    ):
        result = await mock_config_entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_PASSWORD: "wrong-password"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reauth_flow_cannot_connect(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry
) -> None:
    """Reauth handles connection errors during validation."""
    mock_client.authenticate.side_effect = EauxDeMarseilleApiError("portal down")

    with patch(
        "custom_components.eaux_marseille.config_flow.EauxDeMarseilleClient",
        return_value=mock_client,
    ):
        result = await mock_config_entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_PASSWORD: "any"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

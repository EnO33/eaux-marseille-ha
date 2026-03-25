"""Config flow for Eaux de Marseille."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .api import EauxDeMarseilleAuthError, EauxDeMarseilleClient
from .const import CONF_CONTRACT_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_CONTRACT_ID): str,
    }
)


class EauxDeMarseilleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the configuration flow for Eaux de Marseille."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_CONTRACT_ID])
            self._abort_if_unique_id_configured()

            client = EauxDeMarseilleClient(
                login=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                contract_id=user_input[CONF_CONTRACT_ID],
            )

            try:
                await self.hass.async_add_executor_job(client.authenticate)
            except EauxDeMarseilleAuthError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during authentication")
                errors["base"] = "cannot_connect"
            else:
                client.close()
                return self.async_create_entry(
                    title=f"Contrat {user_input[CONF_CONTRACT_ID]}",
                    data=user_input,
                )
            finally:
                client.close()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

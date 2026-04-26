"""Config flow for Eaux de Marseille."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .api import EauxDeMarseilleApiError, EauxDeMarseilleAuthError, EauxDeMarseilleClient
from .const import CONF_CONTRACT_ID, CONF_PROVIDER, DEFAULT_PROVIDER, DOMAIN, Provider

_LOGGER = logging.getLogger(__name__)

# Display labels for each provider in the dropdown.
_PROVIDER_LABELS = {
    Provider.SEM: "Société des Eaux de Marseille (Marseille)",
    Provider.SEMM: "Eau de Marseille Métropole (Aix-Marseille-Provence)",
}

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PROVIDER, default=DEFAULT_PROVIDER.value): vol.In(
            {p.value: label for p, label in _PROVIDER_LABELS.items()}
        ),
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_CONTRACT_ID): str,
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): str,
    }
)


async def _validate_credentials(
    *,
    username: str,
    password: str,
    contract_id: str,
    provider: Provider,
) -> str | None:
    """Authenticate against the portal.

    Returns:
        ``None`` on success, or one of ``"invalid_auth"`` / ``"cannot_connect"``
        as an error key compatible with HA's config-flow error reporting.
    """
    client = EauxDeMarseilleClient(
        login=username,
        password=password,
        contract_id=contract_id,
        provider=provider,
    )
    try:
        await client.authenticate()
    except EauxDeMarseilleAuthError as err:
        _LOGGER.warning("Authentication failed: %s", err)
        return "invalid_auth"
    except EauxDeMarseilleApiError:
        _LOGGER.exception("API error during setup")
        return "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected error during authentication")
        return "cannot_connect"
    finally:
        await client.close()
    return None


class EauxDeMarseilleConfigFlow(ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle the configuration flow for Eaux de Marseille."""

    VERSION = 1

    _reauth_entry: ConfigEntry | None = None

    # ------------------------------------------------------------------
    # User flow (initial setup)
    # ------------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            provider = Provider(user_input[CONF_PROVIDER])
            # Make the unique_id provider-aware so users can configure the
            # same contract number on both portals (extremely unlikely but
            # cheap to support).
            await self.async_set_unique_id(f"{provider.value}_{user_input[CONF_CONTRACT_ID]}")
            self._abort_if_unique_id_configured()

            error = await _validate_credentials(
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                contract_id=user_input[CONF_CONTRACT_ID],
                provider=provider,
            )
            if error is None:
                return self.async_create_entry(
                    title=f"Contrat {user_input[CONF_CONTRACT_ID]}",
                    data=user_input,
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Reauthentication flow (triggered by ConfigEntryAuthFailed)
    # ------------------------------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start the reauth flow for an existing entry."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to re-enter the portal password."""
        if self._reauth_entry is None:
            # Defensive: HA always sets the entry_id in self.context before
            # calling async_step_reauth, so this branch is unreachable in
            # practice. We surface a clear error instead of using assert
            # (which gets stripped under python -O).
            raise RuntimeError("Reauth flow started without an entry context")
        errors: dict[str, str] = {}

        if user_input is not None:
            error = await _validate_credentials(
                username=self._reauth_entry.data[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                contract_id=self._reauth_entry.data[CONF_CONTRACT_ID],
                provider=Provider(
                    self._reauth_entry.data.get(CONF_PROVIDER, DEFAULT_PROVIDER.value)
                ),
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            description_placeholders={
                "username": self._reauth_entry.data[CONF_USERNAME],
            },
            errors=errors,
        )

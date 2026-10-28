"""Config flow for Eaux de Marseille."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import (
    EauxDeMarseilleApiError,
    EauxDeMarseilleAuthError,
    EauxDeMarseilleClient,
    EauxDeMarseilleContractError,
)
from .const import CONF_CONTRACT_ID, CONF_PROVIDER, DEFAULT_PROVIDER, DOMAIN, Provider

_LOGGER = logging.getLogger(__name__)

# Provider dropdown; labels live under "selector.provider" in strings.json.
_PROVIDER_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[provider.value for provider in Provider],
        translation_key=CONF_PROVIDER,
        mode=SelectSelectorMode.DROPDOWN,
    )
)

# Entry titles are stored once and cannot be translated: keep them
# language-neutral ("SEM 1234567").
_TITLE_PREFIX = {
    Provider.SEM: "SEM",
    Provider.SEMM: "SEMM",
    Provider.VIVAIGO: "Vivaigo",
}

# The contract number goes into URL paths and the mobility request body:
# accept only characters that cannot change the path (no "/", "?", "..").
# Letters, "-" and "_" are allowed because the SEMM and Vivaigo formats
# are not verified on a real contract.
_CONTRACT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")

STEP_REAUTH_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): str,
    }
)


def _credentials_schema(current: Mapping[str, Any]) -> vol.Schema:
    """Return the setup form, pre-filled with ``current`` (never the password)."""

    def required(key: str, default: str | None = None) -> vol.Required:
        value = current.get(key, default)
        return vol.Required(key) if value is None else vol.Required(key, default=value)

    return vol.Schema(
        {
            required(CONF_PROVIDER, DEFAULT_PROVIDER.value): _PROVIDER_SELECTOR,
            required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
            required(CONF_CONTRACT_ID): str,
        }
    )


def _entry_title(provider: Provider, contract_id: str) -> str:
    """Return the config entry title, e.g. ``"SEM 1234567"``."""
    return f"{_TITLE_PREFIX[provider]} {contract_id}"


def _with_clean_contract(user_input: dict[str, Any]) -> dict[str, Any] | None:
    """Return ``user_input`` with the contract number stripped, or ``None`` if malformed."""
    contract_id = user_input[CONF_CONTRACT_ID].strip()
    if not _CONTRACT_ID_RE.fullmatch(contract_id):
        return None
    return {**user_input, CONF_CONTRACT_ID: contract_id}


async def _validate_credentials(data: Mapping[str, Any]) -> str | None:
    """Authenticate against the portal and check the contract belongs to the account.

    ``data`` has the shape of the config entry data.

    Returns:
        ``None`` on success, or one of ``"invalid_auth"`` / ``"invalid_contract"``
        / ``"cannot_connect"`` as an error key compatible with HA's config-flow
        error reporting.
    """
    client = EauxDeMarseilleClient(
        login=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        contract_id=data[CONF_CONTRACT_ID],
        provider=Provider(data.get(CONF_PROVIDER, DEFAULT_PROVIDER.value)),
    )
    try:
        await client.authenticate()
        await client.check_contract()
    except EauxDeMarseilleAuthError as err:
        _LOGGER.warning("Authentication failed: %s", client.redact(str(err)))
        return "invalid_auth"
    except EauxDeMarseilleContractError as err:
        _LOGGER.warning("Contract check failed: %s", client.redact(str(err)))
        return "invalid_contract"
    except EauxDeMarseilleApiError as err:
        # No traceback: the chained errors would print the contract number.
        _LOGGER.error("API error during setup: %s", client.redact(str(err)))  # noqa: TRY400
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

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input = _with_clean_contract(user_input)
            if user_input is None:
                errors[CONF_CONTRACT_ID] = "invalid_contract_format"
        if user_input is not None:
            provider = Provider(user_input[CONF_PROVIDER])
            # Make the unique_id provider-aware so users can configure the
            # same contract number on both portals (extremely unlikely but
            # cheap to support).
            await self.async_set_unique_id(f"{provider.value}_{user_input[CONF_CONTRACT_ID]}")
            self._abort_if_unique_id_configured()

            error = await _validate_credentials(user_input)
            if error is None:
                return self.async_create_entry(
                    title=_entry_title(provider, user_input[CONF_CONTRACT_ID]),
                    data=user_input,
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema({}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start the reauth flow for an existing entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to re-enter the portal password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            error = await _validate_credentials(data)
            if error is None:
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            description_placeholders={
                "username": entry.data[CONF_USERNAME],
            },
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the user to change email, contract or provider in place.

        Useful when the email on the portal changes, or when the user
        notices a typo in their contract id, without forcing them to
        delete the entry (which would lose all sensor and statistics
        history attached to the device).
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input = _with_clean_contract(user_input)
            if user_input is None:
                errors[CONF_CONTRACT_ID] = "invalid_contract_format"
        if user_input is not None:
            provider = Provider(user_input[CONF_PROVIDER])
            # If the provider/contract pair changed, make sure another
            # entry isn't already using the new identity.
            await self.async_set_unique_id(f"{provider.value}_{user_input[CONF_CONTRACT_ID]}")
            self._abort_if_unique_id_mismatch(reason="reconfigure_unique_id_mismatch")

            error = await _validate_credentials(user_input)
            if error is None:
                return self.async_update_reload_and_abort(
                    entry,
                    data=user_input,
                    title=_entry_title(provider, user_input[CONF_CONTRACT_ID]),
                )
            errors["base"] = error

        # Pre-fill the form with the current values so the user only
        # edits what they want to change.
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_credentials_schema(entry.data),
            errors=errors,
        )

"""Build an API client from a config entry.

Shared by :func:`async_setup_entry` and the ``reimport_statistics``
service so the config-entry -> client mapping lives in exactly one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .api import EauxDeMarseilleClient
from .const import CONF_CONTRACT_ID, CONF_PROVIDER, DEFAULT_PROVIDER, Provider

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


def build_client(entry: ConfigEntry) -> EauxDeMarseilleClient:
    """Construct an :class:`EauxDeMarseilleClient` from a config entry.

    Entries created before 1.9.0 don't store the provider, so it defaults
    to SEM (see :data:`DEFAULT_PROVIDER`).
    """
    provider = Provider(entry.data.get(CONF_PROVIDER, DEFAULT_PROVIDER.value))
    return EauxDeMarseilleClient(
        login=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        contract_id=entry.data[CONF_CONTRACT_ID],
        provider=provider,
    )

"""Constants for the Eaux de Marseille integration.

Centralising the magic strings/numbers here gives us a single place to
adjust portal URLs, retry policy or HTTP headers if the upstream
service ever changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# ---------------------------------------------------------------------
# Home Assistant integration metadata
# ---------------------------------------------------------------------

DOMAIN = "eaux_marseille"

CONF_CONTRACT_ID = "contract_id"
CONF_PROVIDER = "provider"

# ---------------------------------------------------------------------
# Customer portal endpoints
# ---------------------------------------------------------------------


class Provider(StrEnum):
    """Water utilities served by the same back-end stack (SOMEI/Veolia)."""

    # Société des Eaux de Marseille — periphery: Ventabren, Bandol,
    # Cabriès, Fuveau, Vitrolles, Trets, Le Puy-Sainte-Réparade…
    SEM = "sem"
    # Eau de Marseille Métropole — Marseille proper, plus Allauch,
    # La Ciotat, Cassis, Marignane, Carry-le-Rouet, Carnoux, Gémenos…
    SEMM = "semm"
    # Vivaigo — Salon-de-Provence, Berre-l'Étang, Lambesc, Eyguières,
    # Pélissanne, Velaux, Rognac, Sénas, etc.
    VIVAIGO = "vivaigo"


@dataclass(frozen=True, slots=True)
class PortalEndpoints:
    """Per-provider customer-portal URL, host and static app credentials.

    Both portals are operated by the same vendor (SOMEI/Veolia) and share
    the same ``/webapi`` schema, but each one issues its own application
    credentials embedded in its public JavaScript bundle.
    """

    url: str
    host: str
    client_id: str
    access_key: str

    @property
    def api_base(self) -> str:
        return f"{self.url}/webapi"


PROVIDERS: dict[Provider, PortalEndpoints] = {
    Provider.SEM: PortalEndpoints(
        url="https://espaceclients.eauxdemarseille.fr",
        host="espaceclients.eauxdemarseille.fr",
        client_id="SOMEI-GSEM-PRD",
        access_key="XX_ma2DD-2017-GSEM-PRD!",
    ),
    Provider.SEMM: PortalEndpoints(
        url="https://espaceclients.eaudemarseille-metropole.fr",
        host="espaceclients.eaudemarseille-metropole.fr",
        client_id="SOMEI-SEMM-PRD",
        access_key="XX_ma3pD-2017-SEMM-PRD!",
    ),
    Provider.VIVAIGO: PortalEndpoints(
        url="https://espaceclients.vivaigo.fr",
        host="espaceclients.vivaigo.fr",
        client_id="SOMEI-VIE-PRD",
        access_key="QT_gha8D-2024-VIE-PRD!",
    ),
}

DEFAULT_PROVIDER = Provider.SEM

# Backwards-compat aliases for existing test imports (see
# ``custom_components.eaux_marseille.api._PORTAL_URL`` etc.).
PORTAL_URL = PROVIDERS[DEFAULT_PROVIDER].url
PORTAL_HOST = PROVIDERS[DEFAULT_PROVIDER].host
API_BASE = PROVIDERS[DEFAULT_PROVIDER].api_base
APP_CLIENT_ID = PROVIDERS[DEFAULT_PROVIDER].client_id
APP_ACCESS_KEY = PROVIDERS[DEFAULT_PROVIDER].access_key

# ---------------------------------------------------------------------
# HTTP behaviour
# ---------------------------------------------------------------------

REQUEST_TIMEOUT_S = 15

# Exponential backoff on transient errors: 1s, 2s, 4s.
MAX_RETRIES = 3
BACKOFF_BASE_S = 1.0

# We follow redirects manually (instead of relying on aiohttp) so we can
# enforce same-origin and HTTPS, and so we can preserve POST bodies on
# 307/308. See ``_http._send_following_redirects``.
MAX_REDIRECTS = 5

# Mimic a real browser. Some WAFs gate requests on the User-Agent. The
# ``Origin``/``Referer`` headers are added per-request because they
# depend on the active provider.
BASE_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
}


def headers_for(endpoints: PortalEndpoints) -> dict[str, str]:
    """Return the standard request headers tied to a portal."""
    return {
        **BASE_HEADERS,
        "Origin": endpoints.url,
        "Referer": f"{endpoints.url}/",
    }


# Backwards-compat alias: existing modules import DEFAULT_HEADERS expecting
# the SEM headers. Computed once at import time from the default provider.
DEFAULT_HEADERS: dict[str, str] = headers_for(PROVIDERS[DEFAULT_PROVIDER])

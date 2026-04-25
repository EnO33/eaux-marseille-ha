"""Constants for the Eaux de Marseille integration.

Centralising the magic strings/numbers here gives us a single place to
adjust the portal URL, retry policy or HTTP headers if the upstream
service ever changes.
"""

from __future__ import annotations

# ---------------------------------------------------------------------
# Home Assistant integration metadata
# ---------------------------------------------------------------------

DOMAIN = "eaux_marseille"

CONF_CONTRACT_ID = "contract_id"

# ---------------------------------------------------------------------
# Customer portal endpoints
# ---------------------------------------------------------------------

PORTAL_URL = "https://espaceclients.eauxdemarseille.fr"
PORTAL_HOST = "espaceclients.eauxdemarseille.fr"
API_BASE = f"{PORTAL_URL}/webapi"

# Static application credentials embedded in the portal JavaScript bundle.
# These identify the *web client* to the API and are not user credentials.
APP_CLIENT_ID = "SOMEI-GSEM-PRD"
APP_ACCESS_KEY = "XX_ma2DD-2017-GSEM-PRD!"

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

# Mimic a real browser. Some WAFs gate requests on the User-Agent.
DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": PORTAL_URL,
    "Referer": f"{PORTAL_URL}/",
}

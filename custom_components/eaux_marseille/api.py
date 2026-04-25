"""High-level client for the Eaux de Marseille customer portal.

This module exposes :class:`EauxDeMarseilleClient`, an asynchronous
client that authenticates against ``espaceclients.eauxdemarseille.fr``
and fetches the per-contract consumption data.

Internally, all the HTTP plumbing (retries, manual redirect handling,
JSON parsing, error mapping) lives in :mod:`._http`; the data shapes
live in :mod:`.models`; the constants live in :mod:`.const`. This file
is the orchestrator that ties those pieces together.
"""

from __future__ import annotations

import logging
import urllib.parse
import uuid
from datetime import UTC, datetime
from typing import Any

import aiohttp
from yarl import URL

from . import _http
from .const import (
    API_BASE,
    APP_ACCESS_KEY,
    APP_CLIENT_ID,
    DEFAULT_HEADERS,
    PORTAL_URL,
    REQUEST_TIMEOUT_S,
)
from .exceptions import (
    EauxDeMarseilleApiError,
    EauxDeMarseilleAuthError,
    EauxDeMarseilleError,
)
from .models import ConsumptionData

_LOGGER = logging.getLogger(__name__)

# Re-exported for backwards compatibility with existing test imports.
__all__ = [
    "ConsumptionData",
    "EauxDeMarseilleApiError",
    "EauxDeMarseilleAuthError",
    "EauxDeMarseilleClient",
    "EauxDeMarseilleError",
]

# Aliased here so the test suite's existing `from .api import _PORTAL_URL`
# imports keep working without changing the test files.
_PORTAL_URL = PORTAL_URL
_API_BASE = API_BASE


def _conversation_id() -> str:
    """Generate a unique ``ConversationId`` header value (one per request)."""
    return f"JS-WEB-Netscape-{uuid.uuid4()}"


class EauxDeMarseilleClient:
    """Async client for the Eaux de Marseille customer portal API.

    Usage::

        client = EauxDeMarseilleClient(login, password, contract_id)
        try:
            await client.authenticate()
            data = await client.fetch()
        finally:
            await client.close()

    A :class:`aiohttp.ClientSession` may be supplied to share a
    connection pool with the rest of the application. When omitted, the
    client owns its own session and closes it on :meth:`close`.
    """

    def __init__(
        self,
        login: str,
        password: str,
        contract_id: str,
        session: aiohttp.ClientSession | None = None,
        timeout: int = REQUEST_TIMEOUT_S,
    ) -> None:
        self._login = login
        self._password = password
        self._contract_id = contract_id
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._owns_session = session is None
        self._session = session or aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True),
            timeout=self._timeout,
        )
        self._token: str | None = None

    async def close(self) -> None:
        """Close the underlying :class:`aiohttp.ClientSession` if we own it."""
        if self._owns_session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Public flow
    # ------------------------------------------------------------------

    async def authenticate(self) -> None:
        """Run the full 5-step authentication flow against the portal."""
        _LOGGER.info("Authentication: step 1/5 (acquiring session cookie)")
        await self._acquire_session_cookie()

        _LOGGER.info("Authentication: step 2/5 (generating token)")
        temp_token = await self._generate_token()

        _LOGGER.info("Authentication: step 3/5 (logging in user)")
        ael_token, user_info = await self._login_user(temp_token)

        _LOGGER.info("Authentication: step 4/5 (fetching default contract)")
        contract = await self._get_default_contract()

        _LOGGER.info("Authentication: step 5/5 (setting context cookie)")
        self._set_context_cookie(contract, user_info, ael_token)

        _LOGGER.info("Authentication successful")

    async def fetch(self) -> ConsumptionData:
        """Fetch the three consumption endpoints and aggregate the result."""
        last_billed = await self._get(
            f"/TableauDeBord/derniereConsommationFacturee/{self._contract_id}"
        )
        monthly = await self._get(self._monthly_path_for_year(_current_utc_year()))
        history = await self._get(f"/Facturation/listeConsommationsFacturees/{self._contract_id}")
        return ConsumptionData.from_api_responses(last_billed, monthly, history)

    async def fetch_monthly_range(self, year: int) -> list[dict[str, Any]]:
        """Return the raw monthly consumption entries for ``year``."""
        data = await self._get(self._monthly_path_for_year(year))
        entries: list[dict[str, Any]] = data.get("consommations", [])
        return entries

    # ------------------------------------------------------------------
    # Internal helpers — authentication
    # ------------------------------------------------------------------

    async def _acquire_session_cookie(self) -> None:
        """Visit the portal landing page so the WAF assigns us a cookie."""
        self._token = None
        try:
            async with self._session.get(
                f"{PORTAL_URL}/",
                headers=DEFAULT_HEADERS,
                timeout=self._timeout,
                allow_redirects=True,
            ) as response:
                await response.read()
                _LOGGER.debug(
                    "Portal landing page: HTTP %d (final URL: %s)",
                    response.status,
                    response.url,
                )
                if response.status >= 400:
                    raise EauxDeMarseilleApiError(
                        f"Portal returned HTTP {response.status} on landing "
                        f"page (final URL: {response.url})"
                    )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EauxDeMarseilleApiError(f"Failed to reach portal: {err}") from err

    async def _generate_token(self) -> str:
        """Exchange the static app credentials for a short-lived token."""
        cid = _conversation_id()
        payload = {
            "ConversationId": cid,
            "ClientId": APP_CLIENT_ID,
            "AccessKey": APP_ACCESS_KEY,
        }
        try:
            data = await self._request(
                "POST",
                f"{API_BASE}/Acces/generateToken",
                extra_headers={"ConversationId": cid, "token": APP_ACCESS_KEY},
                json=payload,
            )
        except EauxDeMarseilleApiError as err:
            raise EauxDeMarseilleAuthError(f"Token generation failed: {err}") from err
        if not data or "token" not in data:
            raise EauxDeMarseilleAuthError("Token generation returned unexpected response")
        token: str = data["token"]
        return token

    async def _login_user(self, temp_token: str) -> tuple[str, dict[str, Any]]:
        """Exchange user credentials for the long-lived AEL session token."""
        self._token = temp_token
        try:
            data = await self._request(
                "POST",
                f"{API_BASE}/Utilisateur/authentification",
                json={"identifiant": self._login, "motDePasse": self._password},
            )
        except EauxDeMarseilleApiError as err:
            raise EauxDeMarseilleAuthError(f"Login failed: {err}") from err
        if not data or "tokenAuthentique" not in data:
            keys = sorted(data.keys()) if isinstance(data, dict) else type(data).__name__
            raise EauxDeMarseilleAuthError(
                f"Login returned unexpected response (missing 'tokenAuthentique'); "
                f"got fields: {keys}"
            )

        ael_token: str = data["tokenAuthentique"]
        user_info: dict[str, Any] = data["utilisateurInfo"]
        self._token = ael_token
        self._session.cookie_jar.update_cookies(
            {"aelToken": ael_token},
            response_url=URL(PORTAL_URL),
        )
        return ael_token, user_info

    async def _get_default_contract(self) -> dict[str, Any]:
        """Fetch the metadata for the user's default contract."""
        return await self._get("/Abonnement/getContratParDefaut/")

    def _set_context_cookie(
        self,
        contract: dict[str, Any],
        user_info: dict[str, Any],
        ael_token: str,
    ) -> None:
        """Materialise the ``AEL_CONTEXT`` cookie required by the portal."""
        context = {
            "type": "contrat",
            "object": contract,
            "user": {
                "identifiant": user_info["identifiant"],
                "nomComplet": (f"{user_info.get('prenom', '')} {user_info.get('nom', '')}"),
                "nom": user_info.get("nom", ""),
                "prenom": user_info.get("prenom", ""),
                "email": user_info.get("email", ""),
                "titre": user_info.get("titre", ""),
                "tokenAuthentique": ael_token,
                "userWebId": user_info.get("userWebId"),
                "meta": user_info.get("meta", {}),
                "profils": user_info.get("profils", []),
            },
        }
        encoded = urllib.parse.quote_plus(str(context).replace("'", '"'))
        self._session.cookie_jar.update_cookies(
            {"AEL_CONTEXT": encoded},
            response_url=URL(PORTAL_URL),
        )

    # ------------------------------------------------------------------
    # Internal helpers — endpoints
    # ------------------------------------------------------------------

    def _monthly_path_for_year(self, year: int) -> str:
        """Build the monthly consumption endpoint path for ``year``."""
        start = int(datetime(year, 1, 1, tzinfo=UTC).timestamp())
        end = int(datetime(year, 12, 31, 23, 59, 59, tzinfo=UTC).timestamp())
        return (
            f"/Consommation/listeConsommationsInstanceAlerteChart/"
            f"{self._contract_id}/{start}/{end}/MOIS/true"
        )

    async def _get(self, path: str) -> dict[str, Any]:
        """Convenience wrapper for authenticated GET on ``API_BASE`` paths."""
        return await self._request("GET", f"{API_BASE}{path}") or {}

    async def _request(
        self,
        method: str,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build the request headers and delegate to :mod:`._http`."""
        headers = {**DEFAULT_HEADERS, "ConversationId": _conversation_id()}
        if self._token:
            headers["token"] = self._token
        if extra_headers:
            headers.update(extra_headers)
        return await _http.request_with_retry(
            self._session,
            method,
            url,
            timeout=self._timeout,
            headers=headers,
            **kwargs,
        )


def _current_utc_year() -> int:
    """Return the current UTC year. Extracted for monkey-patching in tests."""
    return datetime.now(UTC).year

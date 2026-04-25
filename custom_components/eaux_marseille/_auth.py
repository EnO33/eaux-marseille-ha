"""Authentication flow for the Eaux de Marseille customer portal.

The portal needs five steps to materialise a working session:

1. GET ``/`` — acquire a session cookie from the WAF.
2. POST ``/Acces/generateToken`` — exchange the static app credentials
   for a short-lived bearer token.
3. POST ``/Utilisateur/authentification`` — exchange user credentials
   and the temp token for the long-lived AEL session token.
4. GET ``/Abonnement/getContratParDefaut/`` — fetch the contract metadata.
5. Plant the ``AEL_CONTEXT`` cookie that the portal expects on every
   subsequent call.

Encapsulated as :class:`PortalAuth`: the session, timeout, and bearer
token are kept on the instance, so each step is a tiny method.
"""

from __future__ import annotations

import logging
import uuid
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
)
from .exceptions import EauxDeMarseilleApiError, EauxDeMarseilleAuthError
from .models import encode_context_cookie

_LOGGER = logging.getLogger(__name__)


def conversation_id() -> str:
    """One-shot ``ConversationId`` header value."""
    return f"JS-WEB-Netscape-{uuid.uuid4()}"


class PortalAuth:
    """Stateful 5-step authentication flow against the SEM portal."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        timeout: aiohttp.ClientTimeout,
    ) -> None:
        self._session = session
        self._timeout = timeout
        self.token: str | None = None

    # ------------------------------------------------------------------
    # Public flow
    # ------------------------------------------------------------------

    async def authenticate(self, login: str, password: str) -> None:
        """Run all 5 steps; ``self.token`` ends up holding the AEL token."""
        _LOGGER.info("Authentication: step 1/5 (acquiring session cookie)")
        await self._step_landing()

        _LOGGER.info("Authentication: step 2/5 (generating token)")
        temp_token = await self._step_generate_token()

        _LOGGER.info("Authentication: step 3/5 (logging in user)")
        ael_token, user_info = await self._step_login(login, password, temp_token)

        _LOGGER.info("Authentication: step 4/5 (fetching default contract)")
        contract = await self._step_default_contract()

        _LOGGER.info("Authentication: step 5/5 (setting context cookie)")
        self._step_set_context(contract, user_info, ael_token)

        _LOGGER.info("Authentication successful")

    # ------------------------------------------------------------------
    # Authenticated GET (used by the consumption client after step 5)
    # ------------------------------------------------------------------

    async def get(self, path: str) -> dict[str, Any]:
        """Authenticated GET on a path under :data:`API_BASE`."""
        return await _http.request_with_retry(
            self._session,
            "GET",
            f"{API_BASE}{path}",
            timeout=self._timeout,
            headers=self._headers(),
        )

    # ------------------------------------------------------------------
    # Steps (private)
    # ------------------------------------------------------------------

    async def _step_landing(self) -> None:
        self.token = None
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
                        f"Portal returned HTTP {response.status} on landing page "
                        f"(final URL: {response.url})"
                    )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EauxDeMarseilleApiError(f"Failed to reach portal: {err}") from err

    async def _step_generate_token(self) -> str:
        cid = conversation_id()
        data = await self._auth_call(
            "POST",
            "/Acces/generateToken",
            extra_headers={"ConversationId": cid, "token": APP_ACCESS_KEY},
            json_payload={
                "ConversationId": cid,
                "ClientId": APP_CLIENT_ID,
                "AccessKey": APP_ACCESS_KEY,
            },
            require_field="token",
            error_prefix="Token generation failed",
        )
        token: str = data["token"]
        return token

    async def _step_login(
        self,
        login: str,
        password: str,
        temp_token: str,
    ) -> tuple[str, dict[str, Any]]:
        self.token = temp_token
        data = await self._auth_call(
            "POST",
            "/Utilisateur/authentification",
            json_payload={"identifiant": login, "motDePasse": password},
            require_field="tokenAuthentique",
            error_prefix="Login failed",
        )
        ael_token: str = data["tokenAuthentique"]
        user_info: dict[str, Any] = data["utilisateurInfo"]
        self.token = ael_token
        self._set_cookie("aelToken", ael_token)
        return ael_token, user_info

    async def _step_default_contract(self) -> dict[str, Any]:
        return await self._auth_call("GET", "/Abonnement/getContratParDefaut/")

    def _step_set_context(
        self,
        contract: dict[str, Any],
        user_info: dict[str, Any],
        ael_token: str,
    ) -> None:
        self._set_cookie(
            "AEL_CONTEXT",
            encode_context_cookie(contract, user_info, ael_token),
        )

    # ------------------------------------------------------------------
    # Helpers (private)
    # ------------------------------------------------------------------

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Build the standard request headers, with the bearer token if set."""
        headers = {**DEFAULT_HEADERS, "ConversationId": conversation_id()}
        if self.token:
            headers["token"] = self.token
        if extra:
            headers.update(extra)
        return headers

    def _set_cookie(self, name: str, value: str) -> None:
        self._session.cookie_jar.update_cookies({name: value}, response_url=URL(PORTAL_URL))

    async def _auth_call(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        require_field: str | None = None,
        error_prefix: str = "Request failed",
    ) -> dict[str, Any]:
        """API call mapped to :class:`EauxDeMarseilleAuthError` on failure."""
        try:
            data = await _http.request_with_retry(
                self._session,
                method,
                f"{API_BASE}{path}",
                timeout=self._timeout,
                headers=self._headers(extra_headers),
                json=json_payload,
            )
        except EauxDeMarseilleApiError as err:
            raise EauxDeMarseilleAuthError(f"{error_prefix}: {err}") from err

        if require_field and (not data or require_field not in data):
            keys = sorted(data.keys()) if isinstance(data, dict) else type(data).__name__
            raise EauxDeMarseilleAuthError(
                f"{error_prefix}: missing {require_field!r}; got: {keys}"
            )
        return data

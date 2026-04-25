"""Authentication flow for the Eaux de Marseille customer portal.

The portal needs five steps to materialise a working session:

1. GET ``/`` to acquire a session cookie from the WAF.
2. POST ``/Acces/generateToken`` with the static app credentials to
   obtain a short-lived bearer token.
3. POST ``/Utilisateur/authentification`` with the user credentials and
   the temp token to obtain the long-lived AEL session token.
4. GET ``/Abonnement/getContratParDefaut/`` to fetch contract metadata.
5. Plant the ``AEL_CONTEXT`` cookie that the portal expects on every
   subsequent call.

These steps are exposed as free functions that operate on a session
plus a small mutable :class:`AuthState` object — it lets us keep the
client class thin without losing the ability to update the token after
each step.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
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


@dataclass
class AuthState:
    """Mutable bag holding the bearer token between auth steps."""

    token: str | None = None


async def acquire_session_cookie(
    session: aiohttp.ClientSession,
    timeout: aiohttp.ClientTimeout,
    state: AuthState,
) -> None:
    """Step 1/5 — visit the portal so the WAF sets a session cookie."""
    state.token = None
    try:
        async with session.get(
            f"{PORTAL_URL}/",
            headers=DEFAULT_HEADERS,
            timeout=timeout,
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


async def generate_token(
    session: aiohttp.ClientSession,
    timeout: aiohttp.ClientTimeout,
    state: AuthState,
) -> str:
    """Step 2/5 — exchange the static app credentials for a short-lived token."""
    cid = conversation_id()
    data = await _call(
        session,
        timeout,
        state,
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


async def login_user(
    session: aiohttp.ClientSession,
    timeout: aiohttp.ClientTimeout,
    state: AuthState,
    *,
    login: str,
    password: str,
    temp_token: str,
) -> tuple[str, dict[str, Any]]:
    """Step 3/5 — exchange user credentials for the AEL session token."""
    state.token = temp_token
    data = await _call(
        session,
        timeout,
        state,
        "POST",
        "/Utilisateur/authentification",
        json_payload={"identifiant": login, "motDePasse": password},
        require_field="tokenAuthentique",
        error_prefix="Login failed",
    )
    ael_token: str = data["tokenAuthentique"]
    user_info: dict[str, Any] = data["utilisateurInfo"]
    state.token = ael_token
    _set_cookie(session, "aelToken", ael_token)
    return ael_token, user_info


async def get_default_contract(
    session: aiohttp.ClientSession,
    timeout: aiohttp.ClientTimeout,
    state: AuthState,
) -> dict[str, Any]:
    """Step 4/5 — fetch the user's default contract metadata."""
    return await _call(session, timeout, state, "GET", "/Abonnement/getContratParDefaut/")


def set_context_cookie(
    session: aiohttp.ClientSession,
    contract: dict[str, Any],
    user_info: dict[str, Any],
    ael_token: str,
) -> None:
    """Step 5/5 — plant the AEL_CONTEXT cookie required by every subsequent call."""
    _set_cookie(session, "AEL_CONTEXT", encode_context_cookie(contract, user_info, ael_token))


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _set_cookie(session: aiohttp.ClientSession, name: str, value: str) -> None:
    session.cookie_jar.update_cookies({name: value}, response_url=URL(PORTAL_URL))


async def _call(
    session: aiohttp.ClientSession,
    timeout: aiohttp.ClientTimeout,
    state: AuthState,
    method: str,
    path: str,
    *,
    json_payload: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
    require_field: str | None = None,
    error_prefix: str = "Request failed",
) -> dict[str, Any]:
    """API call that maps transport errors to :class:`EauxDeMarseilleAuthError`.

    Optionally validates that ``require_field`` is present in the
    response, raising a clear error listing the actual fields received
    (helpful when the portal changes its schema).
    """
    headers = {**DEFAULT_HEADERS, "ConversationId": conversation_id()}
    if state.token:
        headers["token"] = state.token
    if extra_headers:
        headers.update(extra_headers)

    try:
        data = await _http.request_with_retry(
            session,
            method,
            f"{API_BASE}{path}",
            timeout=timeout,
            headers=headers,
            json=json_payload,
        )
    except EauxDeMarseilleApiError as err:
        raise EauxDeMarseilleAuthError(f"{error_prefix}: {err}") from err

    if require_field and (not data or require_field not in data):
        keys = sorted(data.keys()) if isinstance(data, dict) else type(data).__name__
        raise EauxDeMarseilleAuthError(f"{error_prefix}: missing {require_field!r}; got: {keys}")
    return data

"""Reaching the portal: the HTTP session, and the landing page that opens a login.

Both are about the network path to the portal, DNS above all: the session
picks the resolver, and the landing page (step 1 of the login, the first
request of a setup) maps transport errors to messages a user can act on.
"""

from __future__ import annotations

import logging

import aiohttp

from . import _http
from .const import PortalEndpoints
from .exceptions import EauxDeMarseilleApiError

_LOGGER = logging.getLogger(__name__)


def new_session(timeout: aiohttp.ClientTimeout) -> aiohttp.ClientSession:
    """A client session of our own, for when the caller does not provide one.

    We deliberately use ThreadedResolver (socket.getaddrinfo via NSS)
    rather than aiohttp's default AsyncResolver (aiodns/c-ares). aiodns
    talks UDP/53 directly to the resolvers in /etc/resolv.conf, which
    fails on some HAOS/Docker setups where the OS resolves names fine
    via systemd-resolved, NSS or /etc/hosts but UDP to the upstream
    DNS is blocked or misconfigured. ThreadedResolver goes through the
    same path the rest of the OS uses and works in all of these cases.

    Default ``CookieJar`` (i.e. ``unsafe=False``) refuses cookies set
    for IP literals; we only ever talk to the portal hostnames so the
    stricter mode adds defence-in-depth at zero cost.
    """
    return aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(),
        timeout=timeout,
        connector=aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver()),
    )


async def load_landing_page(
    session: aiohttp.ClientSession,
    endpoints: PortalEndpoints,
    *,
    headers: dict[str, str],
    timeout: aiohttp.ClientTimeout,
) -> None:
    """GET the portal's landing page; raise :class:`EauxDeMarseilleApiError` on failure."""
    try:
        # Same redirect policy as every other call: the landing page
        # carries no secret, but nothing should leave the portal's origin.
        async with _http.open_following_redirects(
            session,
            "GET",
            f"{endpoints.url}/",
            headers=headers,
            timeout=timeout,
            allowed_host=endpoints.host,
        ) as response:
            await response.read()
            _LOGGER.debug(
                "Portal landing page: HTTP %d (final URL: %s)", response.status, response.url
            )
            if response.status >= 400:
                raise EauxDeMarseilleApiError(
                    f"Portal returned HTTP {response.status} on landing page "
                    f"(final URL: {response.url})"
                )
    except (aiohttp.ClientError, TimeoutError) as err:
        if _is_dns_failure(err):
            raise EauxDeMarseilleApiError(
                f"DNS resolution failed for {endpoints.host}. "
                "Check Home Assistant's DNS configuration "
                "(Settings -> System -> Network -> Network adapter); "
                f"underlying error: {err}"
            ) from err
        raise EauxDeMarseilleApiError(f"Failed to reach portal: {err}") from err


def _is_dns_failure(err: BaseException) -> bool:
    """Detect DNS resolution failures from the connector.

    aiohttp normalises both ``AsyncResolver`` (aiodns/c-ares) and
    ``ThreadedResolver`` (``socket.getaddrinfo``) failures into
    :class:`aiohttp.ClientConnectorDNSError` (added in aiohttp 3.10).
    """
    return isinstance(err, aiohttp.ClientConnectorDNSError)

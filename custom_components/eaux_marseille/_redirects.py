"""HTTP redirect handling for the Eaux de Marseille API client.

We follow redirects manually (instead of relying on
``aiohttp.allow_redirects=True``) for two reasons:

1. **Security** — the request headers carry the AEL session token, and
   the request body carries the user's password during the auth POST.
   Forwarding either to an attacker-controlled host (open redirect,
   compromised CDN edge) is exactly CVE-2018-18074. We refuse any
   off-portal or scheme-downgraded redirect.
2. **Observability** — we log each ``Location`` we follow at INFO so
   portal-side routing changes are diagnosable from a single user log.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from yarl import URL

from .const import MAX_REDIRECTS, PORTAL_HOST
from .exceptions import EauxDeMarseilleApiError

_LOGGER = logging.getLogger(__name__)


async def resolve(
    response: aiohttp.ClientResponse,
    method: str,
    url: str,
    *,
    hop: int,
    initial_url: str,
) -> tuple[str, str]:
    """Validate a 3xx response and return ``(next_method, next_url)``.

    Raises :class:`EauxDeMarseilleApiError` if the redirect target is
    off-portal, downgrades scheme, has no ``Location`` header, or if
    we've exceeded :data:`MAX_REDIRECTS` hops.
    """
    next_url = await _validated_target(response, url)

    _LOGGER.info(
        "Following HTTP %d redirect: %s → %s",
        response.status,
        url,
        next_url,
    )
    if hop >= MAX_REDIRECTS:
        raise EauxDeMarseilleApiError(
            f"Too many redirects ({MAX_REDIRECTS}) starting from "
            f"{initial_url}; last hop: {next_url}"
        )

    # 301/302/303 + POST → demote to GET (browser convention).
    # 307/308 preserve the method and the body, which is what the
    # portal's auth POST expects.
    if response.status in (301, 302, 303) and method == "POST":
        method = "GET"
    return method, next_url


def drop_body_after_get_redirect(
    kwargs: dict[str, Any],
    hop: int,
    method: str,
) -> dict[str, Any]:
    """Strip the request body when a redirect demoted us to GET."""
    if hop == 0 or method != "GET":
        return kwargs
    pruned = dict(kwargs)
    pruned.pop("json", None)
    pruned.pop("data", None)
    return pruned


async def _validated_target(response: aiohttp.ClientResponse, url: str) -> str:
    """Compute and validate the absolute target URL of a 3xx response."""
    location = response.headers.get("Location")
    if not location:
        body = await response.text()
        raise EauxDeMarseilleApiError(
            f"HTTP {response.status} at {url} with no Location header. "
            f"Body starts with: {body[:200]!r}"
        )
    target = URL(url).join(URL(location))
    if target.host != PORTAL_HOST:
        raise EauxDeMarseilleApiError(
            f"Refusing to follow {response.status} redirect to off-portal "
            f"host {target.host!r} (from {url})"
        )
    if target.scheme != "https":
        raise EauxDeMarseilleApiError(
            f"Refusing to follow {response.status} redirect to non-HTTPS "
            f"scheme {target.scheme!r} (from {url})"
        )
    return str(target)

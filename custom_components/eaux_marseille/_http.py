"""Low-level HTTP transport for the Eaux de Marseille API client.

This module is intentionally free of any portal-specific business logic.
It implements three concerns that compose to form
:func:`request_with_retry`:

* Retry with exponential backoff on transient errors (timeouts, network
  errors, 5xx responses).
* Manual redirect following with same-origin enforcement so we never
  forward credentials to an attacker-controlled host
  (CVE-2018-18074-class protection).
* JSON parsing with a clear error message when the body is HTML
  (typical of WAF challenges or login redirects).

The functions here take an :class:`aiohttp.ClientSession` as an argument
so callers can reuse a connection pool and a cookie jar across
authentication and data calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
from yarl import URL

from .const import (
    BACKOFF_BASE_S,
    MAX_REDIRECTS,
    MAX_RETRIES,
    PORTAL_HOST,
)
from .exceptions import EauxDeMarseilleApiError

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------


async def request_with_retry(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    timeout: aiohttp.ClientTimeout,
    headers: dict[str, str],
    **kwargs: Any,
) -> dict[str, Any]:
    """Send ``method url`` and return the parsed JSON body.

    Retries up to :data:`MAX_RETRIES` times on transient failures
    (network errors, timeouts, 5xx responses). Does not retry on 4xx
    responses — those are not transient and retrying them would just
    burn the user's quota.

    Raises :class:`EauxDeMarseilleApiError` if all attempts fail or if
    the body is not valid JSON.
    """
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            return await _send_following_redirects(
                session,
                method,
                url,
                timeout=timeout,
                headers=headers,
                **kwargs,
            )
        except (TimeoutError, aiohttp.ClientError) as err:
            last_error = err
            if attempt < MAX_RETRIES - 1:
                delay = BACKOFF_BASE_S * (2**attempt)
                _LOGGER.debug(
                    "Request to %s failed (attempt %d/%d), retrying in %.1fs: %s",
                    url,
                    attempt + 1,
                    MAX_RETRIES,
                    delay,
                    err,
                )
                await asyncio.sleep(delay)

    raise EauxDeMarseilleApiError(
        f"Request to {url} failed after {MAX_RETRIES} attempts: {last_error}"
    )


# ---------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------


async def _send_following_redirects(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    timeout: aiohttp.ClientTimeout,
    headers: dict[str, str],
    **kwargs: Any,
) -> dict[str, Any]:
    """Send a single request, manually walking through any redirect chain."""
    current_method, current_url = method, url

    for hop in range(MAX_REDIRECTS + 1):
        request_kwargs = _drop_body_after_get_redirect(kwargs, hop, current_method)

        async with session.request(
            current_method,
            current_url,
            timeout=timeout,
            headers=headers,
            allow_redirects=False,
            **request_kwargs,
        ) as response:
            content_type = response.headers.get("Content-Type", "<none>")
            _LOGGER.debug(
                "%s %s → HTTP %d (content-type=%s)",
                current_method,
                current_url,
                response.status,
                content_type,
            )

            if 300 <= response.status < 400:
                current_method, current_url = await _resolve_redirect(
                    response,
                    current_method,
                    current_url,
                    hop=hop,
                    initial_url=url,
                )
                continue

            if 400 <= response.status < 500:
                text = await response.text()
                raise EauxDeMarseilleApiError(
                    f"HTTP {response.status} at {current_url}: {text[:200]}"
                )

            if response.status >= 500:
                # Raised as ClientResponseError so the outer retry loop
                # picks it up as transient.
                text = await response.text()
                raise aiohttp.ClientResponseError(
                    response.request_info,
                    response.history,
                    status=response.status,
                    message=text[:200],
                )

            return await _parse_json_or_raise(response, current_url, content_type)

    # Unreachable: _resolve_redirect raises before hop > MAX_REDIRECTS.
    raise EauxDeMarseilleApiError(  # pragma: no cover
        f"Redirect loop guard failed for {url}"
    )


async def _resolve_redirect(
    response: aiohttp.ClientResponse,
    method: str,
    url: str,
    *,
    hop: int,
    initial_url: str,
) -> tuple[str, str]:
    """Validate a 3xx response and return ``(next_method, next_url)``.

    Refuses redirects whose target is on a different host or that
    downgrade the scheme to ``http://``. Both would let an attacker who
    can influence the ``Location`` header (open redirect, compromised
    CDN edge) exfiltrate the session token or the credential body.
    """
    location = response.headers.get("Location")
    if not location:
        body = await response.text()
        raise EauxDeMarseilleApiError(
            f"HTTP {response.status} at {url} with no Location header. "
            f"Body starts with: {body[:200]!r}"
        )

    next_url_obj = URL(url).join(URL(location))

    if next_url_obj.host != PORTAL_HOST:
        raise EauxDeMarseilleApiError(
            f"Refusing to follow {response.status} redirect to off-portal "
            f"host {next_url_obj.host!r} (from {url})"
        )
    if next_url_obj.scheme != "https":
        raise EauxDeMarseilleApiError(
            f"Refusing to follow {response.status} redirect to non-HTTPS "
            f"scheme {next_url_obj.scheme!r} (from {url})"
        )

    next_url = str(next_url_obj)
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


async def _parse_json_or_raise(
    response: aiohttp.ClientResponse,
    url: str,
    content_type: str,
) -> dict[str, Any]:
    """Parse the body as JSON, surfacing HTML responses as a clear error."""
    try:
        data: dict[str, Any] = await response.json(content_type=None)
    except (json.JSONDecodeError, aiohttp.ContentTypeError) as err:
        body = await response.text()
        raise EauxDeMarseilleApiError(
            f"Expected JSON from {url} but got "
            f"content-type={content_type}, status={response.status}. "
            f"Body starts with: {body[:200]!r}"
        ) from err
    return data


def _drop_body_after_get_redirect(
    kwargs: dict[str, Any],
    hop: int,
    method: str,
) -> dict[str, Any]:
    """Strip the request body when a redirect demoted us to GET.

    ``hop == 0`` means we're still on the initial request, so the
    caller's body is intentional and must be kept. After a 301/302/303
    that demoted POST to GET, the body would be meaningless and aiohttp
    would happily send it anyway, so drop it.
    """
    if hop == 0 or method != "GET":
        return kwargs
    request_kwargs = dict(kwargs)
    request_kwargs.pop("json", None)
    request_kwargs.pop("data", None)
    return request_kwargs

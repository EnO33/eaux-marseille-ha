"""Low-level HTTP transport for the Eaux de Marseille API client.

* Retry with exponential backoff on transient errors (timeouts, network
  errors, 5xx responses).
* Manual redirect handling (delegated to :mod:`._redirects`) so we can
  enforce same-origin, preserve POST bodies on 307/308, and log every
  ``Location`` we follow.
* JSON parsing with a clear error when the body is HTML (typical of
  WAF challenges or login redirects).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

from . import _redirects
from .const import BACKOFF_BASE_S, MAX_REDIRECTS, MAX_RETRIES
from .exceptions import EauxDeMarseilleApiError

_LOGGER = logging.getLogger(__name__)


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

    Retries up to :data:`MAX_RETRIES` times on transient failures.
    Does not retry on 4xx responses — those are not transient.
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


async def _send_following_redirects(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    timeout: aiohttp.ClientTimeout,
    headers: dict[str, str],
    **kwargs: Any,
) -> dict[str, Any]:
    """Send a single request, walking through any redirect chain."""
    cur_method, cur_url = method, url

    for hop in range(MAX_REDIRECTS + 1):
        request_kwargs = _redirects.drop_body_after_get_redirect(kwargs, hop, cur_method)

        async with session.request(
            cur_method,
            cur_url,
            timeout=timeout,
            headers=headers,
            allow_redirects=False,
            **request_kwargs,
        ) as response:
            ct = response.headers.get("Content-Type", "<none>")
            _LOGGER.debug(
                "%s %s → HTTP %d (content-type=%s)",
                cur_method,
                cur_url,
                response.status,
                ct,
            )

            if 300 <= response.status < 400:
                cur_method, cur_url = await _redirects.resolve(
                    response,
                    cur_method,
                    cur_url,
                    hop=hop,
                    initial_url=url,
                )
                continue

            if 400 <= response.status < 500:
                text = await response.text()
                raise EauxDeMarseilleApiError(f"HTTP {response.status} at {cur_url}: {text[:200]}")

            if response.status >= 500:
                # Raise as ClientResponseError so the outer retry loop
                # treats it as transient.
                text = await response.text()
                raise aiohttp.ClientResponseError(
                    response.request_info,
                    response.history,
                    status=response.status,
                    message=text[:200],
                )

            return await _parse_json_or_raise(response, cur_url, ct)

    raise EauxDeMarseilleApiError(  # pragma: no cover
        f"Redirect loop guard failed for {url}"
    )


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

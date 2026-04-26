"""Low-level HTTP transport for the Eaux de Marseille API client.

* Retry with exponential backoff on transient errors — delegated to
  :mod:`tenacity`.
* Manual redirect handling (delegated to :mod:`._redirects`) so we can
  enforce same-origin, preserve POST bodies on 307/308, and log every
  ``Location`` we follow.
* JSON parsing with a clear error when the body is HTML (typical of
  WAF challenges or login redirects).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp
from tenacity import (
    AsyncRetrying,
    RetryError,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from . import _redirects
from .const import BACKOFF_BASE_S, MAX_REDIRECTS, MAX_RETRIES
from .exceptions import EauxDeMarseilleApiError

_LOGGER = logging.getLogger(__name__)

_RETRY_ON = (TimeoutError, aiohttp.ClientError)


async def request_with_retry(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    timeout: aiohttp.ClientTimeout,
    headers: dict[str, str],
    allowed_host: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Send ``method url`` and return the parsed JSON body.

    Retries up to :data:`MAX_RETRIES` times with exponential backoff
    (1s, 2s, 4s) on transient failures (timeouts, network errors, 5xx).
    Does not retry on 4xx — those are not transient.

    ``allowed_host`` bounds the redirect handler: requests are refused
    if a 3xx points to any other host (CVE-2018-18074 protection).
    """
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(MAX_RETRIES),
            wait=wait_exponential(multiplier=BACKOFF_BASE_S, max=8),
            retry=retry_if_exception_type(_RETRY_ON),
            before_sleep=before_sleep_log(_LOGGER, logging.DEBUG),
            reraise=True,
        ):
            with attempt:
                return await _send_following_redirects(
                    session,
                    method,
                    url,
                    timeout=timeout,
                    headers=headers,
                    allowed_host=allowed_host,
                    **kwargs,
                )
    except _RETRY_ON as err:
        raise EauxDeMarseilleApiError(
            f"Request to {url} failed after {MAX_RETRIES} attempts: {err}"
        ) from err
    except RetryError as err:  # pragma: no cover  # tenacity wraps if reraise=False
        raise EauxDeMarseilleApiError(
            f"Request to {url} failed after {MAX_RETRIES} attempts: {err}"
        ) from err
    raise EauxDeMarseilleApiError(  # pragma: no cover
        f"Retry loop exited without a result for {url}"
    )


async def _send_following_redirects(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    timeout: aiohttp.ClientTimeout,
    headers: dict[str, str],
    allowed_host: str,
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
                    allowed_host=allowed_host,
                )
                continue

            await _raise_for_status(response, cur_url)
            return await _parse_json_or_raise(response, cur_url, ct)

    raise EauxDeMarseilleApiError(  # pragma: no cover
        f"Redirect loop guard failed for {url}"
    )


async def _raise_for_status(response: aiohttp.ClientResponse, url: str) -> None:
    """Raise on 4xx (no retry) or 5xx (re-raised as ClientResponseError so retry kicks in)."""
    if 400 <= response.status < 500:
        text = await response.text()
        raise EauxDeMarseilleApiError(f"HTTP {response.status} at {url}: {text[:200]}")
    if response.status >= 500:
        text = await response.text()
        raise aiohttp.ClientResponseError(
            response.request_info,
            response.history,
            status=response.status,
            message=text[:200],
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

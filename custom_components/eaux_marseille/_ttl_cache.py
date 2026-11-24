"""A value fetched at most once per time-to-live, one fetch at a time.

The poll asks some portal data far more often than it changes: the
contract's granularities (#126) and the billed reading and history, which
move once a billing cycle (#127). :class:`TtlCache` holds such a value
between fetches. Concurrent callers (coordinator poll, statistics import)
share one fetch through a lock.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from .exceptions import EauxDeMarseilleApiError, EauxDeMarseilleSessionExpiredError

_LOGGER = logging.getLogger(__name__)


class TtlCache[T]:
    """Cache the result of ``fetch`` for ``ttl_s`` seconds.

    With ``stale_on_error``, a failed refresh (any API error but a session
    expiry, which the caller's ``SessionGuard`` must see) returns the
    previous value and leaves it expired, so the next call tries again.
    Without a previous value the error propagates.
    """

    def __init__(
        self, fetch: Callable[[], Awaitable[T]], ttl_s: float, *, stale_on_error: bool = False
    ) -> None:
        self._fetch = fetch
        self._ttl_s = ttl_s
        self._stale_on_error = stale_on_error
        self._lock = asyncio.Lock()
        # (value, monotonic expiry); a tuple so that ``None`` is a cacheable value.
        self._held: tuple[T, float] | None = None

    async def get(self) -> T:
        async with self._lock:
            held = self._held
            if held is not None and time.monotonic() < held[1]:
                return held[0]
            try:
                value = await self._fetch()
            except EauxDeMarseilleApiError as err:
                expired_session = isinstance(err, EauxDeMarseilleSessionExpiredError)
                if expired_session or not self._stale_on_error or held is None:
                    raise
                _LOGGER.debug(
                    "Refresh failed (HTTP status %s); keeping the cached value", err.status
                )
                return held[0]
            self._held = (value, time.monotonic() + self._ttl_s)
            return value

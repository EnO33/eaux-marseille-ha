"""Serialised authentication with a single-shot session recovery.

Both the web portal client (:mod:`.api`) and the mobility client
(:mod:`._mobile`) cache a session token, authenticate when none is held and
re-authenticate once when the backend reports the session expired.
:class:`SessionGuard` is that algorithm, shared.

The client is used concurrently (coordinator poll and statistics import), so
authentication runs under a lock. Each successful login bumps a generation
counter: a caller whose request failed with an expired session only
re-authenticates if nobody did since its session was issued, so N concurrent
expiries cost one login instead of N logins resetting each other's tokens.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .exceptions import EauxDeMarseilleSessionExpiredError

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")


class SessionGuard:
    """Authenticate on demand, one login at a time, and recover once on expiry."""

    def __init__(
        self,
        name: str,
        *,
        is_authenticated: Callable[[], bool],
        authenticate: Callable[[], Awaitable[None]],
        invalidate: Callable[[], None],
    ) -> None:
        self._name = name
        self._is_authenticated = is_authenticated
        self._authenticate = authenticate
        self._invalidate = invalidate
        self._lock = asyncio.Lock()
        self._generation = 0

    async def ensure(self, expired: int | None = None) -> int:
        """Make sure a session is held and return its generation.

        ``expired`` is the generation a caller saw expire: the session is
        dropped and renewed only if it is still that one.
        """
        async with self._lock:
            if expired == self._generation:
                self._invalidate()
            if not self._is_authenticated():
                await self._authenticate()
                self._generation += 1
            return self._generation

    async def run(self, action: Callable[[], Awaitable[_T]]) -> _T:
        """Run ``action`` under a session, re-authenticating once on expiry.

        Any other error (auth failure, transport, 5xx after retries)
        propagates unchanged, as does a second expiry in a row.
        """
        generation = await self.ensure()
        try:
            return await action()
        except EauxDeMarseilleSessionExpiredError as err:
            # The error carries the request URL (contract number) and a body
            # excerpt: keep them out of the default log level.
            _LOGGER.info("%s session expired; re-authenticating", self._name)
            _LOGGER.debug("%s session expiry detail: %s", self._name, err)
            await self.ensure(expired=generation)
            return await action()

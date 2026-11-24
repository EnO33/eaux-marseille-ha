"""TtlCache: fetch at most once per time-to-live, keep the last value on request (#127)."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.eaux_marseille._ttl_cache import TtlCache
from custom_components.eaux_marseille.exceptions import (
    EauxDeMarseilleApiError,
    EauxDeMarseilleSessionExpiredError,
)


class _Fetch:
    """A fetch that returns the queued answers in order and counts its calls."""

    def __init__(self, *answers: object) -> None:
        self._answers = list(answers)
        self.calls = 0

    async def __call__(self) -> object:
        self.calls += 1
        await asyncio.sleep(0)
        answer = self._answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


async def test_value_is_reused_within_its_ttl() -> None:
    fetch = _Fetch("a")
    cache = TtlCache(fetch, 3600)

    assert [await cache.get(), await cache.get()] == ["a", "a"]
    assert fetch.calls == 1


async def test_expired_value_is_fetched_again() -> None:
    fetch = _Fetch("a", "b")
    cache = TtlCache(fetch, 0)

    assert [await cache.get(), await cache.get()] == ["a", "b"]


async def test_none_is_a_cached_value() -> None:
    """An "unknown" answer is worth keeping too (#126)."""
    fetch = _Fetch(None)
    cache = TtlCache(fetch, 3600)

    assert await cache.get() is None
    assert await cache.get() is None
    assert fetch.calls == 1


async def test_concurrent_callers_share_one_fetch() -> None:
    fetch = _Fetch("a")
    cache = TtlCache(fetch, 3600)

    assert await asyncio.gather(cache.get(), cache.get()) == ["a", "a"]
    assert fetch.calls == 1


async def test_stale_value_survives_a_failed_refresh_and_is_retried() -> None:
    fetch = _Fetch("a", EauxDeMarseilleApiError("down", status=404), "b")
    cache = TtlCache(fetch, 3600, stale_on_error=True)
    await cache.get()
    cache._held = ("a", float("-inf"))  # the TTL ran out

    assert await cache.get() == "a"
    # Still expired: the next call asks again instead of waiting a full TTL.
    assert await cache.get() == "b"
    assert fetch.calls == 3


@pytest.mark.parametrize(
    ("stale_on_error", "warm"),
    [(True, False), (False, True)],
    ids=["no-previous-value", "stale-not-wanted"],
)
async def test_failed_fetch_propagates(stale_on_error: bool, warm: bool) -> None:
    answers: list[object] = ["a"] if warm else []
    fetch = _Fetch(*answers, EauxDeMarseilleApiError("down", status=404))
    cache = TtlCache(fetch, 0, stale_on_error=stale_on_error)
    if warm:
        await cache.get()

    with pytest.raises(EauxDeMarseilleApiError):
        await cache.get()


async def test_session_expiry_is_never_hidden() -> None:
    """SessionGuard must see the expiry to log in again, stale value or not."""
    fetch = _Fetch("a", EauxDeMarseilleSessionExpiredError("401", status=401))
    cache = TtlCache(fetch, 0, stale_on_error=True)
    await cache.get()

    with pytest.raises(EauxDeMarseilleSessionExpiredError):
        await cache.get()

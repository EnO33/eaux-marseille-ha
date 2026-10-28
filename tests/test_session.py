"""Tests for :class:`._session.SessionGuard` (no Home Assistant needed)."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.eaux_marseille._session import SessionGuard
from custom_components.eaux_marseille.exceptions import (
    EauxDeMarseilleApiError,
    EauxDeMarseilleSessionExpiredError,
)


class _FakeBackend:
    """A token holder whose login yields to the event loop, like a real one."""

    def __init__(self) -> None:
        self.token: str | None = None
        self.logins = 0
        self.guard = SessionGuard(
            "Test",
            is_authenticated=lambda: self.token is not None,
            authenticate=self.login,
            invalidate=self.drop,
        )

    async def login(self) -> None:
        self.token = None
        await asyncio.sleep(0)
        self.logins += 1
        self.token = f"token-{self.logins}"

    def drop(self) -> None:
        self.token = None


async def test_concurrent_first_use_logs_in_once() -> None:
    backend = _FakeBackend()

    async def action() -> str | None:
        return backend.token

    results = await asyncio.gather(*(backend.guard.run(action) for _ in range(3)))

    assert backend.logins == 1
    assert results == ["token-1"] * 3


async def test_concurrent_expiry_logs_in_again_once() -> None:
    backend = _FakeBackend()
    await backend.guard.ensure()
    both_failed = asyncio.Event()
    failures = 0

    async def action() -> str | None:
        nonlocal failures
        if backend.token == "token-1":
            failures += 1
            if failures == 2:
                both_failed.set()
            await both_failed.wait()
            raise EauxDeMarseilleSessionExpiredError("401")
        return backend.token

    results = await asyncio.gather(backend.guard.run(action), backend.guard.run(action))

    assert backend.logins == 2
    assert results == ["token-2", "token-2"]


async def test_second_expiry_in_a_row_propagates() -> None:
    backend = _FakeBackend()

    async def action() -> None:
        raise EauxDeMarseilleSessionExpiredError("401")

    with pytest.raises(EauxDeMarseilleSessionExpiredError):
        await backend.guard.run(action)
    assert backend.logins == 2


async def test_other_errors_do_not_trigger_a_login() -> None:
    backend = _FakeBackend()

    async def action() -> None:
        raise EauxDeMarseilleApiError("404")

    with pytest.raises(EauxDeMarseilleApiError):
        await backend.guard.run(action)
    assert backend.logins == 1

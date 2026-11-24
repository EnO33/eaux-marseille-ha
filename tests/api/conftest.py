"""Fixtures for the API client tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from aioresponses import aioresponses

from custom_components.eaux_marseille.api import EauxDeMarseilleClient

from .portal import CONTRACT_ID, LOGIN, register_auth_flow, register_granularities


# Override pytest-homeassistant-custom-component's autouse fixtures that check
# for lingering threads — our API client uses aiohttp which spawns a background
# shutdown thread that lives beyond test teardown. These tests don't touch HA,
# so the cleanup check is not relevant here.
@pytest.fixture(autouse=True)
def verify_cleanup():
    yield


@pytest.fixture(autouse=True)
def expected_lingering_tasks():
    return True


@pytest.fixture(autouse=True)
def expected_lingering_timers():
    return True


@pytest.fixture
async def client() -> AsyncIterator[EauxDeMarseilleClient]:
    """Return a client instance with fake credentials.

    The mobility fallback is disabled here so these tests exercise the web
    portal in isolation (and don't hit the unmocked mobility API); the
    fallback has its own dedicated test.
    """
    c = EauxDeMarseilleClient(login=LOGIN, password="password", contract_id=CONTRACT_ID)
    c._mobile = None
    yield c
    await c.close()


@pytest.fixture
def granularities() -> tuple[str, ...] | None:
    """Granularities the mocked contract lists; ``None`` leaves the endpoint to the test.

    Override it with ``@pytest.mark.parametrize("granularities", ...)``.
    """
    return ("MOIS", "JOURNEE")


@pytest.fixture
def mock_auth(granularities: tuple[str, ...] | None) -> Iterator[aioresponses]:
    """Register all authentication endpoint mocks, once each, and the granularities."""
    with aioresponses() as m:
        register_auth_flow(m)
        if granularities is not None:
            register_granularities(m, *granularities)
        yield m

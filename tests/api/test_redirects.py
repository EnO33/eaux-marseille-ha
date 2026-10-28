"""Manual redirect handling: same-origin policy, hop limit, verb rewriting."""

from __future__ import annotations

import socket

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey
from aioresponses import aioresponses

from custom_components.eaux_marseille.api import EauxDeMarseilleApiError, EauxDeMarseilleClient

from .portal import (
    CONTRACT_ID,
    CONTRACT_URL,
    LOGIN,
    LOGIN_PAYLOAD,
    LOGIN_URL,
    PORTAL_URL,
    TOKEN_URL,
    register_token,
)


class TestLoginRedirects:
    """Redirects on the login POST."""

    async def test_authenticate_follows_307_redirect(self, client: EauxDeMarseilleClient) -> None:
        """Authentication follows a 307 redirect on the login POST and preserves the body."""
        redirected_url = f"{LOGIN_URL}/v2"
        with aioresponses() as m:
            register_token(m)
            m.post(LOGIN_URL, status=307, headers={"Location": redirected_url})
            m.post(redirected_url, payload=LOGIN_PAYLOAD)
            m.get(CONTRACT_URL, payload={"numContrat": CONTRACT_ID})

            await client.authenticate()

    async def test_authenticate_redirect_without_location(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A 3xx with no Location header surfaces as a clear API error."""
        with aioresponses() as m:
            register_token(m)
            m.post(LOGIN_URL, status=307, body="redirect without location")

            with pytest.raises(EauxDeMarseilleApiError, match="no Location header"):
                await client.authenticate()

    @pytest.mark.parametrize(
        ("location", "match"),
        [
            # Off-portal redirects must be refused (CVE-2018-18074-class leak):
            # if followed, the auth POST body (containing the user's password)
            # and the token header would be forwarded to the attacker.
            ("https://attacker.example/steal", "off-portal host"),
            # Protocol-relative redirects to foreign hosts too.
            ("//attacker.example/steal", "off-portal host"),
            # HTTPS to HTTP downgrade, even on the same host.
            ("http://espaceclients.eauxdemarseille.fr/x", "non-HTTPS scheme"),
        ],
    )
    async def test_authenticate_rejects_unsafe_redirect(
        self, client: EauxDeMarseilleClient, location: str, match: str
    ) -> None:
        """An unsafe Location on the login POST is refused."""
        with aioresponses() as m:
            register_token(m)
            m.post(LOGIN_URL, status=307, headers={"Location": location})

            with pytest.raises(EauxDeMarseilleApiError, match=match):
                await client.authenticate()


class TestLandingPage:
    """The landing page GET that opens the portal session."""

    async def test_authenticate_landing_page_failure(self, client: EauxDeMarseilleClient) -> None:
        """A 4xx/5xx on the landing page is reported with a clear message."""
        with aioresponses() as m:
            m.get(PORTAL_URL + "/", status=503, body="maintenance")

            with pytest.raises(EauxDeMarseilleApiError, match="HTTP 503 on landing page"):
                await client.authenticate()

    @pytest.mark.parametrize(
        ("location", "match"),
        [
            ("https://attacker.example/", "off-portal host"),
            ("http://espaceclients.eauxdemarseille.fr/", "non-HTTPS scheme"),
        ],
    )
    async def test_authenticate_landing_page_rejects_unsafe_redirect(
        self, client: EauxDeMarseilleClient, location: str, match: str
    ) -> None:
        """The landing page obeys the same redirect policy as API calls."""
        with aioresponses() as m:
            m.get(PORTAL_URL + "/", status=302, headers={"Location": location})

            with pytest.raises(EauxDeMarseilleApiError, match=match):
                await client.authenticate()
            assert len(m.requests) == 1

    async def test_authenticate_landing_page_follows_same_origin_redirect(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A same-origin redirect on the landing page is still followed."""
        with aioresponses() as m:
            m.get(PORTAL_URL + "/", status=302, headers={"Location": "/accueil"})
            m.get(PORTAL_URL + "/accueil", status=503, body="maintenance")

            with pytest.raises(EauxDeMarseilleApiError, match="HTTP 503 on landing page"):
                await client.authenticate()

    async def test_authenticate_landing_page_network_error(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A network error on the landing page is wrapped in EauxDeMarseilleApiError."""
        with aioresponses() as m:
            m.get(PORTAL_URL + "/", exception=aiohttp.ClientConnectionError("DNS"))

            with pytest.raises(EauxDeMarseilleApiError, match="Failed to reach portal"):
                await client.authenticate()

    async def test_authenticate_landing_page_dns_failure(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A DNS resolution failure surfaces a clear, actionable message."""
        key = ConnectionKey(
            host="espaceclients.eauxdemarseille.fr",
            port=443,
            is_ssl=True,
            ssl=True,
            proxy=None,
            proxy_auth=None,
            proxy_headers_hash=None,
        )
        dns_err = aiohttp.ClientConnectorDNSError(key, socket.gaierror("DNS"))

        with aioresponses() as m:
            m.get(PORTAL_URL + "/", exception=dns_err)

            with pytest.raises(EauxDeMarseilleApiError, match="DNS resolution failed"):
                await client.authenticate()


class TestRedirectChain:
    """Edge cases of the manual redirect handling."""

    async def test_too_many_redirects(self, client: EauxDeMarseilleClient) -> None:
        """Hitting _MAX_REDIRECTS surfaces a clear error mentioning the chain."""
        with aioresponses() as m:
            m.get(PORTAL_URL + "/", body="<html></html>")
            # 6 hops > _MAX_REDIRECTS (5)
            for i in range(7):
                m.post(
                    TOKEN_URL + ("" if i == 0 else f"/h{i}"),
                    status=307,
                    headers={"Location": f"{TOKEN_URL}/h{i + 1}"},
                )

            with pytest.raises(EauxDeMarseilleApiError, match="Too many redirects"):
                await client.authenticate()

    async def test_post_to_get_redirect_drops_body(self, client: EauxDeMarseilleClient) -> None:
        """A 303 redirect on a POST converts the next hop to GET and drops body."""
        target = f"{TOKEN_URL}/v2"
        with aioresponses() as m:
            m.get(PORTAL_URL + "/", body="<html></html>")
            m.post(TOKEN_URL, status=303, headers={"Location": target})
            # Note: aioresponses requires the redirected verb to be matched.
            # After 303 the request must be GET.
            m.get(target, payload={"token": "redirected-token"})
            m.post(
                LOGIN_URL,
                payload={
                    "tokenAuthentique": "fake-ael-token",
                    "utilisateurInfo": {"identifiant": LOGIN},
                },
            )
            m.get(CONTRACT_URL, payload={"numContrat": CONTRACT_ID})

            await client.authenticate()

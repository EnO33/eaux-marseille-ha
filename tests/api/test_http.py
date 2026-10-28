"""The HTTP layer: JSON decoding, retries, client lifecycle, log redaction."""

from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.eaux_marseille.api import (
    EauxDeMarseilleApiError,
    EauxDeMarseilleAuthError,
    EauxDeMarseilleClient,
)

from .portal import (
    API_BASE,
    CONTRACT_ID,
    CONTRACT_URL,
    LOGIN,
    LOGIN_URL,
    PORTAL_URL,
    TOKEN_URL,
    register_token,
)


class TestJSONErrors:
    """Responses that do not look like the expected JSON shape."""

    async def test_html_response_surfaces_clear_error(self, client: EauxDeMarseilleClient) -> None:
        """A 200 with HTML body (e.g. WAF challenge) is reported with the body excerpt."""
        with aioresponses() as m:
            m.get(PORTAL_URL + "/", body="<html></html>")
            m.post(
                TOKEN_URL,
                body="<html><body>Cloudflare challenge</body></html>",
                content_type="text/html",
            )

            with pytest.raises(EauxDeMarseilleApiError) as excinfo:
                await client.authenticate()
            assert "Expected JSON" in str(excinfo.value) or "unexpected response" in str(
                excinfo.value
            )

    async def test_json_list_body_is_api_error(self, client: EauxDeMarseilleClient) -> None:
        """A JSON list where an object is expected is an API error, not AttributeError."""
        with aioresponses() as m:
            m.get(PORTAL_URL + "/", body="<html></html>")
            m.post(TOKEN_URL, payload=["unexpected"])

            with pytest.raises(EauxDeMarseilleApiError, match="Expected a JSON object"):
                await client.authenticate()


class TestRetry:
    """Test retry behavior on transient errors."""

    async def test_retry_on_server_error(self, client: EauxDeMarseilleClient) -> None:
        """5xx errors trigger a retry that eventually succeeds."""
        with aioresponses() as m:
            # First attempt fails, second succeeds
            m.post(TOKEN_URL, status=503)
            register_token(m)
            m.post(
                LOGIN_URL,
                payload={
                    "tokenAuthentique": "fake-ael-token",
                    "utilisateurInfo": {"identifiant": LOGIN},
                },
            )
            m.get(CONTRACT_URL, payload={"numContrat": CONTRACT_ID})

            await client.authenticate()


class TestClientLifecycle:
    """Test client creation and teardown."""

    async def test_close(self, client: EauxDeMarseilleClient) -> None:
        """close() does not raise."""
        await client.close()

    async def test_default_timeout(self) -> None:
        """Client uses default timeout of 15s."""
        c = EauxDeMarseilleClient("a", "b", "c")
        assert c._timeout.total == 15
        await c.close()

    async def test_custom_timeout(self) -> None:
        """Client accepts a custom timeout."""
        c = EauxDeMarseilleClient("a", "b", "c", timeout=30)
        assert c._timeout.total == 30
        await c.close()

    async def test_external_session_not_closed(self) -> None:
        """When an external session is provided, close() does not close it."""
        session = aiohttp.ClientSession()
        c = EauxDeMarseilleClient("a", "b", "c", session=session)
        await c.close()
        assert not session.closed
        await session.close()


class TestRedact:
    """The client hides its contract number and login from log messages (#85)."""

    def test_redacts_contract_id(self, client: EauxDeMarseilleClient) -> None:
        """A request URL carrying the contract number is scrubbed."""
        scrubbed = client.redact(f"HTTP 500 at {API_BASE}/foo/{CONTRACT_ID}")
        assert CONTRACT_ID not in scrubbed
        assert "**REDACTED**" in scrubbed

    def test_redacts_login_whatever_its_case(self, client: EauxDeMarseilleClient) -> None:
        """The portal may echo the login in another case: it is scrubbed too."""
        scrubbed = client.redact("Login failed for USER@Example.com")
        assert "example.com" not in scrubbed.lower()

    async def test_passthrough_when_identifiers_are_empty(self) -> None:
        """Empty identifiers leave the text untouched."""
        bare = EauxDeMarseilleClient(login="", password="", contract_id="")
        try:
            assert bare.redact("transport error") == "transport error"
        finally:
            await bare.close()

    async def test_error_messages_do_not_echo_the_response_body(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A rejected login body never reaches the exception message."""
        with aioresponses() as m:
            register_token(m)
            m.post(LOGIN_URL, status=401, body=f'{{"email": "{LOGIN}", "secret": "leaky"}}')

            with pytest.raises(EauxDeMarseilleAuthError) as excinfo:
                await client.authenticate()
        assert "leaky" not in str(excinfo.value)
        assert LOGIN not in str(excinfo.value)

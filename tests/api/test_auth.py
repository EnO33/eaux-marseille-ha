"""The portal login: token, credentials, default contract."""

from __future__ import annotations

import pytest
from aioresponses import aioresponses

from custom_components.eaux_marseille.api import (
    EauxDeMarseilleApiError,
    EauxDeMarseilleAuthError,
    EauxDeMarseilleClient,
)

from .portal import (
    CONTRACT_URL,
    LOGIN,
    LOGIN_URL,
    PORTAL_URL,
    TOKEN_URL,
    register_token,
)


class TestAuthentication:
    """Test the authentication flow."""

    async def test_authenticate_success(
        self, client: EauxDeMarseilleClient, mock_auth: aioresponses
    ) -> None:
        """Authentication succeeds with valid responses."""
        await client.authenticate()

    async def test_authenticate_token_failure(self, client: EauxDeMarseilleClient) -> None:
        """A 5xx on token generation is an outage, not a credentials problem."""
        with aioresponses() as m:
            m.get(PORTAL_URL + "/", body="<html></html>")
            m.post(TOKEN_URL, status=500)
            m.post(TOKEN_URL, status=500)
            m.post(TOKEN_URL, status=500)

            with pytest.raises(EauxDeMarseilleApiError, match="Token generation failed"):
                await client.authenticate()

    @pytest.mark.parametrize("status", [400, 401, 404])
    async def test_authenticate_login_4xx_is_auth_error(
        self, client: EauxDeMarseilleClient, status: int
    ) -> None:
        """Any 4xx on the login POST means the portal rejected the credentials."""
        with aioresponses() as m:
            register_token(m)
            m.post(LOGIN_URL, status=status)

            with pytest.raises(EauxDeMarseilleAuthError, match="Login failed"):
                await client.authenticate()

    async def test_authenticate_login_outage_is_api_error(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A 503 on the login POST is sent once and must not trigger a reauth.

        Replaying a login the server may have processed could count as
        repeated failed attempts against the account lockout.
        """
        with aioresponses() as m:
            register_token(m)
            m.post(LOGIN_URL, status=503, repeat=True)

            with pytest.raises(EauxDeMarseilleApiError, match="Login failed") as excinfo:
                await client.authenticate()
            assert not isinstance(excinfo.value, EauxDeMarseilleAuthError)
            logins = [key for key in m.requests if str(key[1]).endswith("/authentification")]
            assert len(m.requests[logins[0]]) == 1

    async def test_authenticate_login_timeout_is_api_error(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A timeout on the login POST is an outage, not a credentials problem."""
        with aioresponses() as m:
            register_token(m)
            m.post(LOGIN_URL, exception=TimeoutError())

            with pytest.raises(EauxDeMarseilleApiError, match="Login failed"):
                await client.authenticate()

    async def test_authenticate_contract_rejected_is_api_error(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A 4xx after a successful login is not a credentials rejection."""
        with aioresponses() as m:
            register_token(m)
            m.post(
                LOGIN_URL,
                payload={
                    "tokenAuthentique": "fake-ael-token",
                    "utilisateurInfo": {"identifiant": LOGIN},
                },
            )
            m.get(CONTRACT_URL, status=403)

            with pytest.raises(EauxDeMarseilleApiError) as excinfo:
                await client.authenticate()
            assert not isinstance(excinfo.value, EauxDeMarseilleAuthError)

    async def test_authenticate_token_response_missing_field(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A 200 with no 'token' field is an API drift, not a credentials problem."""
        with aioresponses() as m:
            m.get(PORTAL_URL + "/", body="<html></html>")
            m.post(TOKEN_URL, payload={"unexpected": "shape"})

            with pytest.raises(EauxDeMarseilleApiError, match="missing 'token'"):
                await client.authenticate()

    async def test_authenticate_login_response_missing_field(
        self, client: EauxDeMarseilleClient
    ) -> None:
        """A 200 login without 'tokenAuthentique' lists the actual fields received."""
        with aioresponses() as m:
            register_token(m)
            m.post(LOGIN_URL, payload={"errorCode": 42, "message": "service down"})

            with pytest.raises(EauxDeMarseilleAuthError) as excinfo:
                await client.authenticate()
            # The error lists which keys we did get, to help diagnose API drift
            assert "errorCode" in str(excinfo.value)
            assert "message" in str(excinfo.value)

    @pytest.mark.parametrize("user_info", [None, [], {}, {"nom": "Doe"}])
    async def test_login_without_user_identifier_is_api_error(
        self, client: EauxDeMarseilleClient, user_info: object
    ) -> None:
        """A login without ``utilisateurInfo.identifiant`` is an API error, not a credentials one."""
        payload: dict[str, object] = {"tokenAuthentique": "fake-ael-token"}
        if user_info is not None:
            payload["utilisateurInfo"] = user_info
        with aioresponses() as m:
            register_token(m)
            m.post(LOGIN_URL, payload=payload)

            with pytest.raises(EauxDeMarseilleApiError, match="utilisateurInfo") as excinfo:
                await client.authenticate()
            assert not isinstance(excinfo.value, EauxDeMarseilleAuthError)

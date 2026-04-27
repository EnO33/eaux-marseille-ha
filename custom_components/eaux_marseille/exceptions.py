"""Exception hierarchy for the Eaux de Marseille integration.

A common base class lets callers catch any integration-specific error
with a single ``except EauxDeMarseilleError`` clause if they don't need
to distinguish between authentication and transport failures.
"""

from __future__ import annotations


class EauxDeMarseilleError(Exception):
    """Base class for all Eaux de Marseille integration errors."""


class EauxDeMarseilleAuthError(EauxDeMarseilleError):
    """Raised when authentication fails (bad credentials, expired session)."""


class EauxDeMarseilleApiError(EauxDeMarseilleError):
    """Raised when the API responds with an unexpected payload or status."""


class EauxDeMarseilleSessionExpiredError(EauxDeMarseilleApiError):
    """Raised when the portal returns 401/403 on an authenticated request.

    Distinct from the generic API error so callers (typically the
    high-level client) can transparently re-authenticate and retry the
    request once, instead of bubbling the failure up to the user.
    """

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

"""
eaux_marseille
==============

Unofficial Python client for the Eaux de Marseille customer portal
(espaceclients.eauxdemarseille.fr).

Retrieves water consumption data and exposes it as a JSON payload
suitable for use as a Home Assistant command_line sensor.
"""

from .client import ApiError, AuthenticationError, EauxDeMarseilleClient
from .config import Config

__all__ = [
    "Config",
    "EauxDeMarseilleClient",
    "AuthenticationError",
    "ApiError",
]
__version__ = "1.0.0"

"""
Configuration loader.

Settings are read exclusively from environment variables so that no
credentials are ever stored in source code or committed to version control.
"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

_REQUIRED = ("EDM_LOGIN", "EDM_PASSWORD", "EDM_CONTRACT_ID")
_DEFAULTS: dict[str, str] = {
    "EDM_TIMEOUT": "15",
    "EDM_LOG_LEVEL": "WARNING",
}


@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration."""

    login: str
    password: str
    contract_id: str
    timeout: int = 15
    log_level: str = "WARNING"

    @classmethod
    def from_env(cls) -> "Config":
        """
        Build a Config instance from environment variables.

        Required variables
        ------------------
        EDM_LOGIN         : Portal login (email address).
        EDM_PASSWORD      : Portal password.
        EDM_CONTRACT_ID   : Contract number (visible on bills and in the portal URL).

        Optional variables
        ------------------
        EDM_TIMEOUT       : HTTP request timeout in seconds (default: 15).
        EDM_LOG_LEVEL     : Python logging level (default: WARNING).
        """
        missing = [key for key in _REQUIRED if not os.getenv(key)]
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

        return cls(
            login=os.environ["EDM_LOGIN"],
            password=os.environ["EDM_PASSWORD"],
            contract_id=os.environ["EDM_CONTRACT_ID"],
            timeout=int(os.getenv("EDM_TIMEOUT", _DEFAULTS["EDM_TIMEOUT"])),
            log_level=os.getenv("EDM_LOG_LEVEL", _DEFAULTS["EDM_LOG_LEVEL"]).upper(),
        )

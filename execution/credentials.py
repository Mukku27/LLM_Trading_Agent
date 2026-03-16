import os
from typing import Optional


class CredentialManager:
    """
    Loads and validates exchange API credentials from environment variables.
    API keys should be loaded from .env via python-dotenv at application startup.
    """

    _BASE_REQUIRED = ("EXCHANGE_API_KEY", "EXCHANGE_API_SECRET")
    _PASSPHRASE_EXCHANGES = frozenset({"coinbase"})

    def __init__(self, logger) -> None:
        self.logger = logger
        self._api_key: Optional[str] = None
        self._api_secret: Optional[str] = None
        self._passphrase: Optional[str] = None

    def load(self, exchange_name: Optional[str] = None) -> bool:
        self._api_key = os.environ.get("EXCHANGE_API_KEY")
        self._api_secret = os.environ.get("EXCHANGE_API_SECRET")
        self._passphrase = os.environ.get("EXCHANGE_API_PASSPHRASE")

        required = list(self._BASE_REQUIRED)
        if exchange_name and exchange_name.lower() in self._PASSPHRASE_EXCHANGES:
            required.append("EXCHANGE_API_PASSPHRASE")

        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            self.logger.warning(
                f"[Credentials] Missing env vars: {missing}. "
                f"Live/paper trading will not be available."
            )
            return False

        self.logger.info("[Credentials] Exchange credentials loaded successfully")
        return True

    @property
    def api_key(self) -> Optional[str]:
        return self._api_key

    @property
    def api_secret(self) -> Optional[str]:
        return self._api_secret

    @property
    def passphrase(self) -> Optional[str]:
        return self._passphrase

    def has_credentials(self) -> bool:
        return bool(self._api_key and self._api_secret)

    async def validate_connectivity(self, connector) -> bool:
        """Test that credentials work by fetching balance."""
        try:
            balance = await connector.fetch_balance()
            if balance:
                self.logger.info("[Credentials] Connectivity validated")
                return True
        except Exception as e:
            self.logger.error(f"[Credentials] Connectivity test failed: {e}")
        return False

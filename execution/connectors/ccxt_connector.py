import os
from typing import Optional, List

from ccxt import async_support as ccxt

from execution.connectors.base import ExchangeConnector

_EXCHANGE_CLASSES = {
    "binance": ccxt.binance,
    "coinbase": ccxt.coinbase,
}

_DEFAULT_OPTIONS = {
    "binance": {"defaultType": "spot"},
}


class CCXTConnector(ExchangeConnector):
    """
    Generic CCXT adapter parameterized by exchange name.
    Replaces the per-exchange BinanceConnector / CoinbaseConnector classes.
    """

    def __init__(self, exchange_name: str, sandbox: bool = False) -> None:
        exchange_cls = _EXCHANGE_CLASSES.get(exchange_name.lower())
        if exchange_cls is None:
            raise ValueError(
                f"Unsupported exchange: {exchange_name}. "
                f"Available: {list(_EXCHANGE_CLASSES.keys())}"
            )

        api_key = os.environ.get("EXCHANGE_API_KEY", "")
        api_secret = os.environ.get("EXCHANGE_API_SECRET", "")
        passphrase = os.environ.get("EXCHANGE_API_PASSPHRASE", "")

        config: dict = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        }
        if passphrase:
            config["password"] = passphrase

        options = _DEFAULT_OPTIONS.get(exchange_name.lower())
        if options:
            config["options"] = options

        self.exchange = exchange_cls(config)

        if sandbox:
            self.exchange.set_sandbox_mode(True)

    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: Optional[float] = None,
    ) -> dict:
        return await self.exchange.create_order(
            symbol=symbol,
            type=order_type,
            side=side,
            amount=amount,
            price=price,
        )

    async def cancel_order(self, order_id: str, symbol: str) -> dict:
        return await self.exchange.cancel_order(order_id, symbol)

    async def fetch_balance(self) -> dict:
        return await self.exchange.fetch_balance()

    async def fetch_order(self, order_id: str, symbol: str) -> dict:
        return await self.exchange.fetch_order(order_id, symbol)

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[dict]:
        return await self.exchange.fetch_open_orders(symbol)

    async def close(self) -> None:
        await self.exchange.close()

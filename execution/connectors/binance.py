import os
from typing import Optional, List

from ccxt import async_support as ccxt

from execution.connectors.base import ExchangeConnector


class BinanceConnector(ExchangeConnector):
    """Adapter for Binance via CCXT with authenticated credentials."""

    def __init__(self, sandbox: bool = False) -> None:
        api_key = os.environ.get("EXCHANGE_API_KEY", "")
        api_secret = os.environ.get("EXCHANGE_API_SECRET", "")

        if not api_key or not api_secret:
            raise ValueError(
                "Exchange credentials are missing or empty. "
                "Set EXCHANGE_API_KEY and EXCHANGE_API_SECRET environment variables."
            )

        self.exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })

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

    async def fetch_ticker(self, symbol: str) -> dict:
        return await self.exchange.fetch_ticker(symbol)

    async def close(self) -> None:
        await self.exchange.close()

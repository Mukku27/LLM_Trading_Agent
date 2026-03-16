from abc import ABC, abstractmethod
from typing import Optional, List


class ExchangeConnector(ABC):
    """Uniform interface wrapping CCXT exchange instances."""

    @abstractmethod
    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: Optional[float] = None,
    ) -> dict:
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> dict:
        ...

    @abstractmethod
    async def fetch_balance(self) -> dict:
        ...

    @abstractmethod
    async def fetch_order(self, order_id: str, symbol: str) -> dict:
        ...

    @abstractmethod
    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[dict]:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...

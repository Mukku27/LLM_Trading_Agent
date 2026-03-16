from abc import ABC, abstractmethod
from typing import List, Optional

from utils.dataclass import OrderRequest, OrderResult, AccountBalance, Portfolio, OrderStatus


class ExecutionEngine(ABC):
    """Abstract base for all execution backends (dry-run, paper, live)."""

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderResult:
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        ...

    @abstractmethod
    async def get_balance(self) -> AccountBalance:
        ...

    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[dict]:
        ...

    @abstractmethod
    async def get_order_status(self, order_id: str, symbol: str) -> OrderStatus:
        ...

    @abstractmethod
    async def sync_portfolio(self) -> Portfolio:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...

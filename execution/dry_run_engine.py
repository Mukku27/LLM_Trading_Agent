import configparser
import uuid
from datetime import datetime
from typing import List, Optional

from core.data_persistence import DataPersistence
from execution.base import ExecutionEngine
from utils.dataclass import (
    OrderRequest, OrderResult, AccountBalance, Portfolio, OrderStatus, Position,
)


class DryRunEngine(ExecutionEngine):
    """
    Wraps the existing JSON-based persistence layer.
    Produces identical behavior to the original system — no exchange calls.
    """

    DEFAULT_SIMULATED_EQUITY = 10_000.0

    def __init__(self, config: configparser.ConfigParser, logger) -> None:
        self.config = config
        self.logger = logger
        self.data_persistence = DataPersistence(logger=logger)
        self._open_orders: List[dict] = []
        self._simulated_equity: float = config.getfloat(
            "execution", "simulated_equity", fallback=self.DEFAULT_SIMULATED_EQUITY
        )

    async def place_order(self, order: OrderRequest) -> OrderResult:
        order_id = str(uuid.uuid4())
        now = datetime.now()

        result = OrderResult(
            order_id=order_id,
            status=OrderStatus.FILLED.value,
            filled_amount=order.amount,
            avg_price=order.price or 0.0,
            fee=0.0,
            timestamp=now,
            raw_response={"mode": "dry_run"},
        )

        self.logger.info(
            f"[DryRun] Order {order_id}: {order.side} {order.amount} {order.symbol} @ {order.price}"
        )
        return result

    async def cancel_order(self, order_id: str, symbol: str = "") -> bool:
        self.logger.info(f"[DryRun] Cancel order {order_id} (simulated)")
        self._open_orders = [o for o in self._open_orders if o.get("id") != order_id]
        return True

    async def get_balance(self) -> AccountBalance:
        return AccountBalance(
            total={"USDC": self._simulated_equity},
            free={"USDC": self._simulated_equity},
            used={"USDC": 0.0},
            timestamp=datetime.now(),
        )

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[dict]:
        return list(self._open_orders)

    async def get_order_status(self, order_id: str, symbol: str = "") -> OrderStatus:
        return OrderStatus.FILLED

    async def sync_portfolio(self) -> Portfolio:
        balance = await self.get_balance()
        position = self.data_persistence.load_position()
        positions = [position] if position else []

        return Portfolio(
            balances=balance,
            open_positions=positions,
            unrealized_pnl=0.0,
            total_equity=self._simulated_equity,
        )

    async def close(self) -> None:
        self.logger.info("[DryRun] Engine closed")

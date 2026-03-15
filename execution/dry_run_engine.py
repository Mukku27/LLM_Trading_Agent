import configparser
import uuid
from datetime import datetime
from typing import List

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

    SIMULATED_EQUITY = 10_000.0

    def __init__(self, config: configparser.ConfigParser, logger) -> None:
        self.config = config
        self.logger = logger
        self.data_persistence = DataPersistence(logger=logger)
        self._open_orders: List[dict] = []

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

    async def cancel_order(self, order_id: str) -> bool:
        self.logger.info(f"[DryRun] Cancel order {order_id} (simulated)")
        self._open_orders = [o for o in self._open_orders if o.get("id") != order_id]
        return True

    async def get_balance(self) -> AccountBalance:
        return AccountBalance(
            total={"USDC": self.SIMULATED_EQUITY},
            free={"USDC": self.SIMULATED_EQUITY},
            used={"USDC": 0.0},
            timestamp=datetime.now(),
        )

    async def get_open_orders(self) -> List[dict]:
        return list(self._open_orders)

    async def get_order_status(self, order_id: str) -> OrderStatus:
        return OrderStatus.FILLED

    async def sync_portfolio(self) -> Portfolio:
        balance = await self.get_balance()
        position = self.data_persistence.load_position()
        positions = [position] if position else []

        return Portfolio(
            balances=balance,
            open_positions=positions,
            unrealized_pnl=0.0,
            total_equity=self.SIMULATED_EQUITY,
        )

    async def close(self) -> None:
        self.logger.info("[DryRun] Engine closed")

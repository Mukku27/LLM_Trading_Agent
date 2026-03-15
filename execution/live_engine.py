import configparser
import uuid
from datetime import datetime
from typing import List

from execution.base import ExecutionEngine
from execution.connectors.base import ExchangeConnector
from utils.dataclass import (
    OrderRequest, OrderResult, AccountBalance, Portfolio, OrderStatus,
)


class LiveEngine(ExecutionEngine):
    """Routes orders to a real exchange via ExchangeConnector."""

    def __init__(
        self,
        connector: ExchangeConnector,
        config: configparser.ConfigParser,
        logger,
    ) -> None:
        self.connector = connector
        self.config = config
        self.logger = logger

    async def place_order(self, order: OrderRequest) -> OrderResult:
        client_id = order.client_order_id or str(uuid.uuid4())
        self.logger.info(
            f"[Live] Placing {order.side} {order.order_type} "
            f"{order.amount} {order.symbol} @ {order.price or 'market'}"
        )

        try:
            raw = await self.connector.create_order(
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                amount=order.amount,
                price=order.price,
            )

            status_map = {
                "closed": OrderStatus.FILLED.value,
                "open": OrderStatus.SUBMITTED.value,
                "canceled": OrderStatus.CANCELLED.value,
                "expired": OrderStatus.CANCELLED.value,
                "rejected": OrderStatus.REJECTED.value,
            }

            return OrderResult(
                order_id=raw.get("id", client_id),
                status=status_map.get(raw.get("status", ""), OrderStatus.PENDING.value),
                filled_amount=float(raw.get("filled", 0)),
                avg_price=float(raw.get("average", 0) or raw.get("price", 0) or 0),
                fee=float(raw.get("fee", {}).get("cost", 0) if raw.get("fee") else 0),
                timestamp=datetime.now(),
                raw_response=raw,
            )
        except Exception as e:
            self.logger.error(f"[Live] Order failed: {e}")
            return OrderResult(
                order_id=client_id,
                status=OrderStatus.FAILED.value,
                filled_amount=0.0,
                avg_price=0.0,
                fee=0.0,
                timestamp=datetime.now(),
                raw_response={"error": str(e)},
            )

    async def cancel_order(self, order_id: str) -> bool:
        try:
            symbol = self.config.get("exchange", "symbol")
            await self.connector.cancel_order(order_id, symbol)
            self.logger.info(f"[Live] Cancelled order {order_id}")
            return True
        except Exception as e:
            self.logger.error(f"[Live] Cancel failed for {order_id}: {e}")
            return False

    async def get_balance(self) -> AccountBalance:
        raw = await self.connector.fetch_balance()
        return AccountBalance(
            total=dict(raw.get("total", {})),
            free=dict(raw.get("free", {})),
            used=dict(raw.get("used", {})),
            timestamp=datetime.now(),
        )

    async def get_open_orders(self) -> List[dict]:
        symbol = self.config.get("exchange", "symbol")
        return await self.connector.fetch_open_orders(symbol)

    async def get_order_status(self, order_id: str) -> OrderStatus:
        symbol = self.config.get("exchange", "symbol")
        raw = await self.connector.fetch_order(order_id, symbol)
        status_map = {
            "closed": OrderStatus.FILLED,
            "open": OrderStatus.SUBMITTED,
            "canceled": OrderStatus.CANCELLED,
            "expired": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
        }
        return status_map.get(raw.get("status", ""), OrderStatus.PENDING)

    async def sync_portfolio(self) -> Portfolio:
        balance = await self.get_balance()
        total_equity = sum(balance.total.values())
        return Portfolio(
            balances=balance,
            open_positions=[],
            unrealized_pnl=0.0,
            total_equity=total_equity,
        )

    async def close(self) -> None:
        await self.connector.close()
        self.logger.info("[Live] Engine closed")

import configparser
import uuid
from datetime import datetime
from typing import List, Optional

from ccxt import NetworkError as CCXTNetworkError
from ccxt import ExchangeNotAvailable, OnMaintenance

from core.data_persistence import DataPersistence
from execution.base import ExecutionEngine
from execution.connectors.base import ExchangeConnector
from utils.dataclass import (
    OrderRequest, OrderResult, AccountBalance, Portfolio, OrderStatus,
)

# Errors that indicate the testnet is unavailable — safe to simulate past.
# All other CCXT errors (auth, funds, bad params) must propagate as FAILED.
_SIMULATABLE_ERRORS = (CCXTNetworkError, ExchangeNotAvailable, OnMaintenance)


class PaperEngine(ExecutionEngine):
    """
    Paper trading engine that uses the Binance testnet (sandbox) for real
    order-book data but simulates fills locally when the testnet rejects.
    Use this for 1-2 weeks of parallel comparison with DryRunEngine before
    going live.
    """

    def __init__(
        self,
        connector: ExchangeConnector,
        config: configparser.ConfigParser,
        logger,
    ) -> None:
        self.connector = connector
        self.config = config
        self.logger = logger
        self.data_persistence = DataPersistence(logger=logger)
        self._simulated_equity = config.getfloat("execution", "simulated_equity", fallback=10_000.0)
        self._simulated_balance: dict = {"USDC": self._simulated_equity}
        self._open_orders: List[dict] = []

    async def place_order(self, order: OrderRequest) -> OrderResult:
        client_id = order.client_order_id or str(uuid.uuid4())

        try:
            raw = await self.connector.create_order(
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                amount=order.amount,
                price=order.price,
            )
            self.logger.info(f"[Paper] Testnet order placed: {raw.get('id', client_id)}")

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
                filled_amount=float(raw.get("filled", order.amount)),
                avg_price=float(raw.get("average", 0) or raw.get("price", 0) or order.price or 0),
                fee=float(raw.get("fee", {}).get("cost", 0) if raw.get("fee") else 0),
                timestamp=datetime.now(),
                raw_response=raw,
            )
        except _SIMULATABLE_ERRORS as e:
            self.logger.warning(
                f"[Paper] Testnet unavailable ({type(e).__name__}: {e}), "
                f"using simulated fill."
            )
            return OrderResult(
                order_id=client_id,
                status=OrderStatus.FILLED.value,
                filled_amount=order.amount,
                avg_price=order.price or 0.0,
                fee=0.0,
                timestamp=datetime.now(),
                raw_response={
                    "mode": "paper_simulated",
                    "simulated": True,
                    "error": str(e),
                },
            )
        except Exception as e:
            self.logger.error(
                f"[Paper] Order FAILED ({type(e).__name__}: {e}). "
                f"This is NOT a testnet-availability issue."
            )
            return OrderResult(
                order_id=client_id,
                status=OrderStatus.FAILED.value,
                filled_amount=0.0,
                avg_price=0.0,
                fee=0.0,
                timestamp=datetime.now(),
                raw_response={"error": str(e), "error_type": type(e).__name__},
            )

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            await self.connector.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            self.logger.warning(f"[Paper] Cancel failed: {e}")
            return False

    async def get_balance(self) -> AccountBalance:
        try:
            raw = await self.connector.fetch_balance()
            return AccountBalance(
                total=dict(raw.get("total", {})),
                free=dict(raw.get("free", {})),
                used=dict(raw.get("used", {})),
                timestamp=datetime.now(),
            )
        except Exception:
            return AccountBalance(
                total=dict(self._simulated_balance),
                free=dict(self._simulated_balance),
                used={"USDC": 0.0},
                timestamp=datetime.now(),
            )

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[dict]:
        try:
            return await self.connector.fetch_open_orders(symbol)
        except Exception:
            return []

    async def get_order_status(self, order_id: str, symbol: str) -> OrderStatus:
        try:
            raw = await self.connector.fetch_order(order_id, symbol)
            status_map = {
                "closed": OrderStatus.FILLED,
                "open": OrderStatus.SUBMITTED,
                "canceled": OrderStatus.CANCELLED,
                "expired": OrderStatus.CANCELLED,
                "rejected": OrderStatus.REJECTED,
            }
            return status_map.get(raw.get("status", ""), OrderStatus.PENDING)
        except Exception:
            return OrderStatus.PENDING

    async def sync_portfolio(self) -> Portfolio:
        balance = await self.get_balance()
        total_equity = sum(balance.total.values())
        position = self.data_persistence.load_position()
        positions = [position] if position else []
        return Portfolio(
            balances=balance,
            open_positions=positions,
            unrealized_pnl=0.0,
            total_equity=max(total_equity, self._simulated_equity),
        )

    async def close(self) -> None:
        await self.connector.close()
        self.logger.info("[Paper] Engine closed")

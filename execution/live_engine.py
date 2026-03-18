import configparser
import uuid
from datetime import datetime
from typing import List, Optional

from ccxt import AuthenticationError as CCXTAuthError
from ccxt import NetworkError as CCXTNetworkError

from core.data_persistence import DataPersistence
from execution.base import ExecutionEngine
from execution.connectors.base import ExchangeConnector
from utils.dataclass import (
    OrderRequest, OrderResult, AccountBalance, Portfolio, OrderStatus,
)

# Transient errors safe to retry/fallback on. All are NetworkError subclasses.
_TRANSIENT_ERRORS = (CCXTNetworkError,)

# Errors that indicate a fundamental config/auth problem — must propagate.
_FATAL_ERRORS = (CCXTAuthError,)


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
        self.data_persistence = DataPersistence(logger=logger)
        self._last_balance: Optional[AccountBalance] = None

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

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            await self.connector.cancel_order(order_id, symbol)
            self.logger.info(f"[Live] Cancelled order {order_id}")
            return True
        except Exception as e:
            self.logger.error(f"[Live] Cancel failed for {order_id}: {e}")
            return False

    async def get_balance(self) -> AccountBalance:
        try:
            raw = await self.connector.fetch_balance()
            balance = AccountBalance(
                total=dict(raw.get("total", {})),
                free=dict(raw.get("free", {})),
                used=dict(raw.get("used", {})),
                timestamp=datetime.now(),
            )
            self._last_balance = balance
            return balance
        except _FATAL_ERRORS:
            raise
        except _TRANSIENT_ERRORS as e:
            self.logger.warning(
                f"[Live] fetch_balance failed ({type(e).__name__}: {e}), "
                f"using last-known-good balance"
            )
            if self._last_balance is not None:
                return self._last_balance
            raise

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[dict]:
        try:
            return await self.connector.fetch_open_orders(symbol)
        except _FATAL_ERRORS:
            raise
        except _TRANSIENT_ERRORS as e:
            self.logger.warning(
                f"[Live] fetch_open_orders failed ({type(e).__name__}: {e}), "
                f"returning empty list"
            )
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
        except _FATAL_ERRORS:
            raise
        except _TRANSIENT_ERRORS as e:
            self.logger.warning(
                f"[Live] fetch_order failed for {order_id} ({type(e).__name__}: {e}), "
                f"returning PENDING"
            )
            return OrderStatus.PENDING

    async def sync_portfolio(self) -> Portfolio:
        balance = await self.get_balance()
        quote_currency = self._get_quote_currency()
        total_equity = await self._compute_equity(balance, quote_currency)
        position = self.data_persistence.load_position()
        positions = [position] if position else []
        return Portfolio(
            balances=balance,
            open_positions=positions,
            unrealized_pnl=0.0,
            total_equity=total_equity,
        )

    def _get_quote_currency(self) -> str:
        symbol = self.config.get("exchange", "symbol", fallback="BTC/USDC")
        return symbol.split("/")[-1]

    async def _compute_equity(
        self, balance: AccountBalance, quote_currency: str,
    ) -> float:
        """Convert all balances to *quote_currency* and return the total."""
        total = 0.0
        for currency, amount in balance.total.items():
            if amount == 0:
                continue
            if currency == quote_currency:
                total += amount
                continue
            pair = f"{currency}/{quote_currency}"
            try:
                ticker = await self.connector.fetch_ticker(pair)
                price = float(ticker.get("last") or ticker.get("close") or 0)
                total += amount * price
            except Exception as e:
                self.logger.warning(
                    f"[Live] Cannot price {currency} via {pair}: {e}, skipping"
                )
        return total

    async def close(self) -> None:
        await self.connector.close()
        self.logger.info("[Live] Engine closed")

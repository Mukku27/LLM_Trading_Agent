import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from utils.dataclass import OrderStatus


_VALID_TRANSITIONS: Dict[str, set] = {
    OrderStatus.PENDING.value: {OrderStatus.SUBMITTED.value, OrderStatus.FAILED.value},
    OrderStatus.SUBMITTED.value: {
        OrderStatus.FILLED.value,
        OrderStatus.PARTIAL.value,
        OrderStatus.CANCELLED.value,
        OrderStatus.REJECTED.value,
        OrderStatus.FAILED.value,
    },
    OrderStatus.PARTIAL.value: {OrderStatus.FILLED.value, OrderStatus.CANCELLED.value},
    OrderStatus.FAILED.value: {OrderStatus.SUBMITTED.value},
}


class OrderTracker:
    """
    Persists order state in SQLite and polls the exchange for status updates.
    Uses per-operation connections for thread safety in async contexts.
    """

    def __init__(self, logger, data_dir: str = "trading_data") -> None:
        self.logger = logger
        self._db_path = Path(data_dir) / "orders.db"
        self._db_path.parent.mkdir(exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id      TEXT PRIMARY KEY,
                    client_id     TEXT,
                    symbol        TEXT NOT NULL,
                    side          TEXT NOT NULL,
                    order_type    TEXT NOT NULL,
                    amount        REAL NOT NULL,
                    price         REAL,
                    status        TEXT NOT NULL DEFAULT 'pending',
                    filled_amount REAL DEFAULT 0,
                    avg_price     REAL DEFAULT 0,
                    fee           REAL DEFAULT 0,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL,
                    raw_response  TEXT DEFAULT '{}'
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def record_order(
        self,
        order_id: str,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: Optional[float],
        status: str,
        filled_amount: float = 0,
        avg_price: float = 0,
        fee: float = 0,
        client_id: Optional[str] = None,
        raw_response: str = "{}",
    ) -> None:
        now = datetime.now().isoformat()
        conn = self._connect()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO orders
                   (order_id, client_id, symbol, side, order_type, amount, price,
                    status, filled_amount, avg_price, fee, created_at, updated_at, raw_response)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (order_id, client_id, symbol, side, order_type, amount, price,
                 status, filled_amount, avg_price, fee, now, now, raw_response),
            )
            conn.commit()
        finally:
            conn.close()

    def update_status(self, order_id: str, new_status: str, **kwargs) -> bool:
        """Transition order status respecting the state machine."""
        row = self.get_order(order_id)
        if row is None:
            self.logger.error(f"[OrderTracker] Order {order_id} not found")
            return False

        current = row["status"]
        allowed = _VALID_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            self.logger.warning(
                f"[OrderTracker] Invalid transition {current} -> {new_status} for {order_id}"
            )
            return False

        now = datetime.now().isoformat()
        sets = ["status = ?", "updated_at = ?"]
        vals: list = [new_status, now]

        for col in ("filled_amount", "avg_price", "fee", "raw_response"):
            if col in kwargs:
                sets.append(f"{col} = ?")
                vals.append(kwargs[col])

        vals.append(order_id)
        conn = self._connect()
        try:
            conn.execute(f"UPDATE orders SET {', '.join(sets)} WHERE order_id = ?", vals)
            conn.commit()
        finally:
            conn.close()

        self.logger.info(f"[OrderTracker] {order_id}: {current} -> {new_status}")
        return True

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_open_orders(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM orders WHERE status IN (?, ?, ?)",
                (OrderStatus.PENDING.value, OrderStatus.SUBMITTED.value, OrderStatus.PARTIAL.value),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_all_orders(self, limit: int = 100) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM orders ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    async def poll_order(self, order_id: str, engine, symbol: str, max_polls: int = 10, base_delay: float = 2.0) -> str:
        """
        Poll exchange for order status with exponential backoff.
        Returns the final status string.
        """
        delay = base_delay
        for attempt in range(max_polls):
            try:
                status = await engine.get_order_status(order_id, symbol=symbol)
                current_status = status.value if hasattr(status, "value") else str(status)

                row = self.get_order(order_id)
                if row and row["status"] != current_status:
                    self.update_status(order_id, current_status)

                terminal = {OrderStatus.FILLED.value, OrderStatus.CANCELLED.value, OrderStatus.REJECTED.value}
                if current_status in terminal:
                    return current_status

            except Exception as e:
                self.logger.warning(f"[OrderTracker] Poll attempt {attempt + 1} failed: {e}")

            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

        self.logger.warning(f"[OrderTracker] Polling exhausted for {order_id}")
        row = self.get_order(order_id)
        return row["status"] if row else OrderStatus.PENDING.value

    def close(self) -> None:
        pass

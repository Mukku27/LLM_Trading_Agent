import asyncio
import configparser
import functools
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Callable, Optional, List

from execution.base import ExecutionEngine
from utils.dataclass import OrderRequest, OrderResult, OrderStatus


@dataclass
class RiskCheckResult:
    approved: bool
    reason: Optional[str] = None


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


@dataclass
class _DailyStats:
    """Accumulated stats for the current trading day (UTC)."""
    date: date = field(default_factory=_utc_today)
    realized_pnl: float = 0.0
    order_count: int = 0


class RiskManager:
    """
    Mandatory gateway between the agent decision and the ExecutionEngine.
    Every order must pass through validate() before execution.
    """

    def __init__(
        self,
        engine: ExecutionEngine,
        config: configparser.ConfigParser,
        logger,
        data_dir: str = "trading_data",
        *,
        confirm_callback: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.engine = engine
        self.config = config
        self.logger = logger

        self.kill_switch: bool = config.getboolean("execution", "kill_switch", fallback=False)
        self.max_position_pct: float = config.getfloat("execution", "max_position_pct", fallback=5.0)
        self.max_daily_loss_pct: float = config.getfloat("execution", "max_daily_loss_pct", fallback=10.0)
        self.max_open_positions: int = config.getint("execution", "max_open_positions", fallback=3)
        self.confirm_trades: bool = config.getboolean("execution", "confirm_trades", fallback=True)
        self.cooldown_seconds: int = config.getint("execution", "cooldown_seconds", fallback=60)
        self.order_timeout_seconds: int = config.getint("execution", "order_timeout_seconds", fallback=300)
        self.max_orders_per_minute: int = config.getint("execution", "max_orders_per_minute", fallback=5)

        self._confirm_callback = confirm_callback

        whitelist_raw = config.get("execution", "symbol_whitelist", fallback="BTC/USDC")
        self.symbol_whitelist: set = {s.strip() for s in whitelist_raw.split(",")}

        self._db_path = Path(data_dir) / "orders.db"
        self._db_path.parent.mkdir(exist_ok=True)
        self._ensure_schema()

        self._daily_stats = self._load_daily_stats()
        self._last_trade_time: dict[str, float] = {}
        self._recent_order_timestamps: List[float] = []

    # ------------------------------------------------------------------
    # SQLite persistence for daily stats
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date          TEXT PRIMARY KEY,
                    realized_pnl  REAL NOT NULL DEFAULT 0,
                    order_count   INTEGER NOT NULL DEFAULT 0,
                    updated_at    TEXT NOT NULL
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def _load_daily_stats(self) -> _DailyStats:
        today = _utc_today()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT realized_pnl, order_count FROM daily_stats WHERE date = ?",
                (today.isoformat(),),
            ).fetchone()
            if row:
                self.logger.info(
                    f"[Risk] Restored daily stats: pnl={row['realized_pnl']:.2f}, "
                    f"orders={row['order_count']}"
                )
                return _DailyStats(
                    date=today,
                    realized_pnl=row["realized_pnl"],
                    order_count=row["order_count"],
                )
            return _DailyStats(date=today)
        finally:
            conn.close()

    def _persist_daily_stats(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO daily_stats (date, realized_pnl, order_count, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET
                       realized_pnl = excluded.realized_pnl,
                       order_count  = excluded.order_count,
                       updated_at   = excluded.updated_at""",
                (self._daily_stats.date.isoformat(),
                 self._daily_stats.realized_pnl,
                 self._daily_stats.order_count,
                 now),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------

    def _rotate_daily_stats(self) -> None:
        today = _utc_today()
        if self._daily_stats.date != today:
            self._daily_stats = self._load_daily_stats()

    def _prune_rate_window(self) -> None:
        cutoff = time.time() - 60
        self._recent_order_timestamps = [
            ts for ts in self._recent_order_timestamps if ts > cutoff
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, order: OrderRequest, portfolio_equity: float, open_position_count: int, *, is_closing: bool = False, estimated_market_price: Optional[float] = None) -> RiskCheckResult:
        """Run the full pre-trade validation checklist."""
        self._rotate_daily_stats()

        if self.kill_switch:
            return RiskCheckResult(False, "Kill switch is ON — all trading halted")

        if order.symbol not in self.symbol_whitelist:
            return RiskCheckResult(False, f"Symbol {order.symbol} not in whitelist: {self.symbol_whitelist}")

        if portfolio_equity > 0:
            price = order.price or estimated_market_price or 0
            order_value = order.amount * price
            position_pct = (order_value / portfolio_equity) * 100
            if position_pct > self.max_position_pct:
                return RiskCheckResult(
                    False,
                    f"Position size {position_pct:.1f}% exceeds max {self.max_position_pct}%",
                )

        if not is_closing and open_position_count >= self.max_open_positions:
            return RiskCheckResult(
                False,
                f"Max open positions ({self.max_open_positions}) reached",
            )

        if portfolio_equity > 0:
            daily_loss_pct = abs(self._daily_stats.realized_pnl) / portfolio_equity * 100
            if self._daily_stats.realized_pnl < 0 and daily_loss_pct >= self.max_daily_loss_pct:
                return RiskCheckResult(
                    False,
                    f"Daily loss {daily_loss_pct:.1f}% exceeds max {self.max_daily_loss_pct}%",
                )

        if not is_closing:
            last_trade = self._last_trade_time.get(order.symbol, 0)
            elapsed = time.time() - last_trade
            if elapsed < self.cooldown_seconds:
                remaining = self.cooldown_seconds - int(elapsed)
                return RiskCheckResult(False, f"Cooldown active for {order.symbol}: {remaining}s remaining")

            self._prune_rate_window()
            if len(self._recent_order_timestamps) >= self.max_orders_per_minute:
                return RiskCheckResult(False, f"Rate limit: {self.max_orders_per_minute} orders/min exceeded")

        return RiskCheckResult(True)

    async def execute(self, order: OrderRequest, portfolio_equity: float, open_position_count: int, *, is_closing: bool = False, estimated_market_price: Optional[float] = None) -> Optional[OrderResult]:
        """Validate then route to the execution engine."""
        check = self.validate(order, portfolio_equity, open_position_count, is_closing=is_closing, estimated_market_price=estimated_market_price)

        if not check.approved:
            self.logger.warning(f"[Risk] Order REJECTED: {check.reason}")
            return None

        if self.confirm_trades and self.config.get("execution", "mode", fallback="dry_run") == "live":
            prompt = (
                f"{order.side} {order.amount} {order.symbol} @ {order.price}"
            )
            self.logger.info(f"[Risk] Confirmation required: {prompt}")

            if self._confirm_callback is not None:
                confirmed = self._confirm_callback(prompt)
            elif sys.stdin.isatty():
                loop = asyncio.get_running_loop()
                answer = await loop.run_in_executor(
                    None, functools.partial(input, "Confirm trade? (yes/no): ")
                )
                confirmed = answer.strip().lower() == "yes"
            else:
                self.logger.warning(
                    "[Risk] Non-interactive environment — auto-rejecting trade. "
                    "Set confirm_trades = false or provide a confirm_callback."
                )
                confirmed = False

            if not confirmed:
                self.logger.info("[Risk] Trade rejected by user")
                return None

        result = await self.engine.place_order(order)

        self._recent_order_timestamps.append(time.time())
        self._last_trade_time[order.symbol] = time.time()

        if result.status == OrderStatus.FILLED.value:
            self._daily_stats.order_count += 1
            self._persist_daily_stats()

        return result

    def record_pnl(self, pnl: float) -> None:
        """Update daily P&L after a position close."""
        self._rotate_daily_stats()
        self._daily_stats.realized_pnl += pnl
        self._persist_daily_stats()

    def activate_kill_switch(self) -> None:
        self.kill_switch = True
        self.logger.warning("[Risk] KILL SWITCH ACTIVATED — all trading halted")

    def deactivate_kill_switch(self) -> None:
        self.kill_switch = False
        self.logger.info("[Risk] Kill switch deactivated")

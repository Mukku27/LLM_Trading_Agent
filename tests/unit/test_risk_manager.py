import time
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from execution.dry_run_engine import DryRunEngine
from execution.risk_manager import RiskManager, _utc_today
from utils.dataclass import OrderRequest, OrderResult, OrderStatus


@pytest.fixture
def engine(base_config, mock_logger):
    return DryRunEngine(base_config, mock_logger)


@pytest.fixture
def risk(engine, base_config, mock_logger, tmp_path):
    return RiskManager(engine, base_config, mock_logger, data_dir=str(tmp_path))


class TestKillSwitch:
    def test_blocks_when_active(self, risk):
        risk.activate_kill_switch()
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=0.01, price=50000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=0)
        assert not result.approved
        assert "Kill switch" in result.reason

    def test_allows_when_inactive(self, risk):
        risk.deactivate_kill_switch()
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=0.01, price=50000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=0)
        assert result.approved


class TestSymbolWhitelist:
    def test_rejects_unlisted_symbol(self, risk):
        order = OrderRequest(symbol="DOGE/USDC", side="buy", order_type="market", amount=100, price=0.1)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=0)
        assert not result.approved
        assert "whitelist" in result.reason

    def test_allows_whitelisted_symbol(self, risk):
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=0.01, price=50000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=0)
        assert result.approved


class TestMaxPositionSize:
    def test_rejects_oversized_position(self, risk):
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=1.0, price=10000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=0)
        assert not result.approved
        assert "Position size" in result.reason

    def test_allows_small_position(self, risk):
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=0.001, price=50000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=0)
        assert result.approved

    def test_market_order_no_price_uses_estimated_price(self, risk):
        """Market orders have price=None. Without estimated_market_price the check is bypassed."""
        # 1 BTC at estimated $100k = $100k = 1000% of $10k equity → must reject
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=1.0)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=0, estimated_market_price=100000)
        assert not result.approved
        assert "Position size" in result.reason

    def test_market_order_no_price_no_estimate_skips_check(self, risk):
        """If neither price nor estimate is provided, value is 0 and check passes (legacy behavior)."""
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=1.0)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=0)
        assert result.approved

    def test_market_order_small_with_estimated_price(self, risk):
        """Small market order with estimated price should pass."""
        # 0.001 BTC at $50k = $50 = 0.5% of $10k → should pass (max is 5%)
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=0.001)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=0, estimated_market_price=50000)
        assert result.approved

    def test_order_price_takes_precedence_over_estimate(self, risk):
        """When order has a price, estimated_market_price is ignored."""
        # order.price=10000 → 1.0 * 10000 = 10000 = 100% → reject
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=1.0, price=10000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=0, estimated_market_price=1)
        assert not result.approved
        assert "Position size" in result.reason


class TestMaxOpenPositions:
    def test_rejects_when_at_limit(self, risk):
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=0.01, price=50000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=3)
        assert not result.approved
        assert "Max open positions" in result.reason

    def test_allows_close_when_at_limit(self, risk):
        order = OrderRequest(symbol="BTC/USDC", side="sell", order_type="market", amount=0.01, price=50000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=3, is_closing=True)
        assert result.approved


class TestDailyLoss:
    def test_rejects_after_daily_loss_exceeded(self, risk):
        risk.record_pnl(-1100)
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=0.01, price=50000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=0)
        assert not result.approved
        assert "Daily loss" in result.reason


class TestDailyLossPersistence:
    def test_circuit_breaker_survives_restart(self, engine, base_config, mock_logger, tmp_path):
        """Simulate: accumulate losses → restart (new instance) → verify breaker still trips."""
        # Instance 1: record a big loss
        risk1 = RiskManager(engine, base_config, mock_logger, data_dir=str(tmp_path))
        risk1.record_pnl(-1100)  # 11% of $10k equity → exceeds 10% max

        # Instance 2: "restart" — new object, same DB
        risk2 = RiskManager(engine, base_config, mock_logger, data_dir=str(tmp_path))
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=0.01, price=50000)
        result = risk2.validate(order, portfolio_equity=10000, open_position_count=0)
        assert not result.approved
        assert "Daily loss" in result.reason

    def test_pnl_accumulates_across_restarts(self, engine, base_config, mock_logger, tmp_path):
        """Two separate instances each recording partial losses should sum correctly."""
        risk1 = RiskManager(engine, base_config, mock_logger, data_dir=str(tmp_path))
        risk1.record_pnl(-500)  # 5% — not yet tripping 10% limit

        risk2 = RiskManager(engine, base_config, mock_logger, data_dir=str(tmp_path))
        risk2.record_pnl(-600)  # total now -1100 = 11% → should trip

        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=0.01, price=50000)
        result = risk2.validate(order, portfolio_equity=10000, open_position_count=0)
        assert not result.approved
        assert "Daily loss" in result.reason

    def test_fresh_day_starts_clean(self, engine, base_config, mock_logger, tmp_path):
        """A new day should not carry over the previous day's losses."""
        risk = RiskManager(engine, base_config, mock_logger, data_dir=str(tmp_path))
        # No losses recorded today
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=0.01, price=50000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=0)
        assert result.approved


class TestCooldown:
    def test_rejects_during_cooldown(self, base_config, engine, mock_logger, tmp_path):
        base_config.set("execution", "cooldown_seconds", "5")
        risk = RiskManager(engine, base_config, mock_logger, data_dir=str(tmp_path))
        risk._last_trade_time["BTC/USDC"] = time.time()
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=0.01, price=50000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=0)
        assert not result.approved
        assert "Cooldown" in result.reason

    def test_allows_close_during_cooldown(self, base_config, engine, mock_logger, tmp_path):
        base_config.set("execution", "cooldown_seconds", "5")
        risk = RiskManager(engine, base_config, mock_logger, data_dir=str(tmp_path))
        risk._last_trade_time["BTC/USDC"] = time.time()
        order = OrderRequest(symbol="BTC/USDC", side="sell", order_type="market", amount=0.01, price=50000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=1, is_closing=True)
        assert result.approved


class TestRateLimit:
    def test_rejects_when_rate_exceeded(self, base_config, engine, mock_logger, tmp_path):
        base_config.set("execution", "max_orders_per_minute", "2")
        risk = RiskManager(engine, base_config, mock_logger, data_dir=str(tmp_path))
        now = time.time()
        risk._recent_order_timestamps = [now - 5, now - 3]
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=0.01, price=50000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=0)
        assert not result.approved
        assert "Rate limit" in result.reason

    def test_allows_close_when_rate_exceeded(self, base_config, engine, mock_logger, tmp_path):
        base_config.set("execution", "max_orders_per_minute", "2")
        risk = RiskManager(engine, base_config, mock_logger, data_dir=str(tmp_path))
        now = time.time()
        risk._recent_order_timestamps = [now - 5, now - 3]
        order = OrderRequest(symbol="BTC/USDC", side="sell", order_type="market", amount=0.01, price=50000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=1, is_closing=True)
        assert result.approved


class TestConfirmTradesNonInteractive:
    """LOW-13: confirm_trades must not block in non-interactive environments."""

    def _live_config(self, base_config):
        base_config.set("execution", "mode", "live")
        base_config.set("execution", "confirm_trades", "true")
        return base_config

    @pytest.mark.asyncio
    async def test_callback_approves(self, engine, base_config, mock_logger, tmp_path):
        cfg = self._live_config(base_config)
        risk = RiskManager(engine, cfg, mock_logger, data_dir=str(tmp_path),
                           confirm_callback=lambda prompt: True)
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market",
                             amount=0.001, price=50000)
        result = await risk.execute(order, portfolio_equity=10000, open_position_count=0)
        assert result is not None

    @pytest.mark.asyncio
    async def test_callback_rejects(self, engine, base_config, mock_logger, tmp_path):
        cfg = self._live_config(base_config)
        risk = RiskManager(engine, cfg, mock_logger, data_dir=str(tmp_path),
                           confirm_callback=lambda prompt: False)
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market",
                             amount=0.001, price=50000)
        result = await risk.execute(order, portfolio_equity=10000, open_position_count=0)
        assert result is None

    @pytest.mark.asyncio
    async def test_non_interactive_auto_rejects(self, engine, base_config, mock_logger, tmp_path):
        """When stdin is not a TTY and no callback provided, trades are auto-rejected."""
        cfg = self._live_config(base_config)
        risk = RiskManager(engine, cfg, mock_logger, data_dir=str(tmp_path))
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market",
                             amount=0.001, price=50000)
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            result = await risk.execute(order, portfolio_equity=10000, open_position_count=0)
        assert result is None
        # Verify warning was logged
        mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_confirm_trades_false_skips_confirmation(self, engine, base_config, mock_logger, tmp_path):
        """When confirm_trades is false, no confirmation is needed regardless of mode."""
        base_config.set("execution", "mode", "live")
        base_config.set("execution", "confirm_trades", "false")
        risk = RiskManager(engine, base_config, mock_logger, data_dir=str(tmp_path))
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market",
                             amount=0.001, price=50000)
        result = await risk.execute(order, portfolio_equity=10000, open_position_count=0)
        assert result is not None


class TestUTCDailyStats:
    """LOW-15: daily stats must use UTC, not local time."""

    def test_utc_today_returns_utc_date(self):
        utc_now = datetime.now(timezone.utc).date()
        assert _utc_today() == utc_now

    def test_daily_stats_date_is_utc(self, engine, base_config, mock_logger, tmp_path):
        risk = RiskManager(engine, base_config, mock_logger, data_dir=str(tmp_path))
        assert risk._daily_stats.date == _utc_today()

    def test_persist_uses_utc_timestamp(self, engine, base_config, mock_logger, tmp_path):
        """The updated_at timestamp stored in SQLite should be UTC."""
        import sqlite3
        risk = RiskManager(engine, base_config, mock_logger, data_dir=str(tmp_path))
        risk.record_pnl(-50)
        conn = sqlite3.connect(str(tmp_path / "orders.db"))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT updated_at FROM daily_stats").fetchone()
        conn.close()
        # UTC isoformat includes +00:00
        assert "+00:00" in row["updated_at"]

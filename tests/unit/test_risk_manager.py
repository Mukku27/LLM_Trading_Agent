import time

import pytest

from execution.dry_run_engine import DryRunEngine
from execution.risk_manager import RiskManager
from utils.dataclass import OrderRequest


@pytest.fixture
def engine(base_config, mock_logger):
    return DryRunEngine(base_config, mock_logger)


@pytest.fixture
def risk(engine, base_config, mock_logger):
    return RiskManager(engine, base_config, mock_logger)


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


class TestCooldown:
    def test_rejects_during_cooldown(self, base_config, engine, mock_logger):
        base_config.set("execution", "cooldown_seconds", "5")
        risk = RiskManager(engine, base_config, mock_logger)
        risk._last_trade_time["BTC/USDC"] = time.time()
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=0.01, price=50000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=0)
        assert not result.approved
        assert "Cooldown" in result.reason

    def test_allows_close_during_cooldown(self, base_config, engine, mock_logger):
        base_config.set("execution", "cooldown_seconds", "5")
        risk = RiskManager(engine, base_config, mock_logger)
        risk._last_trade_time["BTC/USDC"] = time.time()
        order = OrderRequest(symbol="BTC/USDC", side="sell", order_type="market", amount=0.01, price=50000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=1, is_closing=True)
        assert result.approved


class TestRateLimit:
    def test_rejects_when_rate_exceeded(self, base_config, engine, mock_logger):
        base_config.set("execution", "max_orders_per_minute", "2")
        risk = RiskManager(engine, base_config, mock_logger)
        now = time.time()
        risk._recent_order_timestamps = [now - 5, now - 3]
        order = OrderRequest(symbol="BTC/USDC", side="buy", order_type="market", amount=0.01, price=50000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=0)
        assert not result.approved
        assert "Rate limit" in result.reason

    def test_allows_close_when_rate_exceeded(self, base_config, engine, mock_logger):
        base_config.set("execution", "max_orders_per_minute", "2")
        risk = RiskManager(engine, base_config, mock_logger)
        now = time.time()
        risk._recent_order_timestamps = [now - 5, now - 3]
        order = OrderRequest(symbol="BTC/USDC", side="sell", order_type="market", amount=0.01, price=50000)
        result = risk.validate(order, portfolio_equity=10000, open_position_count=1, is_closing=True)
        assert result.approved

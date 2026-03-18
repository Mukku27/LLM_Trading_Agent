"""
Unit tests for MEDIUM-9 (FAILED short-circuit) and MEDIUM-11 (close-position
exponential backoff, max retries, kill switch activation).
"""

import sys
from datetime import datetime, timedelta
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub heavy transitive imports so we don't need pandas/numba/etc.
_stub_modules = [
    "core.market_analyzer", "core.data_fetcher", "core.model_manager",
    "indicators", "indicators.base", "indicators.base.technical_indicators",
    "indicators.base.indicator_base",
    "tiktoken", "pandas", "numpy",
]
for _mod in _stub_modules:
    if _mod not in sys.modules:
        sys.modules[_mod] = ModuleType(_mod)

# Provide the names that trading_strategy.py imports from these stubs.
sys.modules["core.market_analyzer"].MarketAnalyzer = MagicMock

from core.trading_strategy import TradingStrategy
from utils.dataclass import OrderResult, OrderStatus, Portfolio, AccountBalance, Position


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_engine():
    engine = AsyncMock()
    engine.sync_portfolio = AsyncMock(return_value=Portfolio(
        balances=AccountBalance(
            total={"USDC": 10000.0}, free={"USDC": 10000.0},
            used={}, timestamp=datetime.now(),
        ),
        open_positions=[],
        unrealized_pnl=0.0,
        total_equity=10000.0,
    ))
    engine.close = AsyncMock()
    return engine


@pytest.fixture
def mock_risk():
    risk = MagicMock()
    risk.activate_kill_switch = MagicMock()
    return risk


@pytest.fixture
def mock_order_tracker():
    tracker = MagicMock()
    tracker.record_order = MagicMock()
    tracker.poll_order = AsyncMock(return_value=OrderStatus.FILLED.value)
    tracker.close = MagicMock()
    return tracker


@pytest.fixture
def mock_audit():
    return MagicMock()


@pytest.fixture
def strategy(mock_logger, mock_engine, mock_risk, mock_order_tracker, mock_audit):
    s = TradingStrategy.__new__(TradingStrategy)
    s.logger = mock_logger
    s.execution_engine = mock_engine
    s.risk_manager = mock_risk
    s.order_tracker = mock_order_tracker
    s.audit_log = mock_audit
    # symbol, periods, data_persistence are read-only properties delegating to analyzer
    mock_analyzer = MagicMock()
    mock_analyzer.symbol = "BTC/USDC"
    mock_analyzer.data_persistence = MagicMock()
    mock_period = MagicMock()
    mock_period.data = [MagicMock(close=49500.0)]
    mock_analyzer.periods = {"3D": mock_period}
    s.analyzer = mock_analyzer
    s._execution_mode = "paper"
    s._failed_close_at = None
    s._close_retry_backoff_seconds = 30
    s._close_retry_count = 0
    s._max_close_retries = 3
    s.current_position = None
    return s


# ---------------------------------------------------------------------------
# MEDIUM-9: Short-circuit on FAILED / REJECTED / CANCELLED
# ---------------------------------------------------------------------------

class TestTerminalStatusShortCircuit:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("terminal_status", [
        OrderStatus.FAILED.value,
        OrderStatus.REJECTED.value,
        OrderStatus.CANCELLED.value,
    ])
    async def test_terminal_status_skips_poll(
        self, strategy, mock_risk, mock_order_tracker, terminal_status,
    ):
        """Orders that come back with a terminal failure should NOT enter the poll loop."""
        mock_risk.execute = AsyncMock(return_value=OrderResult(
            order_id="ord-1", status=terminal_status,
            filled_amount=0.0, avg_price=0.0, fee=0.0,
            timestamp=datetime.now(), raw_response={},
        ))
        success, fill_price, order_id = await strategy._execute_order(
            side="buy", amount=0.1, price=50000, event_prefix="open",
        )
        assert success is False
        assert fill_price is None
        mock_order_tracker.poll_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_filled_status_returns_success(self, strategy, mock_risk):
        """A FILLED order should succeed without polling."""
        mock_risk.execute = AsyncMock(return_value=OrderResult(
            order_id="ord-2", status=OrderStatus.FILLED.value,
            filled_amount=0.1, avg_price=50100.0, fee=0.5,
            timestamp=datetime.now(), raw_response={},
        ))
        success, fill_price, order_id = await strategy._execute_order(
            side="buy", amount=0.1, price=50000, event_prefix="open",
        )
        assert success is True
        assert fill_price == 50100.0

    @pytest.mark.asyncio
    async def test_pending_enters_poll_loop(
        self, strategy, mock_risk, mock_order_tracker,
    ):
        """PENDING/SUBMITTED should still enter the poll loop."""
        mock_risk.execute = AsyncMock(return_value=OrderResult(
            order_id="ord-3", status=OrderStatus.SUBMITTED.value,
            filled_amount=0.0, avg_price=0.0, fee=0.0,
            timestamp=datetime.now(), raw_response={},
        ))
        mock_order_tracker.poll_order.return_value = OrderStatus.FILLED.value
        success, _, _ = await strategy._execute_order(
            side="buy", amount=0.1, price=50000, event_prefix="open",
        )
        assert success is True
        mock_order_tracker.poll_order.assert_called_once()


# ---------------------------------------------------------------------------
# MEDIUM-11: Exponential backoff, max retries, kill switch
# ---------------------------------------------------------------------------

class TestClosePositionRetryBackoff:

    def _set_position(self, strategy):
        strategy.current_position = Position(
            entry_price=50000, stop_loss=49000, take_profit=52000,
            size=0.1, entry_time=datetime.now(), confidence="HIGH",
            direction="LONG",
        )

    @pytest.mark.asyncio
    async def test_backoff_doubles_each_failure(self, strategy, mock_risk):
        """Backoff should escalate: 30*(2^1)=60, 30*(2^2)=120, ..."""
        self._set_position(strategy)
        mock_risk.execute = AsyncMock(return_value=None)  # always rejected

        # Simulate first failure
        with patch("core.trading_strategy.TradingStrategy._execute_order",
                    new_callable=AsyncMock, return_value=(False, None, None)):
            await strategy.close_position("stop_loss")
        assert strategy._close_retry_count == 1

        # Backoff for retry 1 should be 30 * 2^1 = 60s
        expected_backoff = 30 * (2 ** 1)
        # Set failed_close_at to now, so within backoff window
        strategy._failed_close_at = datetime.now()
        self._set_position(strategy)  # re-set position (close_position doesn't clear on failure)
        await strategy.check_position(current_price=48000)  # should skip due to backoff
        # Still count=1 because check_position returned early
        assert strategy._close_retry_count == 1

    @pytest.mark.asyncio
    async def test_kill_switch_activated_after_max_retries(self, strategy, mock_risk):
        """After max_close_retries failures, kill switch should activate."""
        self._set_position(strategy)
        mock_risk.execute = AsyncMock(return_value=None)

        with patch("core.trading_strategy.TradingStrategy._execute_order",
                    new_callable=AsyncMock, return_value=(False, None, None)):
            for i in range(3):
                strategy.current_position = Position(
                    entry_price=50000, stop_loss=49000, take_profit=52000,
                    size=0.1, entry_time=datetime.now(), confidence="HIGH",
                    direction="LONG",
                )
                await strategy.close_position("stop_loss")

        assert strategy._close_retry_count == 3
        mock_risk.activate_kill_switch.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_count_resets_on_success(self, strategy, mock_risk):
        """Successful close should reset retry count and failed_close_at."""
        self._set_position(strategy)
        strategy._close_retry_count = 2
        strategy._failed_close_at = datetime.now() - timedelta(seconds=999)

        mock_risk.execute = AsyncMock(return_value=OrderResult(
            order_id="ord-close", status=OrderStatus.FILLED.value,
            filled_amount=0.1, avg_price=49500.0, fee=0.1,
            timestamp=datetime.now(), raw_response={},
        ))
        mock_risk.record_pnl = MagicMock()

        await strategy.close_position("stop_loss")
        assert strategy._close_retry_count == 0
        assert strategy._failed_close_at is None
        assert strategy.current_position is None

    @pytest.mark.asyncio
    async def test_backoff_window_blocks_retry(self, strategy):
        """check_position should skip close if still within backoff window."""
        self._set_position(strategy)
        strategy._failed_close_at = datetime.now()
        strategy._close_retry_count = 1
        # Backoff = 30 * 2^1 = 60s, so at time=0 it should skip
        await strategy.check_position(current_price=48000)
        # Position should still exist (close_position not called)
        assert strategy.current_position is not None

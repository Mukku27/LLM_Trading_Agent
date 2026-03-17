"""
Unit tests for LiveEngine error handling.
Covers CRITICAL-5: transient errors should fallback, auth errors should propagate.
"""

from unittest.mock import AsyncMock

import ccxt
import pytest

from execution.live_engine import LiveEngine
from utils.dataclass import OrderStatus


@pytest.fixture
def mock_connector():
    connector = AsyncMock()
    connector.close = AsyncMock()
    return connector


@pytest.fixture
def engine(mock_connector, base_config, mock_logger):
    return LiveEngine(mock_connector, base_config, mock_logger)


class TestGetBalanceErrorHandling:
    @pytest.mark.asyncio
    async def test_timeout_returns_last_known_balance(self, engine, mock_connector):
        """Transient error with cached balance returns stale data."""
        mock_connector.fetch_balance.return_value = {
            "total": {"USDC": 5000.0}, "free": {"USDC": 5000.0}, "used": {"USDC": 0.0},
        }
        # First call succeeds and caches
        balance1 = await engine.get_balance()
        assert balance1.total["USDC"] == 5000.0

        # Second call fails with timeout
        mock_connector.fetch_balance.side_effect = ccxt.RequestTimeout("timed out")
        balance2 = await engine.get_balance()
        assert balance2.total["USDC"] == 5000.0  # stale but valid

    @pytest.mark.asyncio
    async def test_timeout_no_cache_raises(self, engine, mock_connector):
        """Transient error with no cached balance re-raises."""
        mock_connector.fetch_balance.side_effect = ccxt.RequestTimeout("timed out")
        with pytest.raises(ccxt.RequestTimeout):
            await engine.get_balance()

    @pytest.mark.asyncio
    async def test_rate_limit_returns_last_known_balance(self, engine, mock_connector):
        """Rate limit (NetworkError subclass) should fallback to cache."""
        mock_connector.fetch_balance.return_value = {
            "total": {"USDC": 8000.0}, "free": {"USDC": 8000.0}, "used": {"USDC": 0.0},
        }
        await engine.get_balance()

        mock_connector.fetch_balance.side_effect = ccxt.RateLimitExceeded("429")
        balance = await engine.get_balance()
        assert balance.total["USDC"] == 8000.0

    @pytest.mark.asyncio
    async def test_auth_error_propagated(self, engine, mock_connector):
        """Auth errors must NOT be caught — they indicate a fundamental problem."""
        mock_connector.fetch_balance.side_effect = ccxt.AuthenticationError("invalid key")
        with pytest.raises(ccxt.AuthenticationError):
            await engine.get_balance()


class TestGetOpenOrdersErrorHandling:
    @pytest.mark.asyncio
    async def test_timeout_returns_empty_list(self, engine, mock_connector):
        mock_connector.fetch_open_orders.side_effect = ccxt.RequestTimeout("timed out")
        result = await engine.get_open_orders("BTC/USDC")
        assert result == []

    @pytest.mark.asyncio
    async def test_auth_error_propagated(self, engine, mock_connector):
        mock_connector.fetch_open_orders.side_effect = ccxt.AuthenticationError("revoked")
        with pytest.raises(ccxt.AuthenticationError):
            await engine.get_open_orders("BTC/USDC")


class TestGetOrderStatusErrorHandling:
    @pytest.mark.asyncio
    async def test_timeout_returns_pending(self, engine, mock_connector):
        mock_connector.fetch_order.side_effect = ccxt.RequestTimeout("timed out")
        status = await engine.get_order_status("ord-1", "BTC/USDC")
        assert status == OrderStatus.PENDING

    @pytest.mark.asyncio
    async def test_auth_error_propagated(self, engine, mock_connector):
        mock_connector.fetch_order.side_effect = ccxt.AuthenticationError("bad key")
        with pytest.raises(ccxt.AuthenticationError):
            await engine.get_order_status("ord-1", "BTC/USDC")


class TestSyncPortfolioErrorHandling:
    @pytest.mark.asyncio
    async def test_transient_balance_failure_uses_cache(self, engine, mock_connector):
        """sync_portfolio delegates to get_balance, which should fallback."""
        mock_connector.fetch_balance.return_value = {
            "total": {"USDC": 9000.0}, "free": {"USDC": 9000.0}, "used": {"USDC": 0.0},
        }
        await engine.get_balance()  # populate cache

        mock_connector.fetch_balance.side_effect = ccxt.ExchangeNotAvailable("maintenance")
        portfolio = await engine.sync_portfolio()
        assert portfolio.total_equity == 9000.0

"""
Unit tests for LiveEngine error handling and equity calculation.
Covers CRITICAL-5 (transient/fatal error classification) and
MEDIUM-8 (equity must convert balances to quote currency via ticker).
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
    connector.fetch_ticker = AsyncMock()
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


class TestEquityCalculation:
    """MEDIUM-8: equity must convert non-quote balances via ticker prices."""

    @pytest.mark.asyncio
    async def test_quote_only_balance_no_ticker_call(self, engine, mock_connector):
        """If the only currency is the quote, no ticker fetch is needed."""
        mock_connector.fetch_balance.return_value = {
            "total": {"USDC": 5000.0}, "free": {"USDC": 5000.0}, "used": {},
        }
        portfolio = await engine.sync_portfolio()
        assert portfolio.total_equity == 5000.0
        mock_connector.fetch_ticker.assert_not_called()

    @pytest.mark.asyncio
    async def test_mixed_balances_converted_via_ticker(self, engine, mock_connector):
        """BTC balance should be converted to USDC using BTC/USDC ticker."""
        mock_connector.fetch_balance.return_value = {
            "total": {"USDC": 2000.0, "BTC": 0.5},
            "free": {"USDC": 2000.0, "BTC": 0.5},
            "used": {},
        }
        mock_connector.fetch_ticker.return_value = {"last": 60000.0}
        portfolio = await engine.sync_portfolio()
        # 2000 USDC + 0.5 BTC * 60000 = 32000
        assert portfolio.total_equity == 32000.0
        mock_connector.fetch_ticker.assert_called_once_with("BTC/USDC")

    @pytest.mark.asyncio
    async def test_multiple_non_quote_currencies(self, engine, mock_connector):
        """Multiple non-quote currencies each get a ticker lookup."""
        mock_connector.fetch_balance.return_value = {
            "total": {"USDC": 1000.0, "BTC": 0.1, "ETH": 2.0},
            "free": {"USDC": 1000.0, "BTC": 0.1, "ETH": 2.0},
            "used": {},
        }

        async def mock_ticker(symbol):
            tickers = {"BTC/USDC": {"last": 50000.0}, "ETH/USDC": {"last": 3000.0}}
            return tickers[symbol]

        mock_connector.fetch_ticker.side_effect = mock_ticker
        portfolio = await engine.sync_portfolio()
        # 1000 + 0.1*50000 + 2.0*3000 = 1000 + 5000 + 6000 = 12000
        assert portfolio.total_equity == 12000.0

    @pytest.mark.asyncio
    async def test_ticker_failure_skips_currency(self, engine, mock_connector):
        """If ticker fetch fails for a currency, skip it (log warning)."""
        mock_connector.fetch_balance.return_value = {
            "total": {"USDC": 3000.0, "BTC": 0.5},
            "free": {"USDC": 3000.0, "BTC": 0.5},
            "used": {},
        }
        mock_connector.fetch_ticker.side_effect = ccxt.ExchangeError("no pair")
        portfolio = await engine.sync_portfolio()
        # BTC skipped, only USDC counted
        assert portfolio.total_equity == 3000.0

    @pytest.mark.asyncio
    async def test_zero_balance_skipped(self, engine, mock_connector):
        """Zero-amount currencies should not trigger ticker fetches."""
        mock_connector.fetch_balance.return_value = {
            "total": {"USDC": 1000.0, "BTC": 0.0},
            "free": {"USDC": 1000.0},
            "used": {},
        }
        portfolio = await engine.sync_portfolio()
        assert portfolio.total_equity == 1000.0
        mock_connector.fetch_ticker.assert_not_called()

"""
Unit tests for PaperEngine error classification.
Covers CRITICAL-1: only testnet-unavailability errors should produce simulated fills;
auth, funds, and parameter errors must return FAILED.
"""

from unittest.mock import AsyncMock, MagicMock

import ccxt
import pytest

from execution.paper_engine import PaperEngine
from utils.dataclass import OrderRequest, OrderStatus


@pytest.fixture
def mock_connector():
    connector = AsyncMock()
    connector.close = AsyncMock()
    return connector


@pytest.fixture
def paper_engine(mock_connector, base_config, mock_logger):
    base_config.set("execution", "mode", "paper")
    return PaperEngine(mock_connector, base_config, mock_logger)


def _make_order(**overrides):
    defaults = dict(symbol="BTC/USDC", side="buy", order_type="market", amount=0.001, price=50000.0)
    defaults.update(overrides)
    return OrderRequest(**defaults)


# ------------------------------------------------------------------
# Happy path: testnet returns a real response
# ------------------------------------------------------------------

class TestTestnetSuccess:
    @pytest.mark.asyncio
    async def test_maps_closed_to_filled(self, paper_engine, mock_connector):
        mock_connector.create_order.return_value = {
            "id": "order-1",
            "status": "closed",
            "filled": 0.001,
            "average": 50000.0,
            "fee": {"cost": 0.05},
        }
        result = await paper_engine.place_order(_make_order())
        assert result.status == OrderStatus.FILLED.value
        assert result.filled_amount == 0.001
        assert result.avg_price == 50000.0


# ------------------------------------------------------------------
# Simulatable errors: testnet unavailable → simulated fill
# ------------------------------------------------------------------

class TestSimulatableErrors:
    @pytest.mark.asyncio
    async def test_network_error_produces_simulated_fill(self, paper_engine, mock_connector):
        mock_connector.create_order.side_effect = ccxt.NetworkError("connection reset")
        result = await paper_engine.place_order(_make_order())
        assert result.status == OrderStatus.FILLED.value
        assert result.raw_response["simulated"] is True

    @pytest.mark.asyncio
    async def test_exchange_not_available_produces_simulated_fill(self, paper_engine, mock_connector):
        mock_connector.create_order.side_effect = ccxt.ExchangeNotAvailable("testnet down")
        result = await paper_engine.place_order(_make_order())
        assert result.status == OrderStatus.FILLED.value
        assert result.raw_response["simulated"] is True

    @pytest.mark.asyncio
    async def test_on_maintenance_produces_simulated_fill(self, paper_engine, mock_connector):
        mock_connector.create_order.side_effect = ccxt.OnMaintenance("scheduled maintenance")
        result = await paper_engine.place_order(_make_order())
        assert result.status == OrderStatus.FILLED.value
        assert result.raw_response["simulated"] is True

    @pytest.mark.asyncio
    async def test_request_timeout_produces_simulated_fill(self, paper_engine, mock_connector):
        mock_connector.create_order.side_effect = ccxt.RequestTimeout("timed out")
        result = await paper_engine.place_order(_make_order())
        assert result.status == OrderStatus.FILLED.value
        assert result.raw_response["simulated"] is True


# ------------------------------------------------------------------
# Non-simulatable errors: auth / funds / params → FAILED
# ------------------------------------------------------------------

class TestNonSimulatableErrors:
    @pytest.mark.asyncio
    async def test_auth_error_returns_failed(self, paper_engine, mock_connector):
        mock_connector.create_order.side_effect = ccxt.AuthenticationError("invalid key")
        result = await paper_engine.place_order(_make_order())
        assert result.status == OrderStatus.FAILED.value
        assert result.filled_amount == 0.0
        assert "AuthenticationError" in result.raw_response["error_type"]

    @pytest.mark.asyncio
    async def test_insufficient_funds_returns_failed(self, paper_engine, mock_connector):
        mock_connector.create_order.side_effect = ccxt.InsufficientFunds("not enough USDC")
        result = await paper_engine.place_order(_make_order())
        assert result.status == OrderStatus.FAILED.value
        assert result.filled_amount == 0.0

    @pytest.mark.asyncio
    async def test_invalid_order_returns_failed(self, paper_engine, mock_connector):
        mock_connector.create_order.side_effect = ccxt.InvalidOrder("amount too small")
        result = await paper_engine.place_order(_make_order())
        assert result.status == OrderStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_bad_symbol_returns_failed(self, paper_engine, mock_connector):
        mock_connector.create_order.side_effect = ccxt.BadSymbol("INVALID/PAIR")
        result = await paper_engine.place_order(_make_order())
        assert result.status == OrderStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_permission_denied_returns_failed(self, paper_engine, mock_connector):
        mock_connector.create_order.side_effect = ccxt.PermissionDenied("IP not whitelisted")
        result = await paper_engine.place_order(_make_order())
        assert result.status == OrderStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_generic_exception_returns_failed(self, paper_engine, mock_connector):
        mock_connector.create_order.side_effect = RuntimeError("unexpected")
        result = await paper_engine.place_order(_make_order())
        assert result.status == OrderStatus.FAILED.value
        assert result.filled_amount == 0.0

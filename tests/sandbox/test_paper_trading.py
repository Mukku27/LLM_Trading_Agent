"""
Sandbox tests for PaperEngine.
Uses a mock connector to test paper trading logic without real credentials.
"""

import os
from unittest.mock import patch, AsyncMock

import pytest

from execution.connectors.binance import BinanceConnector
from execution.paper_engine import PaperEngine
from utils.dataclass import OrderRequest, OrderStatus


@pytest.fixture
def mock_connector():
    connector = AsyncMock()
    connector.create_order = AsyncMock(return_value={
        "id": "test-order-1",
        "status": "closed",
        "filled": 0.001,
        "average": 50000.0,
        "fee": {"cost": 0.05},
    })
    connector.fetch_balance = AsyncMock(return_value={
        "total": {"USDC": 10000.0},
        "free": {"USDC": 10000.0},
        "used": {"USDC": 0.0},
    })
    connector.close = AsyncMock()
    return connector


@pytest.fixture
def paper_engine(mock_connector, base_config, mock_logger):
    base_config.set("execution", "mode", "paper")
    return PaperEngine(mock_connector, base_config, mock_logger)


@pytest.mark.asyncio
async def test_place_order_testnet_success(paper_engine):
    """PaperEngine routes to connector and maps response correctly."""
    order = OrderRequest(
        symbol="BTC/USDC", side="buy", order_type="market",
        amount=0.001, price=50000.0,
    )
    result = await paper_engine.place_order(order)
    assert result.status == OrderStatus.FILLED.value
    assert result.filled_amount == 0.001
    await paper_engine.close()


@pytest.mark.asyncio
async def test_sync_portfolio_simulated(paper_engine):
    portfolio = await paper_engine.sync_portfolio()
    assert portfolio.total_equity >= 10000
    await paper_engine.close()


def test_binance_connector_rejects_missing_credentials():
    """Connector must fail fast when credentials are empty."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="credentials are missing or empty"):
            BinanceConnector(sandbox=True)

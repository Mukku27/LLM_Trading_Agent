"""
Sandbox tests for PaperEngine.
Run without credentials — falls back to simulated fills.
"""

import pytest

from execution.connectors.binance import BinanceConnector
from execution.paper_engine import PaperEngine
from utils.dataclass import OrderRequest, OrderStatus


@pytest.fixture
def paper_engine(base_config, mock_logger):
    connector = BinanceConnector(sandbox=True)
    base_config.set("execution", "mode", "paper")
    return PaperEngine(connector, base_config, mock_logger)


@pytest.mark.asyncio
async def test_place_order_simulated_fallback(paper_engine):
    """Without real testnet creds, PaperEngine should fall back to simulated fill."""
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

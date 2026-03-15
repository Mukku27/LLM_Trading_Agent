import pytest

from execution.dry_run_engine import DryRunEngine
from utils.dataclass import OrderRequest, OrderStatus


@pytest.fixture
def engine(base_config, mock_logger):
    return DryRunEngine(base_config, mock_logger)


@pytest.mark.asyncio
async def test_place_order_returns_filled(engine):
    order = OrderRequest(
        symbol="BTC/USDC", side="buy", order_type="market",
        amount=0.01, price=50000.0,
    )
    result = await engine.place_order(order)
    assert result.status == OrderStatus.FILLED.value
    assert result.filled_amount == 0.01
    assert result.fee == 0.0
    assert result.order_id


@pytest.mark.asyncio
async def test_cancel_order_always_succeeds(engine):
    assert await engine.cancel_order("fake-id") is True


@pytest.mark.asyncio
async def test_get_balance_returns_simulated(engine):
    balance = await engine.get_balance()
    assert balance.total["USDC"] == DryRunEngine.SIMULATED_EQUITY


@pytest.mark.asyncio
async def test_sync_portfolio(engine):
    portfolio = await engine.sync_portfolio()
    assert portfolio.total_equity == DryRunEngine.SIMULATED_EQUITY
    assert portfolio.unrealized_pnl == 0.0


@pytest.mark.asyncio
async def test_get_order_status_always_filled(engine):
    status = await engine.get_order_status("any")
    assert status == OrderStatus.FILLED

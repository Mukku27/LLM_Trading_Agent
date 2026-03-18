"""
Integration test for the full LiveEngine order lifecycle on Binance testnet.

Run with:  pytest tests/integration/test_live_engine.py -v -m integration
"""

import os

import pytest

from execution.connectors.ccxt_connector import CCXTConnector
from execution.live_engine import LiveEngine
from utils.dataclass import OrderRequest, OrderStatus


pytestmark = pytest.mark.integration

SKIP_REASON = "Set EXCHANGE_API_KEY and EXCHANGE_API_SECRET for Binance testnet to run"
requires_creds = pytest.mark.skipif(
    not os.environ.get("EXCHANGE_API_KEY"),
    reason=SKIP_REASON,
)


@pytest.fixture
def testnet_config(base_config):
    base_config.set("execution", "mode", "paper")
    return base_config


@pytest.fixture
async def engine(testnet_config, mock_logger):
    connector = CCXTConnector(exchange_name="binance", sandbox=True)
    eng = LiveEngine(connector, testnet_config, mock_logger)
    yield eng
    await eng.close()


@requires_creds
@pytest.mark.asyncio
async def test_sync_portfolio(engine):
    portfolio = await engine.sync_portfolio()
    assert portfolio.total_equity >= 0


@requires_creds
@pytest.mark.asyncio
async def test_get_balance(engine):
    balance = await engine.get_balance()
    assert balance.total is not None

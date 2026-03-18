"""
Integration tests for CCXTConnector (binance) against the Binance testnet.

These tests require:
  EXCHANGE_API_KEY and EXCHANGE_API_SECRET env vars pointing at Binance testnet credentials.

Run with:  pytest tests/integration/ -v -m integration
"""

import os

import pytest
import pytest_asyncio

from execution.connectors.ccxt_connector import CCXTConnector


pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("EXCHANGE_API_KEY") or not os.environ.get("EXCHANGE_API_SECRET"),
    reason="Set EXCHANGE_API_KEY and EXCHANGE_API_SECRET for Binance testnet to run",
)]


@pytest_asyncio.fixture
async def connector():
    c = CCXTConnector(exchange_name="binance", sandbox=True)
    yield c
    await c.close()


@pytest.mark.asyncio
async def test_fetch_balance(connector):
    balance = await connector.fetch_balance()
    assert "total" in balance
    assert isinstance(balance["total"], dict)


@pytest.mark.asyncio
async def test_fetch_open_orders(connector):
    orders = await connector.fetch_open_orders("BTC/USDT")
    assert isinstance(orders, list)

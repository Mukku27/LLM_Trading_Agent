"""
Unit tests for connector credential validation at construction time.
Covers CRITICAL-2: empty-string credentials must be rejected immediately.
"""

import os
from unittest.mock import patch

import pytest

from execution.connectors.binance import BinanceConnector
from execution.connectors.ccxt_connector import CCXTConnector


class TestBinanceConnectorCredentials:
    def test_raises_on_missing_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="credentials are missing or empty"):
                BinanceConnector(sandbox=True)

    def test_raises_on_empty_api_key(self):
        with patch.dict(os.environ, {"EXCHANGE_API_KEY": "", "EXCHANGE_API_SECRET": "valid"}, clear=True):
            with pytest.raises(ValueError, match="credentials are missing or empty"):
                BinanceConnector(sandbox=True)

    def test_raises_on_empty_api_secret(self):
        with patch.dict(os.environ, {"EXCHANGE_API_KEY": "valid", "EXCHANGE_API_SECRET": ""}, clear=True):
            with pytest.raises(ValueError, match="credentials are missing or empty"):
                BinanceConnector(sandbox=True)

    def test_accepts_valid_credentials(self):
        with patch.dict(os.environ, {"EXCHANGE_API_KEY": "key123", "EXCHANGE_API_SECRET": "secret456"}, clear=True):
            connector = BinanceConnector(sandbox=True)
            assert connector.exchange is not None


class TestCCXTConnectorCredentials:
    def test_raises_on_missing_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="credentials are missing or empty"):
                CCXTConnector(exchange_name="binance", sandbox=True)

    def test_raises_on_empty_api_key(self):
        with patch.dict(os.environ, {"EXCHANGE_API_KEY": "", "EXCHANGE_API_SECRET": "valid"}, clear=True):
            with pytest.raises(ValueError, match="credentials are missing or empty"):
                CCXTConnector(exchange_name="binance", sandbox=True)

    def test_raises_on_empty_api_secret(self):
        with patch.dict(os.environ, {"EXCHANGE_API_KEY": "valid", "EXCHANGE_API_SECRET": ""}, clear=True):
            with pytest.raises(ValueError, match="credentials are missing or empty"):
                CCXTConnector(exchange_name="binance", sandbox=True)

    def test_accepts_valid_credentials(self):
        with patch.dict(os.environ, {"EXCHANGE_API_KEY": "key123", "EXCHANGE_API_SECRET": "secret456"}, clear=True):
            connector = CCXTConnector(exchange_name="binance", sandbox=True)
            assert connector.exchange is not None

    def test_unsupported_exchange_still_raises(self):
        with patch.dict(os.environ, {"EXCHANGE_API_KEY": "key123", "EXCHANGE_API_SECRET": "secret456"}, clear=True):
            with pytest.raises(ValueError, match="Unsupported exchange"):
                CCXTConnector(exchange_name="nonexistent", sandbox=True)

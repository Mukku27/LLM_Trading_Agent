import configparser
import logging
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_logger():
    logger = MagicMock(spec=logging.Logger)
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.debug = MagicMock()
    return logger


@pytest.fixture
def base_config():
    config = configparser.ConfigParser()
    config.read_dict({
        "exchange": {
            "name": "binance",
            "symbol": "BTC/USDC",
            "timeframe": "1m",
            "limit": "730",
        },
        "trading": {
            "position_size": "0.1",
            "default_stop_loss_pct": "2",
            "default_take_profit_pct": "4",
            "sentiment_refresh_interval": "1",
        },
        "execution": {
            "mode": "dry_run",
            "exchange": "binance",
            "confirm_trades": "false",
            "max_position_pct": "5.0",
            "max_daily_loss_pct": "10.0",
            "max_open_positions": "3",
            "kill_switch": "false",
            "symbol_whitelist": "BTC/USDC",
            "cooldown_seconds": "0",
            "order_timeout_seconds": "300",
            "max_orders_per_minute": "100",
        },
    })
    return config

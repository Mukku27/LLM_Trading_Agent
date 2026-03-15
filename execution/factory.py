import configparser

from execution.base import ExecutionEngine
from execution.dry_run_engine import DryRunEngine
from execution.live_engine import LiveEngine
from execution.paper_engine import PaperEngine
from execution.connectors.binance import BinanceConnector
from execution.connectors.coinbase import CoinbaseConnector


def _create_connector(exchange_name: str, sandbox: bool = False):
    """Instantiate the appropriate ExchangeConnector."""
    connectors = {
        "binance": lambda: BinanceConnector(sandbox=sandbox),
        "coinbase": lambda: CoinbaseConnector(sandbox=sandbox),
    }
    factory_fn = connectors.get(exchange_name.lower())
    if factory_fn is None:
        raise ValueError(
            f"Unsupported exchange: {exchange_name}. "
            f"Available: {list(connectors.keys())}"
        )
    return factory_fn()


def create_execution_engine(
    config: configparser.ConfigParser,
    logger,
) -> ExecutionEngine:
    """Build the correct ExecutionEngine based on config [execution] mode."""
    mode = config.get("execution", "mode", fallback="dry_run")
    exchange_name = config.get("execution", "exchange", fallback="binance")

    if mode == "dry_run":
        logger.info("Execution mode: dry_run")
        return DryRunEngine(config, logger)

    elif mode == "paper":
        logger.info(f"Execution mode: paper (sandbox) on {exchange_name}")
        connector = _create_connector(exchange_name, sandbox=True)
        return PaperEngine(connector, config, logger)

    elif mode == "live":
        logger.info(f"Execution mode: LIVE on {exchange_name}")
        connector = _create_connector(exchange_name, sandbox=False)
        return LiveEngine(connector, config, logger)

    else:
        raise ValueError(f"Unknown execution mode: {mode}")

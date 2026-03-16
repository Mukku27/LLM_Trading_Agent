import configparser

from execution.base import ExecutionEngine
from execution.connectors.ccxt_connector import CCXTConnector
from execution.credentials import CredentialManager
from execution.dry_run_engine import DryRunEngine
from execution.live_engine import LiveEngine
from execution.paper_engine import PaperEngine


def create_execution_engine(
    config: configparser.ConfigParser,
    logger,
) -> ExecutionEngine:
    """Build the correct ExecutionEngine based on config [execution] mode."""
    mode = config.get("execution", "mode", fallback="dry_run")
    exchange_name = config.get("execution", "exchange", fallback="binance")

    _VALID_MODES = {"dry_run", "paper", "live"}
    if mode not in _VALID_MODES:
        raise ValueError(f"Unknown execution mode: {mode}")

    if mode == "dry_run":
        logger.info("Execution mode: dry_run")
        return DryRunEngine(config, logger)

    creds = CredentialManager(logger)
    has_creds = creds.load(exchange_name=exchange_name)

    if mode == "paper":
        if not has_creds:
            logger.warning(
                "No exchange credentials found for paper mode. "
                "PaperEngine will use simulated fills for all orders."
            )
        logger.info(f"Execution mode: paper (sandbox) on {exchange_name}")
        connector = CCXTConnector(exchange_name, sandbox=True)
        return PaperEngine(connector, config, logger)

    if not has_creds:
        raise RuntimeError(
            f"Execution mode '{mode}' requires exchange credentials. "
            f"Set EXCHANGE_API_KEY and EXCHANGE_API_SECRET in your .env file."
        )

    logger.info(f"Execution mode: LIVE on {exchange_name}")
    connector = CCXTConnector(exchange_name, sandbox=False)
    return LiveEngine(connector, config, logger)

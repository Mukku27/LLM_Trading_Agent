import asyncio
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Any

if sys.version_info < (3, 13):
    print("Error: TransformerBot requires Python 3.13 or higher")
    sys.exit(1)

from dotenv import load_dotenv
from ccxt import NotSupported

from core.market_analyzer import MarketAnalyzer
from core.trading_strategy import TradingStrategy
from execution.audit import AuditLog
from execution.factory import create_execution_engine
from execution.order_tracker import OrderTracker
from execution.risk_manager import RiskManager
from logger.logger import Logger
from utils.retry_decorator import retry_async

env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)


async def shutdown(loop, strategy: TradingStrategy, logger: Logger) -> None:
    logger.info("Shutting down gracefully...")
    await strategy.close()

    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [task.cancel() for task in tasks]

    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()


@retry_async()
async def _wait_for_next_timeframe_step(strategy, delay: Optional[int] = None, add_delay: int = 0) -> None:
    try:
        current_time_ms = await strategy.exchange.fetch_time()
    except NotSupported:
        strategy.logger.debug(f"{strategy.exchange.id} does not support fetch_time(). Using local time instead.")
        current_time_ms = int(time.time() * 1000)
    except Exception as e:
        strategy.logger.exception(f"Error fetching time from {strategy.exchange.id}, using local time: {str(e)}")
        current_time_ms = int(time.time() * 1000)

    interval_ms = strategy.interval * 1000
    next_timeframe_start_ms = (current_time_ms // interval_ms + 1) * interval_ms

    if delay is None:
        delay_ms = next_timeframe_start_ms - current_time_ms + add_delay * 1000

        delay_seconds = delay_ms / 1000
        next_check_time = datetime.fromtimestamp(next_timeframe_start_ms / 1000)

        wait_time = str(timedelta(seconds=int(delay_seconds)))
        strategy.logger.info(f"Next check in {wait_time} at {next_check_time.strftime('%Y-%m-%d %H:%M:%S')}")
        await asyncio.sleep(delay_seconds)
        return
    else:
        delay_ms = delay * 1000
        wait_time = str(timedelta(seconds=delay))
        strategy.logger.info(f"Using fixed delay of {wait_time}")

    await asyncio.sleep(delay_ms / 1000)


async def periodic_check(strategy: TradingStrategy) -> None:
    check_count = 0

    while True:
        try:
            await _wait_for_next_timeframe_step(strategy)
            current_time = datetime.now()
            check_count += 1

            strategy.logger.info("=" * 50)
            strategy.logger.info(f"Periodic Check #{check_count} at {current_time}")

            market_data = await strategy.fetch_ohlcv()
            current_price = strategy.periods['3D'].data[-1].close
            if strategy.current_position:
                await strategy.check_position(current_price)

            strategy.logger.info("Performing market analysis...")
            analysis = await strategy.analyze_trend(market_data)
            await strategy.process_analysis(analysis)
        except Exception as e:
            strategy.logger.exception(f"Error during periodic check: {e}")
            await asyncio.sleep(60)


async def run(strategy: TradingStrategy) -> None:
    tasks: list[asyncio.Task[Any]] = []
    try:
        strategy.logger.info(f"Starting {strategy.symbol} analyzer...")
        strategy.current_position = strategy.data_persistence.load_position()
        check_task = asyncio.create_task(periodic_check(strategy))
        tasks.append(check_task)

        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        strategy.logger.info("Strategy received cancellation request...")
        for task in tasks:
            if not task.done():
                task.cancel("Application shutdown requested")
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        strategy.logger.error(f"Error in strategy: {e}")
        for task in tasks:
            if not task.done():
                task.cancel(f"Error occurred: {str(e)}")
        await asyncio.gather(*tasks, return_exceptions=True)


def _build_strategy(logger: Logger) -> TradingStrategy:
    """Construct the full component graph based on config."""
    analyzer = MarketAnalyzer(logger)
    config = analyzer.config

    engine = create_execution_engine(config, logger)
    risk_mgr = RiskManager(engine, config, logger)
    tracker = OrderTracker(logger)
    audit = AuditLog(logger)

    mode = config.get("execution", "mode", fallback="dry_run")
    logger.info(f"Execution pipeline ready  [mode={mode}]")

    return TradingStrategy(
        logger=logger,
        analyzer=analyzer,
        execution_engine=engine,
        risk_manager=risk_mgr,
        order_tracker=tracker,
        audit_log=audit,
    )


def main() -> None:
    logger = Logger(logger_name="Bot", logger_debug=False)
    strategy = _build_strategy(logger)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run(strategy))
    except KeyboardInterrupt:
        loop.run_until_complete(shutdown(loop, strategy, logger))
    finally:
        if loop.is_running():
            loop.close()


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()
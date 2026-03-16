import json
from datetime import datetime
from typing import Optional, Tuple

from core.market_analyzer import MarketAnalyzer
from execution.audit import AuditLog
from execution.base import ExecutionEngine
from execution.order_tracker import OrderTracker
from execution.risk_manager import RiskManager
from utils.dataclass import (
    Position, TradeDecision, TimeframeConfig,
    OrderRequest, OrderStatus,
)
from utils.position_extractor import PositionExtractor


class TradingStrategy:
    """
    Orchestrates trading decisions using composition.
    Routes orders through RiskManager -> ExecutionEngine -> OrderTracker -> AuditLog.
    In dry_run mode this preserves the exact same JSON persistence behavior.
    """

    def __init__(
        self,
        logger,
        analyzer: Optional[MarketAnalyzer] = None,
        execution_engine: Optional[ExecutionEngine] = None,
        risk_manager: Optional[RiskManager] = None,
        order_tracker: Optional[OrderTracker] = None,
        audit_log: Optional[AuditLog] = None,
    ) -> None:
        self.logger = logger
        self.analyzer: MarketAnalyzer = analyzer or MarketAnalyzer(logger)
        self.interval: int = TimeframeConfig.get_seconds(self.analyzer.timeframe)
        self.current_position: Optional[Position] = self.analyzer.data_persistence.load_position()
        self.extractor = PositionExtractor()

        self.execution_engine = execution_engine
        self.risk_manager = risk_manager
        self.order_tracker = order_tracker or OrderTracker(logger)
        self.audit_log = audit_log or AuditLog(logger)

        self._execution_mode: str = (
            self.analyzer.config.get("execution", "mode", fallback="dry_run")
            if hasattr(self.analyzer, "config") else "dry_run"
        )

        self._failed_close_at: Optional[datetime] = None
        self._close_retry_backoff_seconds: int = 30

    # --- Delegate attributes to the composed analyzer for backward compatibility ---

    @property
    def exchange(self):
        return self.analyzer.exchange

    @property
    def symbol(self):
        return self.analyzer.symbol

    @property
    def periods(self):
        return self.analyzer.periods

    @property
    def data_persistence(self):
        return self.analyzer.data_persistence

    @property
    def timeframe(self):
        return self.analyzer.timeframe

    async def close(self) -> None:
        await self.analyzer.close()
        if self.execution_engine:
            await self.execution_engine.close()
        if self.order_tracker:
            self.order_tracker.close()

    async def fetch_ohlcv(self):
        return await self.analyzer.fetch_ohlcv()

    async def analyze_trend(self, market_data):
        return await self.analyzer.analyze_trend(market_data)

    # --- Shared execution pipeline ---

    async def _execute_order(
        self,
        side: str,
        amount: float,
        price: float,
        event_prefix: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Tuple[bool, Optional[float], Optional[str]]:
        """
        Route an order through RiskManager -> ExecutionEngine -> OrderTracker -> AuditLog.
        Returns (success, fill_price, order_id).  If the execution pipeline is not
        configured, returns (True, price, None) so that the legacy JSON path always runs.

        Does NOT emit the ``_filled`` audit entry — callers are responsible for that
        so they can attach context-specific data (e.g. PnL on close).
        """
        if not (self.execution_engine and self.risk_manager):
            return True, price, None

        order_req = OrderRequest(
            symbol=self.symbol,
            side=side,
            order_type="market",
            amount=amount,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        portfolio = await self.execution_engine.sync_portfolio()
        open_count = len(portfolio.open_positions)

        is_closing = event_prefix == "close"
        result = await self.risk_manager.execute(order_req, portfolio.total_equity, open_count, is_closing=is_closing)

        if result is None:
            self.audit_log.record(
                event_type=f"{event_prefix}_rejected", mode=self._execution_mode,
                symbol=self.symbol, side=side, amount=amount,
                price=price, risk_result="rejected",
            )
            return False, None, None

        self.order_tracker.record_order(
            order_id=result.order_id, symbol=self.symbol, side=side,
            order_type="market", amount=amount, price=price,
            status=result.status, filled_amount=result.filled_amount,
            avg_price=result.avg_price, fee=result.fee,
            raw_response=json.dumps(result.raw_response),
        )

        if result.status != OrderStatus.FILLED.value:
            final_status = await self.order_tracker.poll_order(
                result.order_id, self.execution_engine, symbol=self.symbol
            )
            if final_status != OrderStatus.FILLED.value:
                self.logger.warning(
                    f"Order {result.order_id} ended as {final_status}, "
                    f"skipping {event_prefix}"
                )
                self.audit_log.record(
                    event_type=f"{event_prefix}_unfilled", mode=self._execution_mode,
                    symbol=self.symbol, side=side, amount=amount,
                    price=price, order_id=result.order_id, status=final_status,
                )
                return False, None, None

        fill_price = result.avg_price if result.avg_price > 0 else price
        return True, fill_price, result.order_id

    # --- Position management ---

    async def check_position(self, current_price: float) -> None:
        if not self.current_position:
            return

        if self._failed_close_at:
            elapsed = (datetime.now() - self._failed_close_at).total_seconds()
            if elapsed < self._close_retry_backoff_seconds:
                self.logger.debug(
                    f"Skipping close retry, backoff active for "
                    f"{self._close_retry_backoff_seconds - int(elapsed)}s more"
                )
                return
            self._failed_close_at = None

        if self.current_position.direction == 'LONG':
            if current_price <= self.current_position.stop_loss:
                await self.close_position("stop_loss")
            elif current_price >= self.current_position.take_profit:
                await self.close_position("take_profit")
        else:
            if current_price >= self.current_position.stop_loss:
                await self.close_position("stop_loss")
            elif current_price <= self.current_position.take_profit:
                await self.close_position("take_profit")

    async def close_position(self, reason: str) -> None:
        if not self.current_position:
            return

        current_price = self.periods['3D'].data[-1].close
        position_size = self.current_position.size
        confidence = self.current_position.confidence if self.current_position.confidence else "HIGH"
        direction = self.current_position.direction
        side = "sell" if direction == "LONG" else "buy"

        success, fill_price, order_id = await self._execute_order(
            side=side, amount=position_size, price=current_price,
            event_prefix="close",
        )
        if not success:
            self._failed_close_at = datetime.now()
            self.logger.warning(
                f"Failed to execute close for {direction} position ({reason}), "
                f"will retry after {self._close_retry_backoff_seconds}s backoff"
            )
            return

        pnl: Optional[float] = None
        if self.risk_manager and fill_price is not None:
            entry_price = self.current_position.entry_price
            if direction == "LONG":
                pnl = (fill_price - entry_price) * position_size
            else:
                pnl = (entry_price - fill_price) * position_size
            self.risk_manager.record_pnl(pnl)

        if order_id is not None:
            self.audit_log.record(
                event_type="close_filled", mode=self._execution_mode,
                symbol=self.symbol, side=side, amount=position_size,
                price=fill_price, order_id=order_id,
                status=OrderStatus.FILLED.value, pnl=pnl,
            )

        decision = TradeDecision(
            timestamp=datetime.now(),
            action=f"CLOSE_{direction}",
            price=current_price,
            confidence=confidence,
            stop_loss=self.current_position.stop_loss,
            take_profit=self.current_position.take_profit,
            position_size=position_size,
            reasoning=f"Position closed: {reason}"
        )

        self.logger.info(
            f"Closing {direction} position ({reason}) at {current_price:.2f}"
        )
        self.data_persistence.save_trade_decision(decision)
        self.data_persistence.save_position(None)
        self.current_position = None
        self._failed_close_at = None

    def _should_close_position(self, signal: str) -> bool:
        return self.current_position and signal == "CLOSE"

    def _update_position_parameters(self, stop_loss: Optional[float],
                                    take_profit: Optional[float]) -> None:
        if not self.current_position:
            return

        updated = False
        if stop_loss and stop_loss != self.current_position.stop_loss:
            self.current_position.stop_loss = stop_loss
            self.logger.info(f"Updated Stop Loss: {stop_loss:.2f}")
            updated = True

        if take_profit and take_profit != self.current_position.take_profit:
            self.current_position.take_profit = take_profit
            self.logger.info(f"Updated Take Profit: {take_profit:.2f}")
            updated = True

        if updated:
            self.data_persistence.save_position(self.current_position)

    async def _open_new_position(
            self,
            signal: str,
            current_price: float,
            confidence: str,
            stop_loss: Optional[float],
            take_profit: Optional[float],
            position_size: Optional[float] = None
    ) -> None:
        if signal == "BUY":
            direction = "LONG"
            side = "buy"
            default_sl = current_price * 0.98
            default_tp = current_price * 1.04
        elif signal == "SELL":
            direction = "SHORT"
            side = "sell"
            default_sl = current_price * 1.02
            default_tp = current_price * 0.96
        else:
            raise ValueError(f"Invalid signal for position opening: {signal}")

        final_sl = stop_loss if stop_loss else default_sl
        final_tp = take_profit if take_profit else default_tp
        final_position_size = position_size if position_size is not None else 0.1

        success, fill_price, order_id = await self._execute_order(
            side=side, amount=final_position_size, price=current_price,
            event_prefix="open", stop_loss=final_sl, take_profit=final_tp,
        )
        if not success:
            return

        if fill_price is not None and fill_price > 0:
            current_price = fill_price

        if order_id is not None:
            self.audit_log.record(
                event_type="open_filled", mode=self._execution_mode,
                symbol=self.symbol, side=side, amount=final_position_size,
                price=current_price, order_id=order_id,
                status=OrderStatus.FILLED.value,
            )

        self.current_position = Position(
            entry_price=current_price,
            stop_loss=final_sl,
            take_profit=final_tp,
            size=final_position_size,
            entry_time=datetime.now(),
            confidence=confidence,
            direction=direction
        )

        decision = TradeDecision(
            timestamp=datetime.now(),
            action=signal.upper(),
            price=current_price,
            confidence=confidence,
            stop_loss=final_sl,
            take_profit=final_tp,
            position_size=final_position_size,
            reasoning=f"Opened new {direction} position"
        )
        self.data_persistence.save_position(self.current_position)
        self.data_persistence.save_trade_decision(decision)

    async def process_analysis(self, analysis: str) -> None:
        try:
            current_price = self.periods['3D'].data[-1].close
            signal, confidence, stop_loss, take_profit, position_size = self.extractor.extract_trading_info(analysis)
            self.logger.info(f"Extracted Signal: {signal}, Confidence: {confidence}")

            if self.current_position:
                try:
                    if self._should_close_position(signal):
                        self.logger.info("Closing position based on analysis signal...")
                        await self.close_position("analysis_signal")
                        return

                    self._update_position_parameters(stop_loss, take_profit)
                    return

                except AttributeError:
                    self.logger.warning("Position appears to be already closed")
                    self.current_position = None
                    return

            if signal in ["BUY", "SELL"]:
                await self._open_new_position(
                    signal,
                    current_price,
                    confidence,
                    stop_loss,
                    take_profit,
                    position_size
                )
            elif signal == "CLOSE":
                self.logger.warning("Received CLOSE signal without open position")
            else:
                self.logger.info(f"No valid trading signal ({signal}).")
        except Exception as e:
            self.logger.error(f"Error processing analysis: {e}")
            return
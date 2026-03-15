import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any


class AuditLog:
    """
    Append-only JSONL audit trail for every order attempt.
    Every decision — approved or rejected — is recorded with full context.
    """

    def __init__(self, logger, data_dir: str = "trading_data") -> None:
        self.logger = logger
        self._log_path = Path(data_dir) / "audit_log.jsonl"
        self._log_path.parent.mkdir(exist_ok=True)

    def record(
        self,
        event_type: str,
        mode: str,
        symbol: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        order_id: Optional[str] = None,
        status: Optional[str] = None,
        risk_result: Optional[str] = None,
        pnl: Optional[float] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": event_type,
            "mode": mode,
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "price": price,
            "order_id": order_id,
            "status": status,
            "risk_result": risk_result,
            "pnl": pnl,
        }
        if extra:
            entry["extra"] = extra

        try:
            with open(self._log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            self.logger.error(f"[Audit] Failed to write log: {e}")

    def read_recent(self, n: int = 50) -> list:
        if not self._log_path.exists():
            return []
        try:
            with open(self._log_path, "r") as f:
                lines = f.readlines()
            return [json.loads(line) for line in lines[-n:]]
        except Exception as e:
            self.logger.error(f"[Audit] Failed to read log: {e}")
            return []

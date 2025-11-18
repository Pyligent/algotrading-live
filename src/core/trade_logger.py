from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from src.core.logging_utils import get_logger

logger = get_logger(__name__)


@dataclass
class TradeRecord:
    timestamp: datetime
    mode: str  # "backtest" or "live"
    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: int
    price: float
    realized_pnl: Optional[float] = None
    reason: Optional[str] = None
    profile_name: Optional[str] = None


class TradeBlotter:
    def __init__(self, log_dir: str):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.trades_path = self.log_dir / "trades.csv"
        if not self.trades_path.exists():
            with self.trades_path.open("w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "timestamp",
                        "mode",
                        "symbol",
                        "side",
                        "quantity",
                        "price",
                        "realized_pnl",
                        "reason",
                        "profile_name",
                    ]
                )

    def log(self, record: TradeRecord) -> None:
        try:
            with self.trades_path.open("a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        record.timestamp.isoformat(),
                        record.mode,
                        record.symbol,
                        record.side,
                        record.quantity,
                        f"{record.price:.4f}",
                        "" if record.realized_pnl is None else f"{record.realized_pnl:.4f}",
                        "" if record.reason is None else record.reason,
                        "" if record.profile_name is None else record.profile_name,
                    ]
                )
        except Exception as e:
            logger.error(f"Failed to write trade blotter row: {e}")


class DailySummaryLogger:
    def __init__(self, log_dir: str):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.summary_path = self.log_dir / "daily_summary.csv"
        if not self.summary_path.exists():
            with self.summary_path.open("w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "date",
                        "mode",
                        "starting_equity",
                        "ending_equity",
                        "daily_pnl",
                        "daily_return_pct",
                        "max_daily_loss_hit",
                    ]
                )

    def log(
        self,
        d: date,
        mode: str,
        starting_equity: float,
        ending_equity: float,
        max_daily_loss_hit: bool,
    ) -> None:
        daily_pnl = ending_equity - starting_equity
        daily_return_pct = (daily_pnl / starting_equity * 100.0) if starting_equity != 0 else 0.0
        try:
            with self.summary_path.open("a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        d.isoformat(),
                        mode,
                        f"{starting_equity:.2f}",
                        f"{ending_equity:.2f}",
                        f"{daily_pnl:.2f}",
                        f"{daily_return_pct:.2f}",
                        str(max_daily_loss_hit),
                    ]
                )
        except Exception as e:
            logger.error(f"Failed to write daily summary row: {e}")



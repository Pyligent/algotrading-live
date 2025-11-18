from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Dict

from src.core.config_loader import RiskConfig, SymbolOverride
from src.core.logging_utils import get_logger

logger = get_logger(__name__)


@dataclass
class RiskState:
    equity: float
    realized_pnl_today: float = 0.0
    open_positions: Dict[str, int] = None
    starting_equity_today: float = 0.0
    stops_today: int = 0
    cooldown_bars_by_symbol: Dict[str, int] | None = None
    kill_switch_active: bool = False

    def __post_init__(self):
        if self.open_positions is None:
            self.open_positions = {}
        if self.cooldown_bars_by_symbol is None:
            self.cooldown_bars_by_symbol = {}


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self.state = RiskState(equity=0.0)

    def reset_for_new_day(self, equity: float, open_positions: Dict[str, int]):
        self.state = RiskState(
            equity=equity,
            open_positions=open_positions,
            realized_pnl_today=0.0,
            starting_equity_today=equity,
        )

    def update_realized_pnl(self, realized_pnl: float):
        self.state.realized_pnl_today = realized_pnl

    def add_realized_pnl(self, realized_pnl_delta: float) -> None:
        self.state.realized_pnl_today += realized_pnl_delta
        if realized_pnl_delta < 0:
            # Negative PnL does not necessarily mean a stop; caller should increment stops_today when exit_reason == STOP
            pass

    def set_equity(self, equity: float) -> None:
        self.state.equity = equity

    def allowed_risk_per_trade(self) -> float:
        return self.state.equity * self.cfg.max_risk_per_trade_pct / 100.0

    def allowed_risk_per_trade_for_symbol(self, symbol: str, overrides: Dict[str, SymbolOverride] | None) -> float:
        pct = self.cfg.max_risk_per_trade_pct
        if overrides and symbol in overrides and overrides[symbol].risk and overrides[symbol].risk.max_risk_per_trade_pct is not None:
            pct = float(overrides[symbol].risk.max_risk_per_trade_pct)
        return self.state.equity * pct / 100.0

    def is_daily_loss_limit_hit(self) -> bool:
        if self.state.equity <= 0:
            return True
        loss_pct = -self.state.realized_pnl_today / self.state.equity * 100.0
        return loss_pct >= self.cfg.max_daily_loss_pct

    def is_kill_switch_triggered(self) -> bool:
        ks = self.cfg.intraday_kill_switch
        if not ks.enabled:
            return False
        trigger = False
        if ks.max_stops_per_day is not None and self.state.stops_today >= ks.max_stops_per_day:
            trigger = True
        if ks.max_intraday_loss_pct is not None:
            loss_pct = -self.state.realized_pnl_today / (self.state.starting_equity_today or 1.0) * 100.0
            if loss_pct >= ks.max_intraday_loss_pct:
                trigger = True
        # Only log on transition to active to avoid spamming every bar
        if trigger and not self.state.kill_switch_active:
            self.state.kill_switch_active = True
            logger.warning(f"KILL_SWITCH triggered: stops_today={self.state.stops_today}, pnl_today={self.state.realized_pnl_today:.2f}")
        if not trigger and self.state.kill_switch_active:
            self.state.kill_switch_active = False
        return trigger

    def can_open_new_position(self) -> bool:
        if self.is_daily_loss_limit_hit():
            logger.warning("Daily loss limit reached, no new positions allowed.")
            return False
        if self.is_kill_switch_triggered():
            logger.warning("Kill switch active, no new positions allowed.")
            return False
        if len([p for p in self.state.open_positions.values() if p != 0]) >= self.cfg.max_positions:
            logger.warning("Max positions reached, no new positions allowed.")
            return False
        return True

    def is_within_trading_hours(self, ts: datetime) -> bool:
        t = ts.time()
        start_h, start_m = map(int, self.cfg.trade_start_time.split(":"))
        end_h, end_m = map(int, self.cfg.trade_end_time.split(":"))
        return dtime(start_h, start_m) <= t <= dtime(end_h, end_m)

    # Cooldown helpers
    def set_stop_cooldown(self, symbol: str, bars: int) -> None:
        if bars <= 0:
            return
        self.state.cooldown_bars_by_symbol[symbol] = bars

    def decrement_cooldowns(self) -> None:
        keys = list(self.state.cooldown_bars_by_symbol.keys())
        for k in keys:
            remaining = self.state.cooldown_bars_by_symbol.get(k, 0)
            remaining = max(0, remaining - 1)
            self.state.cooldown_bars_by_symbol[k] = remaining
            if remaining == 0:
                self.state.cooldown_bars_by_symbol.pop(k, None)

    def is_symbol_in_cooldown(self, symbol: str) -> bool:
        return self.state.cooldown_bars_by_symbol.get(symbol, 0) > 0

from pathlib import Path
from typing import List

import yaml
from pydantic import BaseModel, ValidationError, model_validator
from typing import Any, Dict


class IBConfig(BaseModel):
    host: str
    port: int
    client_id: int
    account_id: str | None = None
    max_client_id_attempts: int = 10


class UniverseConfig(BaseModel):
    symbols: List[str]
    primary_exchange: str
    currency: str = "USD"


class DataConfig(BaseModel):
    bar_size: str
    history_duration: str
    storage_path: str
    what_to_show: str = "TRADES"


class TrendParams(BaseModel):
    ema_fast: int
    ema_slow: int
    breakout_lookback_bars: int
    min_breakout_pct: float
    atr_lookback: int
    # New optional risk/exit and regime fields; defaults preserve prior behavior
    stop_distance_pct: float | None = None
    take_profit_rr: float | None = None
    take_profit_distance_pct: float | None = None
    regime_atr_lookback: int | None = None
    regime_min_atr_pct: float | None = None
    # Stop mode selection
    stop_mode: str | None = None  # "atr" or "percent"
    stop_atr_multiple: float | None = None


class PullbackParams(BaseModel):
    min_trend_bars: int
    rsi_period: int
    rsi_lower: int
    rsi_upper: int


class VWAPParams(BaseModel):
    lookback_bars_slope: int
    max_extension_pct: float


class EnsembleParams(BaseModel):
    min_votes_long: int


class StrategyParams(BaseModel):
    bar_timeframe: str
    trend: TrendParams
    pullback: PullbackParams
    vwap: VWAPParams
    ensemble: EnsembleParams


class StrategyConfig(BaseModel):
    name: str
    # Allow flexible parameter schemas for different strategies
    parameters: Any


class RiskConfig(BaseModel):
    max_risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_positions: int
    trade_start_time: str
    trade_end_time: str
    emergency_flatten_on_exit: bool = True
    # Intraday kill switch
    class IntradayKillSwitch(BaseModel):
        enabled: bool = False
        max_stops_per_day: int | None = None
        max_intraday_loss_pct: float | None = None
    intraday_kill_switch: IntradayKillSwitch = IntradayKillSwitch()
    # Holding controls
    class HoldingConfig(BaseModel):
        allow_overnight: bool = False
        max_hold_minutes: int | None = None
    holding: HoldingConfig = HoldingConfig()


class BacktestConfig(BaseModel):
    initial_capital: float
    slippage_bps: float
    commission_per_share: float
    start_date: str
    end_date: str


class LiveConfig(BaseModel):
    paper_trading: bool = True
    polling_interval_seconds: int = 30
    log_dir: str = "./logs"
    dry_run: bool = False


class MLConfig(BaseModel):
    enabled: bool = False
    model_path: str = "./models/spy_meta.pkl"
    horizon_bars: int = 3
    target_move_pct: float = 0.1
    min_probability_long: float = 0.55
    log_only: bool = False
    # Explicit chronological windows for ML training and validation
    train_start_date: str | None = None
    train_end_date: str | None = None
    val_start_date: str | None = None
    val_end_date: str | None = None
    # Optional: write ML_FILTER decisions to CSV (for inspection)
    sample_log_path: str | None = None

    @model_validator(mode="after")
    def _validate_dates_if_enabled(self) -> "MLConfig":
        if self.enabled:
            missing = [
                name
                for name, val in [
                    ("train_start_date", self.train_start_date),
                    ("train_end_date", self.train_end_date),
                    ("val_start_date", self.val_start_date),
                    ("val_end_date", self.val_end_date),
                ]
                if not val
            ]
            if missing:
                raise ValueError(
                    f"ML enabled but missing required date fields: {', '.join(missing)}"
                )
            try:
                from datetime import datetime

                ts = datetime.fromisoformat(self.train_start_date)  # type: ignore[arg-type]
                te = datetime.fromisoformat(self.train_end_date)  # type: ignore[arg-type]
                vs = datetime.fromisoformat(self.val_start_date)  # type: ignore[arg-type]
                ve = datetime.fromisoformat(self.val_end_date)  # type: ignore[arg-type]
                if not (ts <= te and vs <= ve and te < vs):
                    raise ValueError(
                        "Invalid ML date windows: require train_start<=train_end < val_start<=val_end"
                    )
            except Exception as e:
                raise ValueError(f"Invalid ML date format or ordering: {e}") from e
        return self
    # Silence protected namespace warning for 'model_path'
    model_config = {"protected_namespaces": ()}

class SymbolRiskOverride(BaseModel):
    max_risk_per_trade_pct: float | None = None
    max_daily_loss_pct: float | None = None


class SymbolMLOverride(BaseModel):
    min_probability_long: float | None = None


class SymbolOverride(BaseModel):
    risk: SymbolRiskOverride | None = None
    ml: SymbolMLOverride | None = None
    # Optional trend-specific overrides (e.g., stop ATR multiple)
    class SymbolTrendOverride(BaseModel):
        stop_atr_multiple: float | None = None
        stop_mode: str | None = None
    trend: SymbolTrendOverride | None = None


class AppConfig(BaseModel):
    ibkr: IBConfig
    universe: UniverseConfig
    data: DataConfig
    strategy: StrategyConfig
    risk: RiskConfig
    backtest: BacktestConfig
    live: LiveConfig
    ml: MLConfig = MLConfig()
    # Optional: per-symbol overrides
    symbol_overrides: dict[str, SymbolOverride] = {}
    # Optional profile name/registry
    active_profile: str | None = None
    profiles: dict[str, dict] = {}

    @classmethod
    def model_validate(cls, obj):
        app = super().model_validate(obj)
        # Validate ML date ranges if enabled
        if app.ml.enabled:
            dates = [
                app.ml.train_start_date,
                app.ml.train_end_date,
                app.ml.val_start_date,
                app.ml.val_end_date,
            ]
            if any(d is None for d in dates):
                raise RuntimeError(
                    "ML is enabled but one or more ML date ranges are missing: "
                    "train_start_date, train_end_date, val_start_date, val_end_date"
                )
            from datetime import datetime

            try:
                ts = datetime.fromisoformat(app.ml.train_start_date)  # type: ignore
                te = datetime.fromisoformat(app.ml.train_end_date)  # type: ignore
                vs = datetime.fromisoformat(app.ml.val_start_date)  # type: ignore
                ve = datetime.fromisoformat(app.ml.val_end_date)  # type: ignore
            except Exception as e:
                raise RuntimeError(f"Invalid ML date format: {e}") from e
            if not (ts <= te and vs <= ve and te < vs):
                raise RuntimeError(
                    "Invalid ML date windows: Must satisfy train_start <= train_end < val_start <= val_end"
                )
        return app


def load_config(path: Path) -> AppConfig:
    with path.open("r") as f:
        raw = yaml.safe_load(f)
    try:
        return AppConfig(**raw)
    except ValidationError as e:
        raise RuntimeError(f"Invalid config file at {path}:\n{e}") from e

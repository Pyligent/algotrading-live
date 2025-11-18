# Strategy Description

This repo implements an intraday, long-only Trend+ML system on 5‑minute bars for US index ETFs (SPY, QQQ, IWM). It also includes an experimental VWAP mean‑reversion module (not recommended for live).

## Core Ensemble (Trend)

Signals are aggregated from three components. Each outputs {0,1}; the sum is the score.

- Trend/Breakout
  - `ema_fast > ema_slow` and `ema_fast` rising
  - Breakout above recent high by at least `min_breakout_pct` over `breakout_lookback_bars`
  - `atr_lookback` controls volatility measurement for regime/stop sizing

- Pullback‑in‑trend
  - In an uptrend window (`min_trend_bars` of closes above `ema_fast`)
  - Prior bar between `ema_slow` and `ema_fast`
  - RSI in band `[rsi_lower, rsi_upper]`
  - Current bar back above `ema_fast`

- VWAP slope/extension guard (trend context)
  - Requires price > VWAP and rising VWAP over `lookback_bars_slope`
  - Blocks excessive extensions: `max_extension_pct`

Ensemble threshold:
- `ensemble.min_votes_long`: minimum score to generate a candidate entry (often 1 to keep frequency reasonable).

## ML Meta‑Filter

Purpose: Accept or veto candidate entries based on predictive features (trend, VWAP, RSI, ATR, and current signal bits).

- Training
  - Label = forward max return over `horizon_bars` ≥ `target_move_pct`
  - Chronological split via config: `train_*`, `val_*`
  - Classifier: GradientBoostingClassifier (scikit‑learn)
  - Artifacts saved at `ml.model_path` (joblib with metadata)

- Live/Backtest usage
  - Compute features on each candidate
  - Predict probability `p_up`; accept if `p_up ≥ min_probability_long`
  - Per‑symbol overrides supported (e.g., stricter QQQ threshold)
  - `log_only: true` logs decisions without vetoing entries

## Exits & Risk

- Hard stop / take‑profit:
  - ATR mode (preferred): `stop_mode: "atr"`, `stop_atr_multiple`, `take_profit_rr`
  - Percent fallback: `stop_distance_pct`, `take_profit_distance_pct` (optional)
  - Backtest assumes the stop hits first when both levels cross in the same bar

- Signal exit:
  - If holding and ensemble score goes to 0, exit on that bar’s close with reason `SIGNAL`

- Holding rules:
  - `holding.allow_overnight`: if false, flatten at `risk.trade_end_time` (EOD)
  - `holding.max_hold_minutes`: time‑based exit with reason `MAX_HOLD`

- Intraday kill switch:
  - `intraday_kill_switch.enabled`
  - Triggers if `stops_today ≥ max_stops_per_day` or loss `% ≥ max_intraday_loss_pct`
  - Blocks new entries the rest of the day; logging indicates activation

- Stop cooldown (per symbol):
  - After a STOP exit, disallow new entries for `cooldown_bars`

## Profiles

- `core_trend_ml_strict`: SPY/QQQ only; production‑grade Trend+ML with ATR stops; per‑symbol overrides possible.
- `trend_ml_spy_qqq_iwm_v1`: Adds IWM to the universe; broader Trend+ML portfolio.
- `trend_ml_spy_iwm_satellite_v1`: SPY+IWM satellite profile (STABLE); tuned ATR multiple.
- `vwap_mr_spy_qqq_v1`: Experimental VWAP mean‑reversion (research only).

Each profile may define:
- `universe_override`: symbols/exchange/currency
- `symbol_overrides`: per‑symbol risk, ML thresholds, and strategy tweaks
- Profile‑specific `ml` and `risk` blocks (no sharing with other profiles)

## Live vs Backtest Notes

- Backtest:
  - Bar‑by‑bar processing with hard stop/target precedence
  - Writes equity curve and realized trades per profile label
  - Prints ML acceptance overall and per symbol
  - Auto‑runs trade analysis summary at end

- Live:
  - Polls IBKR for latest bars (5‑min), maintains a rolling intraday DF
  - Applies strategy logic, risk, and hard stops/targets
  - Logs trades/daily summaries to `logs/`
  - Enforces EOD flatten and `MAX_HOLD`
  - Requires IB Gateway/TWS running and reachable

## Key Parameters (quick reference)

- Trend:
  - `ema_fast`, `ema_slow`, `breakout_lookback_bars`, `min_breakout_pct`, `atr_lookback`
  - `stop_mode`, `stop_atr_multiple`, `stop_distance_pct`
  - `take_profit_rr` or `take_profit_distance_pct`
  - `regime_atr_lookback`, `regime_min_atr_pct`
- Pullback:
  - `min_trend_bars`, `rsi_period`, `rsi_lower`, `rsi_upper`
- VWAP:
  - `lookback_bars_slope`, `max_extension_pct`
- Ensemble:
  - `min_votes_long`
- ML:
  - `enabled`, `model_path`, `horizon_bars`, `target_move_pct`, `min_probability_long`, `log_only`, `train_*`, `val_*`
- Risk:
  - `max_risk_per_trade_pct`, `max_daily_loss_pct`, `max_positions`, `trade_start_time`, `trade_end_time`
  - `holding.*`, `intraday_kill_switch.*`, `stop_cooldown.*`



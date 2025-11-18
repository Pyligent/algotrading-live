# Intraday Trend+ML (SPY/QQQ/IWM) – Full Guide

This guide walks you through setup, data download, ML training, running backtests for multiple profiles, analyzing results, combining profiles, and launching live paper trading.

## 0) Prereqs
- TWS or IB Gateway running (paper), API enabled, `Socket port=7497`, Trusted IPs includes `127.0.0.1`.
- Python 3.12+ recommended; create venv and install deps:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
- If `matplotlib` fails on macOS/Python 3.13: use Python 3.12 or `brew install freetype pkg-config && pip install matplotlib`.

## 1) Config key ideas
- Profiles: multiple strategy configurations in `config/config.yaml` under `profiles:`.
- Profile selection: `--profile <name>` at CLI without editing YAML.
- `universe_override`: per-profile symbols/exchange/currency.
- `symbol_overrides`: per-symbol risk, ML threshold, and strategy tweaks.
- ML: `ml.enabled`, `model_path`, `min_probability_long`, train/val date ranges.
- Risk: per-trade %, max daily loss %, kill switch, cooldown, holding rules.

## 2) Download historical data
Use dates from `backtest.start_date/end_date` by default, or override via CLI.

```bash
# Core SPY/QQQ
python -m src.main download --config config/config.yaml --profile core_trend_ml_strict --client-id 123

# SPY/QQQ/IWM
python -m src.main download --config config/config.yaml --profile trend_ml_spy_qqq_iwm_v1 --client-id 123

# SPY+IWM satellite
python -m src.main download --config config/config.yaml --profile trend_ml_spy_iwm_satellite_v1 --client-id 123
```
Notes:
- Downloader fetches in 90‑day chunks with pacing/backoff to avoid IBKR 162 timeouts.
- CSVs saved to `data/<SYMBOL_5min>.csv` in US/Eastern tz-naive timestamps.

## 3) Train ML meta‑models
Train a model per profile (so features/thresholds are consistent).

```bash
# Core SPY+QQQ
python -m src.ml.train_model --config config/config.yaml --symbols SPY QQQ --model-path ./models/spyqqq_meta.pkl

# SPY+QQQ+IWM
python -m src.ml.train_model --config config/config.yaml --symbols SPY QQQ IWM --model-path ./models/spyqqqiwm_meta.pkl

# SPY+IWM satellite
python -m src.ml.train_model --config config/config.yaml --symbols SPY IWM --model-path ./models/spy_iwm_satellite_meta.pkl
```
Training uses `ml.train_*` and `ml.val_*` from the active config. It prints validation metrics and writes `ml_reports/threshold_report.csv`.

## 4) Run backtests
```bash
# Core SPY/QQQ
python -m src.main backtest --config config/config.yaml --profile core_trend_ml_strict

# SPY/QQQ/IWM
python -m src.main backtest --config config/config.yaml --profile trend_ml_spy_qqq_iwm_v1

# SPY+IWM satellite
python -m src.main backtest --config config/config.yaml --profile trend_ml_spy_iwm_satellite_v1
```
Outputs are written to `backtest_output/<config_stem>_<profile>/`.
At the end of each run, you’ll see:
- Final equity, Sharpe, max drawdown, trades, win rate
- ML acceptance (overall) and per‑symbol:
  - `ML candidates: ... | accepted: ...`
  - `ML [SPY] candidates: ... | accepted: ...`
  - `ML [IWM] candidates: ... | accepted: ...`

## 5) Analyze trades
```bash
# Example: analyze core
python -m src.analysis.trade_analyzer --trades-file backtest_output/config_core_trend_ml_strict/realized_trades.csv

# SPY/QQQ/IWM
python -m src.analysis.trade_analyzer --trades-file backtest_output/config_trend_ml_spy_qqq_iwm_v1/realized_trades.csv

# SPY+IWM satellite
python -m src.analysis.trade_analyzer --trades-file backtest_output/config_trend_ml_spy_iwm_satellite_v1/realized_trades.csv
```
The analyzer writes CSVs under `analysis/`:
- Overall, by symbol, by exit reason, and by symbol×exit breakdowns.

## 6) Combine profiles (portfolio)
```bash
# Example: 60% core + 40% SPY+IWM satellite
python -m src.analysis.combine_profiles \
  --core-equity backtest_output/config_core_trend_ml_strict/equity_curve.csv \
  --mr-equity   backtest_output/config_trend_ml_spy_iwm_satellite_v1/equity_curve.csv \
  --w-core 0.6 --w-mr 0.4 \
  --output-dir analysis/portfolio_core_plus_satellite
```
The script computes final equity, annualized Sharpe, max drawdown, and saves the combined equity curve/plots.

## 7) Profiles summary
- `core_trend_ml_strict` (default production): SPY/QQQ, Trend+ML, per‑symbol overrides possible.
- `trend_ml_spy_qqq_iwm_v1`: SPY/QQQ/IWM Trend+ML, broader universe with per‑symbol thresholds.
- `trend_ml_spy_iwm_satellite_v1`: SPY+IWM only, “satellite” engine with its own ML model/thresholds.
- `vwap_mr_spy_qqq_v1`: experimental VWAP MR for SPY/QQQ (research only).

## 8) Live paper trading
```bash
python -m src.main trade-live --config config/config.yaml --profile core_trend_ml_strict
```
Checklist:
- TWS API enabled; port matches `ibkr.port`.
- `live.dry_run: false` to actually place orders; set `true` to simulate.
- System flattens at `risk.trade_end_time` or on shutdown.

## 9) Troubleshooting IBKR
- 326 “client id already in use”: pass `--client-id 123` (any free number); the client will retry IDs automatically.
- 162 timeout/cancelled: the downloader chunks (90‑day windows with backoff); re‑run to continue. Ensure delayed data or market data permissions are enabled in TWS.
- Empty CSVs: verify `universe_override.symbols` and dates; check TWS status and permissions; re‑run with a different client id.

## 10) Useful commands (copy/paste)
```bash
# Download all data for SPY/QQQ/IWM (using profile dates)
python -m src.main download --config config/config.yaml --profile trend_ml_spy_qqq_iwm_v1 --client-id 123

# Train models
python -m src.ml.train_model --config config/config.yaml --symbols SPY QQQ --model-path ./models/spyqqq_meta.pkl
python -m src.ml.train_model --config config/config.yaml --symbols SPY QQQ IWM --model-path ./models/spyqqqiwm_meta.pkl
python -m src.ml.train_model --config config/config.yaml --symbols SPY IWM --model-path ./models/spy_iwm_satellite_meta.pkl

# Backtests
python -m src.main backtest --config config/config.yaml --profile core_trend_ml_strict
python -m src.main backtest --config config/config.yaml --profile trend_ml_spy_qqq_iwm_v1
python -m src.main backtest --config config/config.yaml --profile trend_ml_spy_iwm_satellite_v1

# Analyze
python -m src.analysis.trade_analyzer --trades-file backtest_output/config_core_trend_ml_strict/realized_trades.csv
python -m src.analysis.trade_analyzer --trades-file backtest_output/config_trend_ml_spy_qqq_iwm_v1/realized_trades.csv
python -m src.analysis.trade_analyzer --trades-file backtest_output/config_trend_ml_spy_iwm_satellite_v1/realized_trades.csv

# Portfolio combine
python -m src.analysis.combine_profiles \
  --core-equity backtest_output/config_core_trend_ml_strict/equity_curve.csv \
  --mr-equity   backtest_output/config_trend_ml_spy_iwm_satellite_v1/equity_curve.csv \
  --w-core 0.6 --w-mr 0.4 --output-dir analysis/portfolio_core_plus_satellite
```



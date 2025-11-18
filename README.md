# IB SPY/QQQ/IWM Ensemble Algo (Phase 1)

This is a minimal local algorithmic trading project for Interactive Brokers paper trading using TWS/IB Gateway. It trades SPY, QQQ, and IWM intraday using an ensemble of three long-only strategies:

1. Trend / Breakout filter
2. Pullback-in-trend timing
3. VWAP / extension filter

The system supports:

- Backtesting on historical data.
- Live paper trading with IBKR via `ib_insync`.
- Risk management: per-trade risk, max daily loss, max positions.

## Setup

### 1. Create and activate a virtualenv

```bash
python3 -m venv .venv
source .venv/bin/activate  # macOS / Linux
# or on Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

Note: On Python 3.13 (macOS), matplotlib wheels may not be available. We made plotting optional:
- If matplotlib isn’t installed, backtests still run and skip the equity plot.
- To ensure plotting works, either:
  - Use Python 3.12 for the venv, or
  - Install system deps then install matplotlib:
    - `brew install freetype pkg-config`
    - `pip install "matplotlib>=3.9"` (a wheel may be available), or build from source.

### 3. Configure IBKR TWS

- Run TWS or IB Gateway using your paper account.
- In TWS: `Configure -> API -> Settings`:
  - Enable ActiveX and Socket Clients (or "Enable API").
  - Set `Socket port` to `7497` (default paper).
  - Allow connections from `127.0.0.1`.
  - Credentials: you log in via TWS/IB Gateway UI. Credentials are NOT stored in this repo.
  - Account selection: optionally set `ibkr.account_id` in `config/config.yaml` (e.g., `DU12345678`) to force orders to that account and validate on connect.
  - Client ID conflicts: if you see “client id is already in use”, either:
    - Bump `ibkr.client_id` in `config/config.yaml`, or
    - Pass `--client-id N` to the command for this run, or
    - Increase `ibkr.max_client_id_attempts` to widen auto-retry range.

### 4. Copy config

```bash
cp config/config.example.yaml config/config.yaml
```

Edit `config/config.yaml` with your preferences (client id, symbols, risk, etc.).

## Historical data download

Use TWS/IB Gateway to pull SPY/QQQ intraday bars and cache them to CSV before backtesting.

```bash
# Uses dates from config.backtest by default
python -m src.main download --config config/config.yaml

# Or override dates explicitly
python -m src.main download --config config/config.yaml --start-date 2024-01-01 --end-date 2024-12-31

# If a client id is in use, you can override it for this run
python -m src.main download --config config/config.yaml --client-id 123
```

- Files are saved to `data/<SYMBOL>_5min.csv` (e.g., `data/SPY_5min.csv`, `data/QQQ_5min.csv`).
- Typical config:
  - `data.bar_size: "5 mins"`, `data.history_duration: "30 D"` (or `"1 Y"`).
  - `universe.primary_exchange: "SMART"`; IB will qualify the contract.
- The connector automatically retries adjacent client IDs if one is in use. You can also:
  - Set `ibkr.max_client_id_attempts` in config, or
  - Pass `--client-id N` on the command.

### Download troubleshooting

- Error 162 “connected from a different IP”: ensure only this machine is logged into TWS/Gateway; disable VPN; add `127.0.0.1` to Trusted IPs; restart TWS.
- “client id is already in use”: either stop the other process, bump `ibkr.client_id`, increase `ibkr.max_client_id_attempts`, or pass `--client-id`.
- No data/hangs: verify you have market data permissions or enable delayed data; try shorter `data.history_duration` (e.g., `"30 D"`).

## Backtest

Example backtest command:

```bash
python -m src.main backtest --config config/config.yaml
```

This will:
- Download historical data (if not cached).
- Run bar-by-bar backtest of the SPY/QQQ ensemble strategy.
- Print summary metrics and save outputs in `./backtest_output/`:
  - Results are saved under a subfolder named after your config file, e.g.:
    - `backtest_output/config/` for `config/config.yaml`
    - `backtest_output/config.trend_v1_loose/` for `config/config.trend_v1_loose.yaml`
  - Inside each subfolder:
    - `equity_curve.csv` (timestamp,equity)
    - `realized_trades.csv` (per-trade entries with PnL)
    - `equity_curve.png` and `drawdown.png` if matplotlib is available

### Profiles and CLI

- You can define multiple profiles under `profiles:` in `config/config.yaml` and select one at runtime:

```bash
# Run core SPY/QQQ Trend+ML (strict)
python -m src.main backtest --config config/config.yaml --profile core_trend_ml_strict

# Run SPY/QQQ/IWM Trend+ML
python -m src.main backtest --config config/config.yaml --profile trend_ml_spy_qqq_iwm_v1

# Run SPY+IWM satellite Trend+ML
python -m src.main backtest --config config/config.yaml --profile trend_ml_spy_iwm_satellite_v1
```

- Output folders are tagged with the active profile, e.g. `backtest_output/config_core_trend_ml_strict/`.

### Universe & Symbol Overrides

- Each profile may include:
  - `universe_override`: per-profile symbols/exchange/currency (e.g., run SPY+IWM only)
  - `symbol_overrides`: per-symbol risk/ML/strategy tweaks (e.g., stricter ML for QQQ)

Example:

```yaml
profiles:
  trend_ml_spy_qqq_iwm_v1:
    universe_override:
      symbols: ["SPY","QQQ","IWM"]
      primary_exchange: "SMART"
      currency: "USD"
    symbol_overrides:
      SPY:
        risk: { max_risk_per_trade_pct: 1.0 }
        ml:   { min_probability_long: 0.62 }
      QQQ:
        risk: { max_risk_per_trade_pct: 0.5 }
        ml:   { min_probability_long: 0.68 }
        trend:{ stop_atr_multiple: 1.2 }
      IWM:
        risk: { max_risk_per_trade_pct: 0.8 }
        ml:   { min_probability_long: 0.58 }
```

### Per-symbol ML logging

- Backtests log overall ML acceptance and per-symbol stats at the end, e.g.:
  - `ML candidates: 650 | accepted: 80 (12.3%)`
  - `ML [SPY] candidates: 220 | accepted: 35 (15.9%)`
  - `ML [QQQ] candidates: 300 | accepted: 25 (8.3%)`
  - `ML [IWM] candidates: 130 | accepted: 20 (15.4%)`

### Strategy presets

- Trend_v1_loose (current baseline):
  - Loosened ensemble: `min_votes_long: 1`
  - Trend breakouts: shorter lookback (e.g., 10–15), smaller breakout % (e.g., 0.05–0.10)
  - Pullback: `min_trend_bars` around 5–7, RSI band widened (e.g., 35–55)
  - Produces higher frequency (~hundreds of trades/year), modest baseline return, and is a reference profile for subsequent enhancements.
  - Keep risk controls unchanged (per-trade %, daily loss, max positions).

### New risk/exit and regime parameters

Under `strategy.parameters.trend` you can now configure:

- Stop/target
  - `stop_distance_pct`: e.g., 0.5 means stop at 0.5% below entry (long)
  - One of:
    - `take_profit_rr`: reward:risk multiple (e.g., 2.0 sets TP at 2× stop distance)
    - `take_profit_distance_pct`: direct percent take profit (e.g., 1.0 = +1%)
  - Defaults are optional; if omitted, behavior matches previous version (no hard stop/target handling; exits via signals or EOD).
- Regime filter (applied to entries only)
  - `regime_atr_lookback`: bars for ATR regime calculation (e.g., 14)
  - `regime_min_atr_pct`: min ATR as % of price to allow new entries (e.g., 0.2)
  - If omitted, entries are not blocked by regime.

Example:

```yaml
strategy:
  name: "spy_qqq_ensemble"  # Trend_v1_loose baseline with stop/target/regime enabled
  parameters:
    bar_timeframe: "5min"
    trend:
      ema_fast: 20
      ema_slow: 50
      breakout_lookback_bars: 12
      min_breakout_pct: 0.08
      atr_lookback: 14
      stop_distance_pct: 0.5
      take_profit_rr: 2.0
      regime_atr_lookback: 14
      regime_min_atr_pct: 0.25
    pullback:
      min_trend_bars: 6
      rsi_period: 14
      rsi_lower: 35
      rsi_upper: 55
    vwap:
      lookback_bars_slope: 10
      max_extension_pct: 0.5
    ensemble:
      min_votes_long: 1
```

Notes:
- Backtest enforces stop/target before signal exits; if both hit in the same bar, the stop is assumed to hit first (conservative).
- Live trading mirrors stop/target checks on completed bars and exits with market orders; end-of-day flatten remains enforced.

## ML meta-filter (optional)

You can add a machine-learning meta-filter that decides whether to accept each candidate long entry.

### Config

```yaml
ml:
  enabled: true
  model_path: "./models/spy_meta.pkl"
  horizon_bars: 3           # label horizon used for training
  target_move_pct: 0.1      # threshold (in %) to define a “good” move for labels
  min_probability_long: 0.55
  log_only: false           # if true, logs predictions but does not veto trades
```

### Train the model

```bash
# Ensure data/SPY_5min.csv exists (use download mode if needed)
# Install new deps if needed (adds scikit-learn/joblib)
pip install -r requirements.txt

# Train on the symbols in your config (defaults to universe.symbols, e.g., SPY and QQQ)
python -m src.ml.train_model --config config/config.yaml

# Or explicitly specify symbols
python -m src.ml.train_model --config config/config.yaml --symbols SPY QQQ

# Model is saved to the ml.model_path in your config (e.g., ./models/spyqqq_meta.pkl)
```

The training script:
- Builds features from 5‑min bars where the strategy would consider entries
- Labels with forward max return over the next H bars vs target_move_pct
- Trains a simple GradientBoostingClassifier and saves it

### Enable and run

Set `ml.enabled: true` in `config.yaml` and run backtests/live as usual. The strategy will:
- Log each candidate: `ML_FILTER symbol=... p_up=... accepted=...`
- Only place trades when `p_up >= min_probability_long` (unless `log_only: true`)
- Backtests will show ML candidate counts and acceptance rate in the summary

### ML Quickstart (end-to-end)

1) Download data (5‑min bars):
```bash
python -m src.main download --config config/config.yaml
```
2) Train meta-model on SPY+QQQ (uses config.universe.symbols by default):
```bash
pip install -r requirements.txt
python -m src.ml.train_model --config config/config.yaml

# For robust time separation, set ranges in config.ml and/or pass explicitly:
python -m src.ml.train_model --config config/config.yaml \
  --train-start 2024-01-01 --train-end 2025-03-31 \
  --val-start 2025-04-01 --val-end 2025-11-13
```
3) Enable the filter in `config/config.yaml`:
```yaml
ml:
  enabled: true
  model_path: "./models/spyqqq_meta.pkl"
  horizon_bars: 3
  target_move_pct: 0.1
  min_probability_long: 0.55
  log_only: false
```
4) Backtest and review ML stats/logs (results in per-config subfolder under `backtest_output/`):
```bash
python -m src.main backtest --config config/config.yaml
```

### Multi-asset ML models

- Train core SPY+QQQ model:
```bash
python -m src.ml.train_model --config config/config.yaml --symbols SPY QQQ --model-path ./models/spyqqq_meta.pkl
```
- Train SPY+QQQ+IWM model:
```bash
python -m src.ml.train_model --config config/config.yaml --symbols SPY QQQ IWM --model-path ./models/spyqqqiwm_meta.pkl
```
- Train SPY+IWM satellite model:
```bash
python -m src.ml.train_model --config config/config.yaml --symbols SPY IWM --model-path ./models/spy_iwm_satellite_meta.pkl
```

### Train/Validation ranges in config

Add to `config.yaml` under `ml` to enforce chronological separation:
```yaml
ml:
  enabled: true
  model_path: "./models/spyqqq_meta.pkl"
  horizon_bars: 3
  target_move_pct: 0.1
  min_probability_long: 0.55
  log_only: false
  train_start_date: "2024-01-01"
  train_end_date: "2025-03-31"
  val_start_date: "2025-04-01"
  val_end_date: "2025-11-13"
  sample_log_path: "./ml_reports/ml_decisions.csv"  # optional: writes ML_FILTER decisions
```
If `ml.enabled: true`, these date fields are validated. The backtest logs the model’s training ranges and warns on overlap with the backtest period.

### Parameter sweep (threshold/risk/take-profit)

Run a simple grid of ML threshold and risk scaling:
```bash
python -m src.experiments.param_sweep --config config/config.yaml \
  --thresholds 0.5 0.55 0.6 0.65 \
  --risks 0.25 0.5 0.75 1.0 \
  --tps 1.5 2.0
```
Results are saved to `experiments/param_sweep_results.csv` and per-run backtest outputs are in `backtest_output/sweep_*` subfolders.
5) (Optional) Live paper trading with ML filter:
```bash
python -m src.main trade-live --config config/config.yaml
```

Note (macOS): if scikit-learn complains about OpenMP, run:
```bash
brew install libomp
pip install -r requirements.txt
```

If you see "No cached data found", run the downloader first (see section above).

## Analysis & Portfolio

### Trade analyzer (overall/per-symbol/per-exit)

```bash
# Analyze a profile’s trades
python -m src.analysis.trade_analyzer --trades-file backtest_output/config_core_trend_ml_strict/realized_trades.csv

# SPY/QQQ/IWM
python -m src.analysis.trade_analyzer --trades-file backtest_output/config_trend_ml_spy_qqq_iwm_v1/realized_trades.csv

# SPY+IWM satellite
python -m src.analysis.trade_analyzer --trades-file backtest_output/config_trend_ml_spy_iwm_satellite_v1/realized_trades.csv
```

Outputs are saved under `analysis/`:
- `trade_summary_overall.csv`
- `trade_summary_by_symbol.csv`
- `trade_summary_by_exit_reason.csv`
- `trade_summary_by_symbol_and_exit.csv`

### Combine profiles into a portfolio

```bash
python -m src.analysis.combine_profiles \
  --core-equity backtest_output/config_core_trend_ml_strict/equity_curve.csv \
  --mr-equity   backtest_output/config_trend_ml_spy_iwm_satellite_v1/equity_curve.csv \
  --w-core 0.6 --w-mr 0.4 \
  --output-dir analysis/portfolio_core_plus_satellite
```

This writes `portfolio_equity_curve.csv` and plots to the specified output directory, and logs final equity, Sharpe, and max drawdown.

## Live paper trading

**Warning:** Live trading sends real orders to your **paper** account. Test in small size.

```bash
python -m src.main trade-live --config config/config.yaml
```

This will:
- Connect to IBKR (TWS / Gateway).
- Poll for new 5-min bars.
- Generate signals using the ensemble.
- Apply risk checks.
- Place market orders for SPY/QQQ.

To stop trading, press `Ctrl+C` in the terminal. The system flattens open positions on shutdown and before the configured `trade_end_time`.

### Live Paper Trading Checklist

- TWS/IB Gateway API settings:
  - Enable API connections: Configure → API → Settings → Enable ActiveX and Socket Clients
  - Trusted IPs: include `127.0.0.1`
  - Socket port: `7497` (paper) or your custom port
  - Read-only API: optional
- Python env:
  - Create venv and `pip install -r requirements.txt`
- Config (`config/config.yaml`):
  - `ibkr.host`, `ibkr.port`, `ibkr.client_id`
  - `universe.symbols` (e.g., `["SPY","QQQ"]`)
  - `risk.*` (per-trade %, max daily loss %, max positions, trading hours)
  - `live.log_dir` (folder for logs and blotter CSV)
  - `live.dry_run` (set `true` to log actions without sending orders)
- Commands:
  - Backtest with blotter: `python -m src.main backtest --config config/config.yaml`
  - Live paper: `python -m src.main trade-live --config config/config.yaml`
  - Live dry run (no orders): set `live.dry_run: true` in `config.yaml`

### Trade logs and daily summaries

- A human-readable trade blotter is written to `<log_dir>/trades.csv`:
  - Columns: `timestamp, mode, symbol, side, quantity, price, realized_pnl, reason`
  - Mode is `backtest` or `live`
- Daily summaries are written to `<log_dir>/daily_summary.csv`:
  - Columns: `date, mode, starting_equity, ending_equity, daily_pnl, daily_return_pct, max_daily_loss_hit`
- Console logs are streamed to terminal and `<log_dir>/live.log` (for live).
- Signals and ensemble scores are logged for each processed bar to aid observability.

## Running on Windows / Cloud (Quick Notes)

- Prefer IB Gateway for unattended sessions.
- Keep the Python process and IB Gateway on the same machine (localhost API: 127.0.0.1:7497).
- Windows: use Task Scheduler to start the live command at logon/startup and restart on failure. Example (PowerShell):
  -Command "cd C:\path\Algotrading; .\.venv\Scripts\Activate.ps1; python -m src.main trade-live --config config\config.yaml --profile trend_ml_spy_iwm_satellite_v1 --client-id 2001"
- Cloud Windows VM works well; ensure firewall restricts RDP and do not expose the IB API port.

## Documentation & License

- User Instructions: see `docs/INSTRUCTIONS.md` (end-to-end setup, data, training, backtests, analysis, portfolio, live).
- Strategy Description: see `docs/STRATEGY.md` (signals, parameters, exits, ML, risk).
- License: see `LICENSE.md` for usage terms (MIT).

## Docker (Quick Start)

We ship a Dockerfile and compose setup.

- Build:
```bash
docker build -t algotrading:latest .
```

- Backtest (one-off):
```bash
docker run --rm -it \
  -v "$PWD/data:/app/data" \
  -v "$PWD/models:/app/models" \
  -v "$PWD/logs:/app/logs" \
  -v "$PWD/backtest_output:/app/backtest_output" \
  -v "$PWD/ml_reports:/app/ml_reports" \
  -v "$PWD/config:/app/config" \
  algotrading:latest backtest --config config/config.yaml --profile core_trend_ml_strict
```

- Live (macOS/Windows): set `config.ibkr.host: "host.docker.internal"`, then:
```bash
docker run --rm -it \
  -v "$PWD/models:/app/models" \
  -v "$PWD/logs:/app/logs" \
  -v "$PWD/config:/app/config" \
  algotrading:latest trade-live --config config/config.yaml --profile trend_ml_spy_iwm_satellite_v1 --client-id 2001
```

- Compose:
```bash
docker compose up --build
```

See `docs/DOCKER.md` for full details (networking to IB Gateway, Linux host mode).

## VWAP MR Note

- The `vwap_mr_spy_qqq_v1` profile is experimental and not recommended for live use right now. Backtests have shown weak or near‑zero edge on SPY/QQQ with current settings. Keep it for research only.

## Notes

- This is a starting point for Phase 1. The optimization module is a stub, ready for future portfolio-level work.
- Everything is designed to be readable and Cursor-friendly so you can refactor and extend.

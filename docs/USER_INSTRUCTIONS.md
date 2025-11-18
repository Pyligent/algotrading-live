# User Instructions (Quick Reference)

This is a concise index. For the full guide, see `docs/INSTRUCTIONS.md`.

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Download Data
```bash
# Core SPY/QQQ
python -m src.main download --config config/config.yaml --profile core_trend_ml_strict --client-id 123
# SPY/QQQ/IWM
python -m src.main download --config config/config.yaml --profile trend_ml_spy_qqq_iwm_v1 --client-id 123
# SPY+IWM satellite
python -m src.main download --config config/config.yaml --profile trend_ml_spy_iwm_satellite_v1 --client-id 123
```

## Train ML Models
```bash
python -m src.ml.train_model --config config/config.yaml --symbols SPY QQQ --model-path ./models/spyqqq_meta.pkl
python -m src.ml.train_model --config config/config.yaml --symbols SPY QQQ IWM --model-path ./models/spyqqqiwm_meta.pkl
python -m src.ml.train_model --config config/config.yaml --symbols SPY IWM --model-path ./models/spy_iwm_satellite_meta.pkl
```

## Backtest
```bash
python -m src.main backtest --config config/config.yaml --profile core_trend_ml_strict
python -m src.main backtest --config config/config.yaml --profile trend_ml_spy_qqq_iwm_v1
python -m src.main backtest --config config/config.yaml --profile trend_ml_spy_iwm_satellite_v1
```
After each backtest, an analysis summary is printed automatically and CSVs are saved to `analysis/`.

## Analyze Trades (standalone)
```bash
python -m src.analysis.trade_analyzer --trades-file backtest_output/config_core_trend_ml_strict/realized_trades.csv
```

## Combine Portfolios
```bash
python -m src.portfolio.portfolio_backtest \
  --profiles core_trend_ml_strict trend_ml_spy_iwm_satellite_v1 \
  --weights 0.6 0.4 \
  --config config/config.yaml
```

## Live Paper Trading
```bash
# Dry-run first (set live.dry_run: true in config)
python -m src.main trade-live --config config/config.yaml --profile trend_ml_spy_iwm_satellite_v1 --client-id 2001

# Then live orders (set live.dry_run: false)
python -m src.main trade-live --config config/config.yaml --profile trend_ml_spy_iwm_satellite_v1 --client-id 2001
```

## Notes
- Use unique `--client-id` values if IB Gateway reports “client id already in use”.
- Live loop uses `180 D` lookback to fetch 5‑min bars.
- Regime ATR warmup requires ~lookback bars; you’ll see “insufficient bars” until then.



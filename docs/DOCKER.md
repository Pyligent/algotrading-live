# Docker Usage

This project ships a Dockerfile and compose setup to run backtests (and live if you point to an IB Gateway accessible from the container).

Important: For live, the container must reach IB Gateway’s API port. On macOS/Windows, use `host.docker.internal`. On Linux, use `network_mode: host` or set `config.ibkr.host` to your host IP.

## Build the image
```bash
docker build -t algotrading:latest .
```

## Backtest (one-off)
```bash
# Core SPY/QQQ
docker run --rm -it \
  -v "$PWD/data:/app/data" \
  -v "$PWD/models:/app/models" \
  -v "$PWD/logs:/app/logs" \
  -v "$PWD/backtest_output:/app/backtest_output" \
  -v "$PWD/ml_reports:/app/ml_reports" \
  -v "$PWD/config:/app/config" \
  algotrading:latest backtest --config config/config.yaml --profile core_trend_ml_strict
```

## Live (paper)
1) Ensure IB Gateway is running on the host, API enabled on port 7497.
2) In `config/config.yaml`, set:
   - `ibkr.host: "host.docker.internal"` (macOS/Windows)
   - For Linux, consider `network_mode: host` in compose and leave host as `127.0.0.1`.
3) Run:
```bash
docker run --rm -it \
  -v "$PWD/data:/app/data" \
  -v "$PWD/models:/app/models" \
  -v "$PWD/logs:/app/logs" \
  -v "$PWD/config:/app/config" \
  algotrading:latest trade-live --config config/config.yaml --profile trend_ml_spy_iwm_satellite_v1 --client-id 2001
```

## docker-compose
Edit `docker-compose.yml` command to your desired mode/profile. Then:
```bash
docker compose up --build
```
Logs and outputs are mounted back to the repo (data, models, logs, backtest_output).

## Notes
- To avoid matplotlib build issues in container, plots use Agg backend; they save to files as usual.
- If you see IB “client id already in use”, re-run with a different `--client-id`.
- Ensure your models exist in `./models` before live; mount that folder.



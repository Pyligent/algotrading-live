FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg

# Optional system libs for plotting; safe if not used
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git \
    libfreetype6 pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (better caching)
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy source
COPY src /app/src
COPY config /app/config
COPY README.md /app/README.md
COPY docs /app/docs

# Create mount points for persistent artifacts
RUN mkdir -p /app/data /app/models /app/logs /app/backtest_output /app/ml_reports
VOLUME ["/app/data", "/app/models", "/app/logs", "/app/backtest_output", "/app/ml_reports"]

# Default entrypoint; you pass mode/args (e.g., "backtest --config config/config.yaml")
ENTRYPOINT ["python", "-m", "src.main"]



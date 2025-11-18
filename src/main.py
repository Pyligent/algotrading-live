import argparse
from pathlib import Path

from src.core.config_loader import load_config
from src.core.logging_utils import get_logger
from src.backtest.backtest_engine import run_backtest
from src.live.live_trader import run_live_trading
from src.data.historical_downloader import download_historical_data
from src.core.config_loader import StrategyConfig, RiskConfig, MLConfig, SymbolOverride, UniverseConfig

logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="SPY/QQQ Ensemble Trading System")
    parser.add_argument("mode", choices=["backtest", "trade-live", "download"], help="Run mode")
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to YAML config file, e.g. config/config.yaml",
    )
    parser.add_argument("--start-date", type=str, help="Override start date (YYYY-MM-DD) for download")
    parser.add_argument("--end-date", type=str, help="Override end date (YYYY-MM-DD) for download")
    parser.add_argument("--client-id", type=int, help="Override IB client id for this run")
    parser.add_argument("--profile", type=str, help="Override active_profile for this run")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_config(config_path)
    if args.client_id is not None:
        config.ibkr.client_id = args.client_id
    if args.profile:
        # Override active profile from CLI without changing YAML
        config.active_profile = args.profile
    # derive a label from the config filename for output directories
    def _derive_label(p: Path) -> str:
        stem = p.stem  # e.g., config or config.trend_v1_loose
        # sanitize: keep alnum, dash, underscore, dot; replace spaces with underscore
        safe = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in stem)
        return safe
    # Apply active profile overrides (strategy/risk/ml/backtest)
    if config.active_profile and config.profiles and config.active_profile in config.profiles:
        profile = config.profiles[config.active_profile]
        if "strategy" in profile:
            config.strategy = StrategyConfig(**profile["strategy"])
        if "risk" in profile:
            config.risk = RiskConfig(**profile["risk"])
        if "ml" in profile:
            config.ml = MLConfig(**profile["ml"])
        if "backtest" in profile:
            # Only override fields provided
            for k, v in profile["backtest"].items():
                setattr(config.backtest, k, v)
        # Replace global overrides with profile-specific overrides (or clear if none provided)
        if "symbol_overrides" in profile:
            ov = {}
            for sym, body in profile["symbol_overrides"].items():
                ov[sym] = SymbolOverride(**body)
            config.symbol_overrides = ov
        else:
            config.symbol_overrides = {}
        # Universe override
        if "universe_override" in profile:
            uo = profile["universe_override"]
            # Start from current universe and override fields
            uni = config.universe
            symbols = uo.get("symbols", uni.symbols)
            primary = uo.get("primary_exchange", uni.primary_exchange)
            currency = uo.get("currency", uni.currency)
            config.universe = UniverseConfig(symbols=symbols, primary_exchange=primary, currency=currency)
        logger.info(f"Active profile: {config.active_profile}")

    if args.mode == "backtest":
        logger.info("Starting backtest...")
        label = _derive_label(config_path)
        if config.active_profile:
            label = f"{label}_{config.active_profile}"
        run_backtest(config, label=label)
    elif args.mode == "trade-live":
        logger.info("Starting live trading...")
        run_live_trading(config)
    elif args.mode == "download":
        logger.info("Starting historical data download...")
        from src.broker.ib_client import IBClient
        ib_client = IBClient(config.ibkr, config.universe)
        ib_client.connect()
        try:
            # Keep API requests responsive; seconds
            ib_client.ib.setTimeout(30)
        except Exception:
            pass
        start = args.start_date or config.backtest.start_date
        end = args.end_date or config.backtest.end_date
        download_historical_data(
            ib=ib_client.ib,
            universe=config.universe,
            data_cfg=config.data,
            start_date=start,
            end_date=end,
        )
        ib_client.disconnect()
        logger.info("Historical data download completed.")
    else:
        raise ValueError(f"Unknown mode: {args.mode}")


if __name__ == "__main__":
    main()

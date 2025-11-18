import argparse
from copy import deepcopy
from pathlib import Path
from typing import List

import pandas as pd

from src.core.config_loader import load_config, AppConfig
from src.backtest.backtest_engine import run_backtest


def main():
    parser = argparse.ArgumentParser(description="Parameter sweep for ML threshold and risk")
    parser.add_argument("--config", required=True, help="Path to config YAML")
    parser.add_argument(
        "--thresholds",
        nargs="*",
        type=float,
        default=[0.5, 0.55, 0.6, 0.65],
        help="List of ml.min_probability_long values",
    )
    parser.add_argument(
        "--risks",
        nargs="*",
        type=float,
        default=[0.25, 0.5, 0.75, 1.0],
        help="List of risk.max_risk_per_trade_pct values",
    )
    parser.add_argument(
        "--tps",
        nargs="*",
        type=float,
        default=[],
        help="Optional list of trend.take_profit_rr values",
    )
    args = parser.parse_args()

    base_cfg = load_config(Path(args.config))
    combos = []
    if args.tps:
        for th in args.thresholds:
            for rp in args.risks:
                for tp in args.tps:
                    combos.append((th, rp, tp))
    else:
        for th in args.thresholds:
            for rp in args.risks:
                combos.append((th, rp, None))

    rows = []
    for (th, rp, tp) in combos:
        cfg: AppConfig = deepcopy(base_cfg)
        cfg.ml.min_probability_long = float(th)
        cfg.risk.max_risk_per_trade_pct = float(rp)
        if tp is not None:
            cfg.strategy.parameters.trend.take_profit_rr = float(tp)
        label = f"sweep_th{th}_risk{rp}" + (f"_tp{tp}" if tp is not None else "")
        print(f"Running backtest for {label} ...")
        run_backtest(cfg, label=label)
        # Best-effort parse of summary from backtest_output; alternatively, metrics could be returned
        # Here we only record the parameters; user can inspect subfolders for detailed metrics
        rows.append({"threshold": th, "risk_pct": rp, "tp_rr": tp, "label": label})

    df = pd.DataFrame(rows)
    out_dir = Path("experiments")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "param_sweep_results.csv"
    df.to_csv(out_path, index=False)
    print(f"Saved parameter combinations to {out_path}")


if __name__ == "__main__":
    main()



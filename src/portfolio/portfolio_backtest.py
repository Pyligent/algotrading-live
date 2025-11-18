import argparse
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd

from src.core.config_loader import load_config
from src.core.logging_utils import get_logger

logger = get_logger(__name__)


def _label_from_config_and_profile(config_path: Path, profile: str) -> str:
    stem = config_path.stem  # e.g., "config"
    safe = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in stem)
    return f"{safe}_{profile}"


def _load_equity_or_reconstruct(label_dir: Path, initial_capital: float) -> pd.Series:
    eq_path = label_dir / "equity_curve.csv"
    if eq_path.exists():
        df = pd.read_csv(eq_path, index_col="timestamp", parse_dates=True)
        s = df["equity"].astype(float)
        s.name = "equity"
        return s
    # Fallback: reconstruct daily equity from realized trades
    trades_path = label_dir / "realized_trades.csv"
    if not trades_path.exists():
        raise FileNotFoundError(f"Neither equity_curve.csv nor realized_trades.csv found in {label_dir}")
    trades = pd.read_csv(trades_path)
    if "exit_time" not in trades.columns or "pnl" not in trades.columns:
        raise RuntimeError(f"Missing required columns in {trades_path} (need exit_time, pnl)")
    trades["exit_time"] = pd.to_datetime(trades["exit_time"])
    trades["date"] = trades["exit_time"].dt.floor("D")
    daily_pnl = trades.groupby("date")["pnl"].sum().sort_index()
    equity = daily_pnl.cumsum() + float(initial_capital)
    equity.name = "equity"
    return equity


def combine_profiles(
    config_path: Path, profiles: List[str], weights: List[float], output_dir: Path
) -> Tuple[pd.Series, pd.DataFrame]:
    cfg = load_config(config_path)
    initial_capital = float(cfg.backtest.initial_capital)
    if len(profiles) != len(weights):
        raise ValueError("profiles and weights must have the same length")
    if len(profiles) == 0:
        raise ValueError("at least one profile is required")
    # Normalize weights to sum to 1.0
    w = np.array(weights, dtype=float)
    if w.sum() == 0:
        raise ValueError("weights sum to zero")
    w = w / w.sum()
    # Load series
    label_dirs = [Path("backtest_output") / _label_from_config_and_profile(config_path, p) for p in profiles]
    series_list = []
    for p, d in zip(profiles, label_dirs):
        s = _load_equity_or_reconstruct(d, initial_capital)
        series_list.append(s)
        logger.info(f"Loaded equity for profile {p} from {d}")
    # Align and combine as weighted returns
    idx = series_list[0].index
    for s in series_list[1:]:
        idx = idx.union(s.index)
    idx = idx.sort_values()
    rets = []
    for s in series_list:
        s_aligned = s.reindex(idx).ffill()
        base = s_aligned.iloc[0]
        r = s_aligned / base  # return multiple relative to its start
        rets.append(r)
    # Combined portfolio equity
    combined_returns = np.zeros(len(idx))
    for wi, ri in zip(w, rets):
        combined_returns += wi * ri.values
    portfolio_equity = pd.Series(combined_returns * initial_capital, index=idx, name="equity")
    # Stats
    final_equity = float(portfolio_equity.iloc[-1])
    daily_returns = portfolio_equity.pct_change().dropna()
    ann_return = (portfolio_equity.iloc[-1] / portfolio_equity.iloc[0]) ** (252.0 / max(1, len(daily_returns))) - 1.0
    sharpe = np.sqrt(252) * daily_returns.mean() / (daily_returns.std() + 1e-12)
    max_dd = float((portfolio_equity / portfolio_equity.cummax() - 1.0).min())
    # Per-profile contributions (weighted equity end minus weighted initial)
    contributions = []
    for wi, ri, p in zip(w, rets, profiles):
        weighted_equity = wi * ri * initial_capital
        contrib = float(weighted_equity.iloc[-1] - wi * initial_capital)
        contributions.append({"profile": p, "weight": float(wi), "pnl_contribution": contrib})
    contrib_df = pd.DataFrame(contributions)
    # Save outputs
    output_dir.mkdir(parents=True, exist_ok=True)
    portfolio_equity.to_frame().to_csv(output_dir / "equity_curve.csv", index_label="timestamp")
    # Plots
    try:
        import matplotlib.pyplot as plt  # type: ignore

        plt.figure(figsize=(12, 6))
        portfolio_equity.plot(title="Combined Portfolio Equity Curve")
        plt.tight_layout()
        plt.savefig(output_dir / "equity_curve.png")

        plt.figure(figsize=(12, 4))
        dd = portfolio_equity / portfolio_equity.cummax() - 1.0
        dd.plot(title="Portfolio Drawdown")
        plt.tight_layout()
        plt.savefig(output_dir / "drawdown.png")
    except Exception as e:
        logger.warning(f"Skipping plots (matplotlib unavailable): {e}")
    # Summary
    summary = {
        "final_equity": final_equity,
        "annualized_return": float(ann_return),
        "annualized_sharpe": float(sharpe),
        "max_drawdown": max_dd,
    }
    summary_df = pd.DataFrame([summary])
    summary_df.to_csv(output_dir / "summary.csv", index=False)
    contrib_df.to_csv(output_dir / "per_profile_contribution.csv", index=False)
    logger.info(f"Saved portfolio equity and stats to {output_dir}")
    return portfolio_equity, summary_df


def main():
    parser = argparse.ArgumentParser(description="Combine multiple backtest profiles into a single portfolio.")
    parser.add_argument(
        "--profiles",
        nargs="+",
        required=True,
        help="Profile names to combine (must match the active_profile names used in backtests)",
    )
    parser.add_argument(
        "--weights",
        nargs="+",
        type=float,
        required=True,
        help="Portfolio weights for each profile (same length as --profiles). Will be normalized to sum 1.0",
    )
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to YAML config (used to resolve backtest_output labels and initial capital).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Optional output directory. Default: backtest_output/portfolio_<profiles-joined>",
    )
    args = parser.parse_args()

    cfg_path = Path(args.config)
    out_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path("backtest_output") / f"portfolio_{'-'.join(args.profiles)}"
    )
    combine_profiles(cfg_path, args.profiles, args.weights, out_dir)


if __name__ == "__main__":
    main()



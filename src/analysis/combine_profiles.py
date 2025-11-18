import argparse
from pathlib import Path
import pandas as pd
import numpy as np


def load_equity_curve(curve_csv: Path) -> pd.Series:
    df = pd.read_csv(curve_csv, parse_dates=["timestamp"], index_col="timestamp")
    return df["equity"].astype(float)


def to_daily_equity(equity_series: pd.Series) -> pd.Series:
    return equity_series.resample("1D").last().dropna()


def combine_equities(eq_a: pd.Series, eq_b: pd.Series, w_a: float, w_b: float) -> pd.Series:
    # Align on common dates; normalize both to 100000 start
    ix = eq_a.index.union(eq_b.index)
    eq_a = eq_a.reindex(ix).ffill()
    eq_b = eq_b.reindex(ix).ffill()
    a0 = float(eq_a.dropna().iloc[0])
    b0 = float(eq_b.dropna().iloc[0])
    eq_a_n = 100000.0 * eq_a / a0
    eq_b_n = 100000.0 * eq_b / b0
    return w_a * eq_a_n + w_b * eq_b_n


def metrics(equity: pd.Series) -> dict:
    daily_ret = equity.pct_change().dropna()
    sharpe = float(np.sqrt(252) * (daily_ret.mean() / (daily_ret.std() + 1e-9)))
    max_dd = float((equity / equity.cummax() - 1).min())
    return {
        "final_equity": float(equity.iloc[-1]),
        "ann_sharpe": sharpe,
        "max_drawdown": max_dd,
    }


def main():
    p = argparse.ArgumentParser(description="Combine two profile equity curves into a portfolio view")
    p.add_argument("--core-equity", required=True, help="Path to core profile equity_curve.csv")
    p.add_argument("--mr-equity", required=True, help="Path to MR profile equity_curve.csv")
    p.add_argument("--w-core", type=float, default=0.6)
    p.add_argument("--w-mr", type=float, default=0.4)
    p.add_argument("--out-dir", default="analysis")
    args = p.parse_args()

    eq_core = load_equity_curve(Path(args.core_equity))
    eq_mr = load_equity_curve(Path(args.mr_equity))

    daily_core = to_daily_equity(eq_core)
    daily_mr = to_daily_equity(eq_mr)
    port = combine_equities(daily_core, daily_mr, args.w_core, args.w_mr)

    m_core = metrics(daily_core)
    m_mr = metrics(daily_mr)
    m_port = metrics(port)

    print("Core metrics:", m_core)
    print("MR metrics:", m_mr)
    print("Portfolio metrics:", m_port)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)
    port.to_frame(name="equity").to_csv(out_dir / "portfolio_equity.csv", index_label="date")
    pd.DataFrame([m_core]).to_csv(out_dir / "core_metrics.csv", index=False)
    pd.DataFrame([m_mr]).to_csv(out_dir / "mr_metrics.csv", index=False)
    pd.DataFrame([m_port]).to_csv(out_dir / "portfolio_metrics.csv", index=False)
    print(f"Saved combined portfolio outputs to {out_dir}")


if __name__ == "__main__":
    main()



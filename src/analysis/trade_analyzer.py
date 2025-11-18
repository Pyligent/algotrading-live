import argparse
from pathlib import Path
import pandas as pd


def summarize(trades_path: Path) -> None:
    df = pd.read_csv(trades_path)
    # Normalize columns for both backtest realized_trades.csv and live blotter trades.csv
    # Map pnl -> realized_pnl if needed
    if "realized_pnl" not in df.columns and "pnl" in df.columns:
        df["realized_pnl"] = df["pnl"]
    # Map reason -> exit_reason if needed
    if "exit_reason" not in df.columns and "reason" in df.columns:
        df["exit_reason"] = df["reason"]
    # Validate presence
    for c in ["symbol", "realized_pnl", "exit_reason"]:
        if c not in df.columns:
            raise RuntimeError(f"Missing required column '{c}' in {trades_path}")
    df["realized_pnl"] = pd.to_numeric(df["realized_pnl"], errors="coerce").fillna(0.0)
    df["win"] = (df["realized_pnl"] > 0).astype(int)
    out_dir = Path("analysis")
    out_dir.mkdir(exist_ok=True)

    # Overall
    overall = {
        "trades": len(df),
        "total_pnl": df["realized_pnl"].sum(),
        "avg_pnl": df["realized_pnl"].mean() if len(df) > 0 else 0.0,
        "win_rate": df["win"].mean() * 100.0 if len(df) > 0 else 0.0,
        "max_win": df["realized_pnl"].max() if len(df) > 0 else 0.0,
        "max_loss": df["realized_pnl"].min() if len(df) > 0 else 0.0,
    }
    overall_df = pd.DataFrame([overall])
    overall_df.to_csv(out_dir / "trade_summary_overall.csv", index=False)

    # By symbol
    by_symbol = (
        df.groupby("symbol")
        .agg(
            trades=("realized_pnl", "count"),
            total_pnl=("realized_pnl", "sum"),
            avg_pnl=("realized_pnl", "mean"),
            win_rate=("win", "mean"),
            max_win=("realized_pnl", "max"),
            max_loss=("realized_pnl", "min"),
        )
        .reset_index()
    )
    by_symbol["win_rate"] = by_symbol["win_rate"] * 100.0
    by_symbol.to_csv(out_dir / "trade_summary_by_symbol.csv", index=False)

    # By exit reason
    by_exit = (
        df.groupby("exit_reason")
        .agg(
            trades=("realized_pnl", "count"),
            total_pnl=("realized_pnl", "sum"),
            avg_pnl=("realized_pnl", "mean"),
            win_rate=("win", "mean"),
        )
        .reset_index()
    )
    by_exit["win_rate"] = by_exit["win_rate"] * 100.0
    by_exit.to_csv(out_dir / "trade_summary_by_exit_reason.csv", index=False)

    # Symbol x exit
    by_sym_exit = (
        df.groupby(["symbol", "exit_reason"])
        .agg(
            trades=("realized_pnl", "count"),
            total_pnl=("realized_pnl", "sum"),
            avg_pnl=("realized_pnl", "mean"),
            win_rate=("win", "mean"),
        )
        .reset_index()
    )
    by_sym_exit["win_rate"] = by_sym_exit["win_rate"] * 100.0
    by_sym_exit.to_csv(out_dir / "trade_summary_by_symbol_and_exit.csv", index=False)

    # Print clean text summary
    print("=== Overall ===")
    print(overall_df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    print("\n=== By Symbol ===")
    print(by_symbol.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    print("\n=== By Exit Reason ===")
    print(by_exit.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    print("\n=== By Symbol x Exit ===")
    print(by_sym_exit.to_string(index=False, float_format=lambda x: f"{x:.2f}"))


def main():
    parser = argparse.ArgumentParser(description="Trade analyzer for per-symbol and per-exit stats")
    parser.add_argument("--trades-file", required=True, help="Path to trades CSV (e.g., backtest_output/.../realized_trades.csv or logs/trades.csv)")
    args = parser.parse_args()
    summarize(Path(args.trades_file))


if __name__ == "__main__":
    main()



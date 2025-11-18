import pandas as pd
import numpy as np


def compute_basic_metrics(equity_series: pd.Series) -> dict:
    daily_returns = equity_series.pct_change().dropna()
    sharpe = np.sqrt(252) * daily_returns.mean() / (daily_returns.std() + 1e-9)
    max_dd = (equity_series / equity_series.cummax() - 1).min()
    return {
        "final_equity": float(equity_series.iloc[-1]),
        "sharpe": float(sharpe),
        "max_drawdown": float(max_dd),
    }

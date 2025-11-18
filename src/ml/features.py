import math
from typing import Dict

import numpy as np
import pandas as pd


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = (delta.clip(lower=0)).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


def _vwap(df: pd.DataFrame) -> pd.Series:
    price = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"]
    cum_pv = (price * vol).cumsum()
    cum_vol = vol.cumsum().replace(0, np.nan)
    return cum_pv / cum_vol


def compute_features(
    df: pd.DataFrame,
    ema_fast: int,
    ema_slow: int,
    vwap_lookback: int,
    rsi_period: int,
    atr_lookback: int,
    signals: Dict[str, int] | None = None,
) -> Dict[str, float]:
    """
    Compute a compact feature vector from the latest row of df.
    Expects df with columns: open/high/low/close/volume and a DatetimeIndex.
    """
    if len(df) < max(ema_fast, ema_slow, vwap_lookback, rsi_period, atr_lookback) + 5:
        return {}
    close = df["close"]
    returns = close.pct_change()
    feat: Dict[str, float] = {}
    # Returns
    for w in (1, 3, 5, 10):
        if len(returns) > w:
            feat[f"ret_{w}"] = float(returns.iloc[-w:].sum())
    # Volatility
    for w in (5, 10, 20):
        if len(returns) > w:
            feat[f"vol_{w}"] = float(returns.iloc[-w:].std(ddof=0))
    # Trend EMAs and slopes
    ema_f = _ema(close, ema_fast)
    ema_s = _ema(close, ema_slow)
    feat["ema_fast_slope"] = float(ema_f.iloc[-1] - ema_f.iloc[-3])
    feat["ema_slow_slope"] = float(ema_s.iloc[-1] - ema_s.iloc[-3])
    feat["dist_ema_fast_pct"] = float((close.iloc[-1] - ema_f.iloc[-1]) / ema_f.iloc[-1] * 100.0)
    feat["dist_ema_slow_pct"] = float((close.iloc[-1] - ema_s.iloc[-1]) / ema_s.iloc[-1] * 100.0)
    # VWAP and slope
    vwap_series = _vwap(df)
    feat["dist_vwap_pct"] = float((close.iloc[-1] - vwap_series.iloc[-1]) / vwap_series.iloc[-1] * 100.0)
    feat["vwap_slope"] = float(vwap_series.iloc[-1] - vwap_series.iloc[-vwap_lookback])
    # RSI / ATR
    rsi_series = _rsi(close, rsi_period)
    feat["rsi"] = float(rsi_series.iloc[-1])
    atr = (df["high"] - df["low"]).rolling(atr_lookback).mean()
    feat["atr"] = float(atr.iloc[-1])
    feat["atr_pct"] = float((atr.iloc[-1] / close.iloc[-1]) * 100.0)
    # Time of day features
    ts = df.index[-1]
    minutes = ts.hour * 60 + ts.minute
    feat["tod_sin"] = math.sin(2 * math.pi * minutes / (24 * 60))
    feat["tod_cos"] = math.cos(2 * math.pi * minutes / (24 * 60))
    # Signals
    if signals:
        trend_sig = int(signals.get("trend", 0))
        pull_sig = int(signals.get("pullback", 0))
        vwap_sig = int(signals.get("vwap", 0))
        feat["sig_trend"] = float(trend_sig)
        feat["sig_pullback"] = float(pull_sig)
        feat["sig_vwap"] = float(vwap_sig)
        feat["sig_score"] = float(trend_sig + pull_sig + vwap_sig)
    return feat



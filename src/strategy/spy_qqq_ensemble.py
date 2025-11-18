from typing import Any, Dict, List, Deque
from collections import deque

import numpy as np
import pandas as pd

from src.data.bar_generator import Bar
from src.strategy.base_strategy import BaseStrategy
from src.core.logging_utils import get_logger
from src.ml.model_store import ModelWrapper
from src.ml.features import compute_features

logger = get_logger(__name__)


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = (delta.clip(lower=0)).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


class SpyQqqEnsembleStrategy(BaseStrategy):
    """Ensemble of 3 long-only components for SPY/QQQ."""

    def __init__(self, params: Dict[str, Any]):
        super().__init__(params)
        self.history: Dict[str, Deque[Bar]] = {}
        self.max_history_bars = 300
        self.position_state: Dict[str, str] = {}  # symbol -> "FLAT" or "LONG"
        self.ml_wrapper: ModelWrapper | None = None
        self.ml_cfg: Dict[str, Any] | None = None
        self.ml_stats: Dict[str, int] = {"candidates": 0, "accepted": 0}
        self.ml_stats_by_symbol: Dict[str, Dict[str, int]] = {}

    def on_start(self, context: Dict[str, Any]) -> None:
        self.history.clear()
        self.position_state = {s: "FLAT" for s in context["universe"].symbols}
        # Load ML model if enabled
        self.ml_cfg = context.get("ml").model_dump() if context.get("ml") is not None else None
        if self.ml_cfg and self.ml_cfg.get("enabled"):
            self.ml_wrapper = ModelWrapper(self.ml_cfg["model_path"])
            ok = self.ml_wrapper.load()
            if not ok:
                logger.warning("ML enabled but model failed to load; proceeding without filter")
                self.ml_wrapper = None
        # Initialize per-symbol ML stats
        self.ml_stats_by_symbol = {s: {"candidates": 0, "accepted": 0} for s in context["universe"].symbols}

    def _bars_to_df(self, bars: Deque[Bar]) -> pd.DataFrame:
        data = [
            {
                "timestamp": b.timestamp,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ]
        df = pd.DataFrame(data)
        df.set_index("timestamp", inplace=True)
        return df

    def _compute_vwap(self, df: pd.DataFrame) -> pd.Series:
        price = (df["high"] + df["low"] + df["close"]) / 3.0
        vol = df["volume"]
        cum_pv = (price * vol).cumsum()
        cum_vol = vol.cumsum().replace(0, np.nan)
        return cum_pv / cum_vol

    def _trend_signal(self, df: pd.DataFrame) -> int:
        p = self.params["trend"]
        close = df["close"]
        if len(close) < max(p["ema_fast"], p["ema_slow"], p["breakout_lookback_bars"]) + 5:
            return 0

        ema_fast = ema(close, p["ema_fast"])
        ema_slow = ema(close, p["ema_slow"])
        atr = (df["high"] - df["low"]).rolling(p["atr_lookback"]).mean()

        last = df.index[-1]
        if not (
            close[last] > ema_fast[last] > ema_slow[last]
            and ema_fast[last] > ema_fast.iloc[-4]
            and atr.iloc[-1] > atr.median()
        ):
            return 0

        window = close.iloc[-(p["breakout_lookback_bars"] + 1) : -1]
        breakout_level = window.max()
        breakout_size_pct = (close[last] - breakout_level) / breakout_level * 100.0

        if breakout_size_pct >= p["min_breakout_pct"]:
            return 1
        return 0

    def _pullback_signal(self, df: pd.DataFrame) -> int:
        p = self.params["pullback"]
        trend_p = self.params["trend"]
        close = df["close"]
        if len(close) < max(trend_p["ema_fast"], trend_p["ema_slow"], p["min_trend_bars"]) + 5:
            return 0

        ema_fast = ema(close, trend_p["ema_fast"])
        ema_slow = ema(close, trend_p["ema_slow"])
        rsi_series = rsi(close, p["rsi_period"])

        last = df.index[-1]
        trend_mask = close > ema_fast
        if trend_mask.iloc[-p["min_trend_bars"] :].sum() < p["min_trend_bars"]:
            return 0

        prev = df.index[-2]
        if not (ema_slow[prev] < close[prev] < ema_fast[prev]):
            return 0
        if not (p["rsi_lower"] <= rsi_series[prev] <= p["rsi_upper"]):
            return 0

        if close[last] > ema_fast[last]:
            return 1
        return 0

    def _vwap_signal(self, df: pd.DataFrame) -> int:
        p = self.params["vwap"]
        if len(df) < p["lookback_bars_slope"] + 2:
            return 0

        vwap_series = self._compute_vwap(df)
        last = df.index[-1]
        prev = df.index[-p["lookback_bars_slope"]]

        price = df["close"][last]
        v = vwap_series[last]
        if pd.isna(v):
            return 0

        if not (price > v and v > vwap_series[prev]):
            return 0

        ext_pct = (price - v) / v * 100.0
        if ext_pct > p["max_extension_pct"]:
            return 0

        return 1

    def on_bar(self, bar: Bar, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        symbol = bar.symbol
        if symbol not in self.history:
            self.history[symbol] = deque(maxlen=self.max_history_bars)
        self.history[symbol].append(bar)

        df = self._bars_to_df(self.history[symbol])
        actions: List[Dict[str, Any]] = []

        trend_sig = self._trend_signal(df)
        pullback_sig = self._pullback_signal(df)
        vwap_sig = self._vwap_signal(df)

        # Regime filter: only apply to entries; compute ATR% if configured
        regime_ok = True
        tr_params = self.params["trend"]
        if tr_params.get("regime_atr_lookback") and tr_params.get("regime_min_atr_pct") is not None:
            lookback = int(tr_params["regime_atr_lookback"])
            if len(df) >= lookback + 1:
                tr = (df["high"] - df["low"]).rolling(lookback).mean()
                atr_pct = float((tr.iloc[-1] / df["close"].iloc[-1]) * 100.0)
                regime_ok = atr_pct >= float(tr_params["regime_min_atr_pct"])
                if not regime_ok:
                    logger.info(
                        f"[REGIME] {symbol} {bar.timestamp.isoformat()} ATR%={atr_pct:.2f} "
                        f"< threshold {float(tr_params['regime_min_atr_pct']):.2f}, skip entries"
                    )
            else:
                regime_ok = False
                logger.info(
                    f"[REGIME] {symbol} insufficient bars for regime check (need {lookback})"
                )

        score = trend_sig + pullback_sig + vwap_sig
        min_votes = self.params["ensemble"]["min_votes_long"]

        position = self.position_state.get(symbol, "FLAT")

        logger.info(
            f"[SIGNAL] {symbol} bar={bar.timestamp.isoformat()} "
            f"trend={trend_sig} pullback={pullback_sig} vwap={vwap_sig} score={score}"
        )

        if position == "FLAT" and score >= min_votes and regime_ok:
            # ML meta-filter (optional)
            accept = True
            p_up = None
            if self.ml_cfg and self.ml_cfg.get("enabled") and self.ml_wrapper is not None:
                self.ml_stats["candidates"] += 1
                self.ml_stats_by_symbol[symbol]["candidates"] += 1
                # Build features
                feat = compute_features(
                    df,
                    ema_fast=self.params["trend"]["ema_fast"],
                    ema_slow=self.params["trend"]["ema_slow"],
                    vwap_lookback=self.params["vwap"]["lookback_bars_slope"],
                    rsi_period=self.params["pullback"]["rsi_period"],
                    atr_lookback=self.params["trend"]["atr_lookback"],
                    signals={"trend": trend_sig, "pullback": pullback_sig, "vwap": vwap_sig},
                )
                if feat:
                    try:
                        p_up = self.ml_wrapper.predict_proba(feat)
                        # Per-symbol override for ML threshold
                        min_prob = float(self.ml_cfg.get("min_probability_long", 0.5))
                        overrides = context.get("symbol_overrides") if context else None
                        if overrides and symbol in overrides and overrides[symbol].ml and overrides[symbol].ml.min_probability_long is not None:
                            min_prob = float(overrides[symbol].ml.min_probability_long)
                        accept = p_up >= min_prob
                    except Exception as e:
                        logger.error(f"ML prediction failed: {e}")
                        accept = True
                log_only = bool(self.ml_cfg.get("log_only", False))
                logger.info(
                    f"ML_FILTER symbol={symbol} ts={bar.timestamp.isoformat()} score={score} "
                    f"p_up={p_up if p_up is not None else 'NA'} accepted={accept} log_only={log_only}"
                )
                # Optional CSV logging of decisions
                sample_path = self.ml_cfg.get("sample_log_path")
                if sample_path:
                    import os, csv
                    os.makedirs(os.path.dirname(sample_path), exist_ok=True)
                    write_header = not os.path.exists(sample_path)
                    with open(sample_path, "a", newline="") as f:
                        w = csv.writer(f)
                        if write_header:
                            w.writerow(["timestamp", "symbol", "score", "p_up", "accepted"])
                        w.writerow([bar.timestamp.isoformat(), symbol, score, p_up if p_up is not None else "", accept])
                if log_only:
                    accept = True
                if accept:
                    self.ml_stats["accepted"] += 1
                    self.ml_stats_by_symbol[symbol]["accepted"] += 1
                else:
                    # Skip entry
                    return actions

            actions.append(
                {
                    "type": "ENTER_LONG",
                    "symbol": symbol,
                    "reason": f"trend={trend_sig},pullback={pullback_sig},vwap={vwap_sig},score={score}",
                }
            )
            self.position_state[symbol] = "LONG"

        if position == "LONG" and score == 0:
            actions.append(
                {
                    "type": "EXIT",
                    "symbol": symbol,
                    "reason": f"trend={trend_sig},pullback={pullback_sig},vwap={vwap_sig},score={score}",
                }
            )
            self.position_state[symbol] = "FLAT"

        return actions

    def on_end(self, context: Dict[str, Any]) -> None:
        pass

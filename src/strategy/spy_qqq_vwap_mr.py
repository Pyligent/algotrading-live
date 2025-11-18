from typing import Any, Dict, List, Deque
from collections import deque

import numpy as np
import pandas as pd

from src.data.bar_generator import Bar
from src.strategy.base_strategy import BaseStrategy
from src.core.logging_utils import get_logger

logger = get_logger(__name__)


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = (delta.clip(lower=0)).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


class SpyQqqVWAPMRStrategy(BaseStrategy):
    """VWAP mean-reversion long-only v1 for SPY/QQQ on 5-min bars."""

    def __init__(self, params: Dict[str, Any]):
        super().__init__(params)
        self.history: Dict[str, Deque[Bar]] = {}
        self.max_history_bars = 300
        self.position_state: Dict[str, str] = {}  # symbol -> "FLAT" or "LONG"

    def on_start(self, context: Dict[str, Any]) -> None:
        self.history.clear()
        self.position_state = {s: "FLAT" for s in context["universe"].symbols}

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

    def _compute_vwap_session(self, df: pd.DataFrame) -> pd.Series:
        # Simple cumulative intraday vwap (assumes reset daily via new session)
        price = (df["high"] + df["low"] + df["close"]) / 3.0
        vol = df["volume"].replace(0, np.nan)
        cum_pv = (price * vol).cumsum()
        cum_vol = vol.cumsum()
        return cum_pv / cum_vol

    def on_bar(self, bar: Bar, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        symbol = bar.symbol
        if symbol not in self.history:
            self.history[symbol] = deque(maxlen=self.max_history_bars)
        self.history[symbol].append(bar)

        df = self._bars_to_df(self.history[symbol])
        actions: List[Dict[str, Any]] = []

        p = self.params["vwap_mr"]
        if len(df) < max(p["rsi_period"], p["atr_lookback"]) + 5:
            logger.info(f"[VWAP_MR] {symbol} skip: not enough history bars for warmup (have={len(df)})")
            return actions

        # Filter to current session by calendar date
        last_date = df.index[-1].date()
        df_sess = df[df.index.date == last_date]
        # Warmup: require enough bars for RSI/ATR and at least the fast EMA span
        if len(df_sess) < max(p["rsi_period"], p["atr_lookback"], p.get("trend_ema_fast", 20)) + 3:
            logger.info(f"[VWAP_MR] {symbol} skip: session warmup insufficient (sess_bars={len(df_sess)})")
            return actions

        # Time window constraints based on current bar time
        t = bar.timestamp.time()
        start_h, start_m = map(int, p["entry_start_time"].split(":"))
        end_h, end_m = map(int, p["entry_end_time"].split(":"))
        if not ((t.hour > start_h or (t.hour == start_h and t.minute >= start_m))
                and (t.hour < end_h or (t.hour == end_h and t.minute <= end_m))):
            logger.info(f"[VWAP_MR] {symbol} skip: outside entry window ({t})")
            return actions

        vwap_sess = self._compute_vwap_session(df_sess)
        close_sess = df_sess["close"]
        vwap_now = float(vwap_sess.iloc[-1])
        close_now = float(close_sess.iloc[-1])
        close_prev = float(close_sess.iloc[-2])
        ext_pct = (close_now - vwap_now) / (vwap_now + 1e-9) * 100.0
        rsi_series = _rsi(close_sess, p["rsi_period"])
        rsi_now = float(rsi_series.iloc[-1])
        # Trend EMAs (session-only)
        ema_fast = close_sess.ewm(span=p.get("trend_ema_fast", 20), adjust=False).mean()
        ema_slow = close_sess.ewm(span=p.get("trend_ema_slow", 50), adjust=False).mean()
        ema_fast_now = float(ema_fast.iloc[-1])
        ema_slow_now = float(ema_slow.iloc[-1])
        in_uptrend = ema_fast_now > ema_slow_now

        position = self.position_state.get(symbol, "FLAT")

        # Debug logging for MR diagnostics
        logger.info(
            f"[VWAP_MR] {symbol} t={bar.timestamp} close={close_now:.2f} vwap={vwap_now:.2f} "
            f"ext_pct={ext_pct:.2f} rsi={rsi_now:.1f} uptrend={in_uptrend} pos={position}"
        )

        # Long-only MR entry v2: uptrend + stretched below VWAP + oversold + bounce
        if position == "FLAT":
            if not in_uptrend:
                logger.info(f"[VWAP_MR] {symbol} block: not in uptrend (ema_fast<=ema_slow)")
            elif not (ext_pct <= -float(p["long_extension_pct"])):
                logger.info(f"[VWAP_MR] {symbol} block: extension {ext_pct:.2f} > -{float(p['long_extension_pct']):.2f}")
            elif not (rsi_now <= float(p["rsi_long_max"])):
                logger.info(f"[VWAP_MR] {symbol} block: rsi {rsi_now:.1f} > {float(p['rsi_long_max']):.1f}")
            elif not (close_now > close_prev):
                logger.info(f"[VWAP_MR] {symbol} block: no bounce (close_now<=close_prev)")
            else:
                actions.append({
                    "type": "ENTER_LONG",
                    "symbol": symbol,
                    "reason": f"MR_LONG_UPTREND ext={ext_pct:.2f} rsi={rsi_now:.1f}"
                })
                self.position_state[symbol] = "LONG"

        # Exit on reversion to VWAP (take profit by signal)
        if position == "LONG":
            if close_now >= vwap_now:
                actions.append({"type": "EXIT", "symbol": symbol, "reason": "MR_REVERT_VWAP"})
                self.position_state[symbol] = "FLAT"

        return actions

    def on_end(self, context: Dict[str, Any]) -> None:
        return



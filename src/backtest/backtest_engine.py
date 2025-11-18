from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, TypedDict

import pandas as pd
import numpy as np

from src.core.config_loader import AppConfig
from src.core.enums import OrderSide
from src.core.logging_utils import get_logger
from src.data.data_store import load_cached_data
from src.data.bar_generator import Bar
from src.strategy.spy_qqq_ensemble import SpyQqqEnsembleStrategy
from src.strategy.spy_qqq_vwap_mr import SpyQqqVWAPMRStrategy
from src.risk.risk_limits import RiskManager
from src.risk.risk_checker import size_order
from src.core.trade_logger import TradeBlotter, DailySummaryLogger, TradeRecord
from src.analysis.trade_analyzer import summarize as analyze_trades

logger = get_logger(__name__)


@dataclass
class Position:
    symbol: str
    qty: int
    entry_price: float
    entry_time: pd.Timestamp
    stop_price: float | None = None
    target_price: float | None = None


class RealizedTrade(TypedDict):
    symbol: str
    qty: int
    entry_price: float
    exit_price: float
    entry_time: str
    exit_time: str
    pnl: float
    exit_reason: str


def run_backtest(config: AppConfig, label: str | None = None):
    data_map = load_cached_data(config.data, config.universe)
    if not data_map:
        raise RuntimeError(
            "No cached data found. First run a downloader or integrate download into backtest."
        )

    # Align all symbols on the same index
    all_indices = sorted(set().union(*(df.index for df in data_map.values())))
    all_index = pd.DatetimeIndex(all_indices)

    for symbol, df in data_map.items():
        data_map[symbol] = df.reindex(all_index).ffill()

    # Build strategy based on profile name
    params_obj = config.strategy.parameters
    params = params_obj.model_dump() if hasattr(params_obj, "model_dump") else params_obj
    if config.strategy.name == "spy_qqq_vwap_mr":
        strategy = SpyQqqVWAPMRStrategy(params=params)
    else:
        strategy = SpyQqqEnsembleStrategy(params=params)
    risk_mgr = RiskManager(config.risk)
    equity = config.backtest.initial_capital
    risk_mgr.reset_for_new_day(equity, open_positions={s: 0 for s in config.universe.symbols})

    blotter = TradeBlotter(config.live.log_dir)
    daily_logger = DailySummaryLogger(config.live.log_dir)

    positions: Dict[str, Position | None] = {s: None for s in config.universe.symbols}
    equity_curve: List[float] = []
    pnl_list: List[float] = []
    realized_trades: List[RealizedTrade] = []

    context: Dict[str, Any] = {
        "universe": config.universe,
        "ml": config.ml,
        "symbol_overrides": config.symbol_overrides,
        "profile_name": config.active_profile or "default",
    }
    strategy.on_start(context)
    # Log model metadata and date overlaps if any
    try:
        if getattr(strategy, "ml_wrapper", None) and strategy.ml_wrapper and strategy.ml_wrapper.metadata:
            md = strategy.ml_wrapper.metadata
            logger.info(
                f"Using ML model trained on {md.get('train_start')}..{md.get('train_end')} "
                f"| validated on {md.get('val_start')}..{md.get('val_end')} "
                f"| symbols={md.get('symbols')}"
            )
            bt_start = pd.to_datetime(config.backtest.start_date)
            bt_end = pd.to_datetime(config.backtest.end_date)
            tr_start = pd.to_datetime(md.get("train_start")) if md.get("train_start") else None
            val_start = pd.to_datetime(md.get("val_start")) if md.get("val_start") else None
            # Warn if backtest overlaps with train period
            if tr_start is not None:
                tr_end = pd.to_datetime(md.get("train_end"))
                if not (bt_end < tr_start or bt_start > tr_end):
                    logger.warning("Backtest period overlaps ML training period — results may be in-sample.")
            # Also warn if overlaps validation (optional)
    except Exception:
        pass

    for ts in all_index:
        equity_curve.append(equity)
        pnl_list.append(0.0)
        # Decrement cooldowns once per bar
        risk_mgr.decrement_cooldowns()

        for symbol in config.universe.symbols:
            row = data_map[symbol].loc[ts]
            if pd.isna(row["close"]):
                continue

            bar = Bar(
                symbol=symbol,
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )

            actions = strategy.on_bar(bar, context)

            # Holding rules: MAX_HOLD and EOD before other exits
            pos = positions[symbol]
            if pos is not None:
                # Max hold minutes
                max_hold = config.risk.holding.max_hold_minutes
                if max_hold is not None:
                    held_min = (ts - pos.entry_time).total_seconds() / 60.0
                    if held_min >= max_hold:
                        exit_price = bar.close
                        trade_pnl = (exit_price - pos.entry_price) * pos.qty
                        equity += trade_pnl
                        risk_mgr.add_realized_pnl(trade_pnl)
                        risk_mgr.set_equity(equity)
                        pnl_list[-1] += trade_pnl
                        realized_trades.append(
                            RealizedTrade(
                                symbol=symbol,
                                qty=pos.qty,
                                entry_price=pos.entry_price,
                                exit_price=exit_price,
                                entry_time=pos.entry_time.to_pydatetime().isoformat(),
                                exit_time=ts.to_pydatetime().isoformat(),
                                pnl=trade_pnl,
                                exit_reason="MAX_HOLD",
                            )
                        )
                        positions[symbol] = None
                        blotter.log(
                            TradeRecord(
                                timestamp=ts.to_pydatetime(),
                                mode="backtest",
                                symbol=symbol,
                                side="SELL",
                                quantity=pos.qty,
                                price=exit_price,
                                realized_pnl=trade_pnl,
                                reason="MAX_HOLD",
                                profile_name=context["profile_name"],
                            )
                        )
                        continue
                # EOD flatten if overnight not allowed
                if not config.risk.holding.allow_overnight:
                    end_h, end_m = map(int, config.risk.trade_end_time.split(":"))
                    if ts.time().hour == end_h and ts.time().minute >= end_m:
                        exit_price = bar.close
                        trade_pnl = (exit_price - pos.entry_price) * pos.qty
                        equity += trade_pnl
                        risk_mgr.add_realized_pnl(trade_pnl)
                        risk_mgr.set_equity(equity)
                        pnl_list[-1] += trade_pnl
                        realized_trades.append(
                            RealizedTrade(
                                symbol=symbol,
                                qty=pos.qty,
                                entry_price=pos.entry_price,
                                exit_price=exit_price,
                                entry_time=pos.entry_time.to_pydatetime().isoformat(),
                                exit_time=ts.to_pydatetime().isoformat(),
                                pnl=trade_pnl,
                                exit_reason="EOD",
                            )
                        )
                        positions[symbol] = None
                        blotter.log(
                            TradeRecord(
                                timestamp=ts.to_pydatetime(),
                                mode="backtest",
                                symbol=symbol,
                                side="SELL",
                                quantity=pos.qty,
                                price=exit_price,
                                realized_pnl=trade_pnl,
                                reason="EOD",
                                profile_name=context["profile_name"],
                            )
                        )
                        continue

            # 1) Hard stop/target checks happen before signal exits
            pos = positions[symbol]
            if pos is not None and (pos.stop_price is not None or pos.target_price is not None):
                stopped = pos.stop_price is not None and bar.low <= float(pos.stop_price)
                targeted = pos.target_price is not None and bar.high >= float(pos.target_price)
                # Rule: assume stop hits first if both occur in same bar (conservative)
                hit = None
                if stopped:
                    hit = "STOP"
                    exit_px = float(pos.stop_price)
                elif targeted:
                    hit = "TARGET"
                    exit_px = float(pos.target_price)
                if hit is not None:
                    trade_pnl = (exit_px - pos.entry_price) * pos.qty
                    equity += trade_pnl
                    risk_mgr.add_realized_pnl(trade_pnl)
                    risk_mgr.set_equity(equity)
                    pnl_list[-1] += trade_pnl
                    realized_trades.append(
                        RealizedTrade(
                            symbol=symbol,
                            qty=pos.qty,
                            entry_price=pos.entry_price,
                            exit_price=exit_px,
                            entry_time=pos.entry_time.to_pydatetime().isoformat(),
                            exit_time=ts.to_pydatetime().isoformat(),
                            pnl=trade_pnl,
                            exit_reason=hit,
                        )
                    )
                    positions[symbol] = None
                    logger.info(f"[BACKTEST] EXIT {symbol} {pos.qty} @ {exit_px:.2f} REASON={hit} PNL={trade_pnl:.2f}")
                    # After hard exit, skip signal processing for this symbol on this bar
                    if hit == "STOP":
                        risk_mgr.state.stops_today += 1
                    blotter.log(
                        TradeRecord(
                            timestamp=ts.to_pydatetime(),
                            mode="backtest",
                            symbol=symbol,
                            side="SELL",
                            quantity=pos.qty if pos else 0,
                            price=exit_px,
                            realized_pnl=trade_pnl,
                            reason=hit,
                            profile_name=context["profile_name"],
                        )
                    )
                    continue

            for a in actions:
                if a["type"] == "EXIT" and positions[symbol] is not None:
                    pos = positions[symbol]
                    exit_price = bar.close
                    trade_pnl = (exit_price - pos.entry_price) * pos.qty
                    equity += trade_pnl
                    risk_mgr.add_realized_pnl(trade_pnl)
                    risk_mgr.set_equity(equity)
                    pnl_list[-1] += trade_pnl
                    realized_trades.append(
                        RealizedTrade(
                            symbol=symbol,
                            qty=pos.qty,
                            entry_price=pos.entry_price,
                            exit_price=exit_price,
                            entry_time=pos.entry_time.to_pydatetime().isoformat(),
                            exit_time=ts.to_pydatetime().isoformat(),
                            pnl=trade_pnl,
                            exit_reason="SIGNAL",
                        )
                    )
                    positions[symbol] = None
                    logger.info(
                        f"[BACKTEST] EXIT {symbol} {pos.qty} @ {exit_price:.2f}, "
                        f"PNL={trade_pnl:.2f}"
                    )
                    blotter.log(
                        TradeRecord(
                            timestamp=ts.to_pydatetime(),
                            mode="backtest",
                            symbol=symbol,
                            side="SELL",
                            quantity=pos.qty,
                            price=exit_price,
                            realized_pnl=trade_pnl,
                            reason="SIGNAL",
                            profile_name=context["profile_name"],
                        )
                    )

            pos = positions[symbol]
            if pos is not None:
                _ = (bar.close - pos.entry_price) * pos.qty

            for a in actions:
                if a["type"] == "ENTER_LONG" and positions[symbol] is None:
                    # Determine stop distance from config: prefer strategy trend.stop_distance_pct
                    # Select stop params from strategy-specific section
                    if config.strategy.name == "spy_qqq_vwap_mr":
                        mr_params = config.strategy.parameters["vwap_mr"] if isinstance(config.strategy.parameters, dict) else config.strategy.parameters.vwap_mr
                        stop_pct = mr_params.get("stop_distance_pct", 0.7)
                    else:
                        trend_params = config.strategy.parameters.trend if hasattr(config.strategy.parameters, "trend") else config.strategy.parameters["trend"]
                        if isinstance(trend_params, dict):
                            stop_pct = trend_params.get("stop_distance_pct", 0.7)
                        else:
                            stop_pct = trend_params.stop_distance_pct if trend_params.stop_distance_pct is not None else 0.7
                    # Block entries if kill-switch or cooldown for symbol
                    if risk_mgr.is_kill_switch_triggered():
                        continue
                    if risk_mgr.is_symbol_in_cooldown(symbol):
                        continue
                    order_req = size_order(
                        symbol=symbol,
                        side=OrderSide.BUY,
                        price=bar.close,
                        stop_distance_pct=float(stop_pct),
                        risk_manager=risk_mgr,
                        symbol_overrides=config.symbol_overrides,
                    )
                    if order_req is None:
                        continue
                    qty = order_req.quantity
                    # Compute stop/target levels
                    stop_price = None
                    target_price = None
                    # Determine stop mode and multiple; allow symbol override
                    if config.strategy.name == "spy_qqq_vwap_mr":
                        mr = mr_params
                        stop_mode = (mr.get("stop_mode", "percent")).lower()
                        stop_atr_mult = mr.get("stop_atr_multiple")
                        atr_lookback = int(mr.get("atr_lookback", 14))
                        take_profit_rr = mr.get("take_profit_rr")
                    else:
                        if isinstance(trend_params, dict):
                            stop_mode = (trend_params.get("stop_mode", "percent") or "percent").lower()
                            stop_atr_mult = trend_params.get("stop_atr_multiple")
                            atr_lookback = int(trend_params.get("atr_lookback", 14))
                            take_profit_rr = trend_params.get("take_profit_rr")
                        else:
                            stop_mode = (trend_params.stop_mode or "percent").lower()
                            stop_atr_mult = trend_params.stop_atr_multiple
                            atr_lookback = int(trend_params.atr_lookback)
                            take_profit_rr = trend_params.take_profit_rr
                    if symbol in config.symbol_overrides and config.symbol_overrides[symbol].trend and config.symbol_overrides[symbol].trend.stop_atr_multiple is not None:
                        stop_atr_mult = float(config.symbol_overrides[symbol].trend.stop_atr_multiple)
                    if stop_mode == "atr" and stop_atr_mult is not None:
                        # Compute ATR (as avg high-low) over lookback from data_map
                        df_sym = data_map[symbol].loc[:ts]
                        if len(df_sym) >= atr_lookback:
                            atr_val = float((df_sym["high"].tail(atr_lookback) - df_sym["low"].tail(atr_lookback)).mean())
                            stop_distance = stop_atr_mult * atr_val
                            stop_price = bar.close - stop_distance
                            if take_profit_rr is not None:
                                rr = float(take_profit_rr)
                                target_price = bar.close + rr * stop_distance
                        # else fallback to percent if not enough bars
                    if stop_price is None:
                        # Percent fallback
                        if config.strategy.name == "spy_qqq_vwap_mr":
                            pct = mr.get("stop_distance_pct")
                        else:
                            pct = trend_params.get("stop_distance_pct") if isinstance(trend_params, dict) else trend_params.stop_distance_pct
                        if pct is not None:
                            stop_price = bar.close * (1.0 - float(pct) / 100.0)
                            rr_val = take_profit_rr
                            if rr_val is not None:
                                tp_pct = float(rr_val) * float(pct)
                            target_price = bar.close * (1.0 + tp_pct / 100.0)
                        elif config.strategy.name != "spy_qqq_vwap_mr":
                            tp_pct_val = trend_params.get("take_profit_distance_pct") if isinstance(trend_params, dict) else getattr(trend_params, "take_profit_distance_pct", None)
                            if tp_pct_val is not None:
                                tp_pct = float(tp_pct_val)
                            target_price = bar.close * (1.0 + tp_pct / 100.0)

                    positions[symbol] = Position(
                        symbol=symbol,
                        qty=qty,
                        entry_price=bar.close,
                        entry_time=ts,
                        stop_price=stop_price,
                        target_price=target_price,
                    )
                    logger.info(
                        f"[BACKTEST] ENTER_LONG {symbol} {qty} @ {bar.close:.2f}"
                    )
                    blotter.log(
                        TradeRecord(
                            timestamp=ts.to_pydatetime(),
                            mode="backtest",
                            symbol=symbol,
                            side="BUY",
                            quantity=qty,
                            price=bar.close,
                            reason=a.get("reason"),
                            profile_name=context["profile_name"],
                        )
                    )

    for symbol, pos in positions.items():
        if pos is not None:
            last_price = data_map[symbol]["close"].iloc[-1]
            trade_pnl = (last_price - pos.entry_price) * pos.qty
            equity += trade_pnl
            risk_mgr.add_realized_pnl(trade_pnl)
            risk_mgr.set_equity(equity)
            pnl_list[-1] += trade_pnl
            realized_trades.append(
                RealizedTrade(
                    symbol=symbol,
                    qty=pos.qty,
                    entry_price=pos.entry_price,
                    exit_price=float(last_price),
                    entry_time=pos.entry_time.to_pydatetime().isoformat(),
                    exit_time=all_index[-1].to_pydatetime().isoformat(),
                    pnl=trade_pnl,
                    exit_reason="EOD",
                )
            )

    equity_series = pd.Series(equity_curve, index=all_index)
    pnl_series = pd.Series(pnl_list, index=all_index)

    daily_returns = equity_series.pct_change().dropna()
    sharpe = np.sqrt(252) * daily_returns.mean() / (daily_returns.std() + 1e-9)
    max_dd = (equity_series / equity_series.cummax() - 1).min()

    # Trade-level metrics
    total_trades = len(realized_trades)
    total_pnl = float(sum(t["pnl"] for t in realized_trades)) if total_trades > 0 else 0.0
    wins = sum(1 for t in realized_trades if t["pnl"] > 0)
    win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
    avg_trade_pnl = (total_pnl / total_trades) if total_trades > 0 else 0.0

    logger.info(f"Final equity: {equity:.2f}")
    logger.info(f"Annualized Sharpe: {sharpe:.2f}")
    logger.info(f"Max drawdown: {max_dd:.2%}")
    logger.info(f"Trades: {total_trades} | Win rate: {win_rate:.1f}% | Avg trade PnL: {avg_trade_pnl:.2f}")
    # ML acceptance stats if applicable
    try:
        ml_stats = getattr(strategy, "ml_stats", None)
        if ml_stats:
            cand = int(ml_stats.get("candidates", 0))
            acc = int(ml_stats.get("accepted", 0))
            acc_rate = (acc / cand * 100.0) if cand > 0 else 0.0
            logger.info(f"ML candidates: {cand} | accepted: {acc} ({acc_rate:.1f}%)")
        ml_by_sym = getattr(strategy, "ml_stats_by_symbol", None)
        if ml_by_sym:
            for sym, st in ml_by_sym.items():
                sc = int(st.get("candidates", 0))
                sa = int(st.get("accepted", 0))
                sr = (sa / sc * 100.0) if sc > 0 else 0.0
                logger.info(f"ML [{sym}] candidates: {sc} | accepted: {sa} ({sr:.1f}%)")
    except Exception:
        pass

    # Write daily summaries
    daily_equity = equity_series.resample("1D").last().dropna()
    if not daily_equity.empty:
        prev_equity = daily_equity.shift(1).fillna(config.backtest.initial_capital)
        for d, end_eq in daily_equity.items():
            start_eq = float(prev_equity.loc[d])
            max_loss_hit = (start_eq - end_eq) / start_eq * 100.0 >= config.risk.max_daily_loss_pct if start_eq != 0 else False
            daily_logger.log(
                d.date(),
                mode="backtest",
                starting_equity=start_eq,
                ending_equity=float(end_eq),
                max_daily_loss_hit=max_loss_hit,
            )

    out_dir = Path("backtest_output") / (label if label else "")
    out_dir.mkdir(exist_ok=True)
    # Save equity and trades to CSV for external analysis/plotting
    equity_series.to_frame(name="equity").to_csv(out_dir / "equity_curve.csv", index_label="timestamp")
    trades_path = out_dir / "realized_trades.csv"
    pd.DataFrame(realized_trades).to_csv(trades_path, index=False)
    try:
        import matplotlib.pyplot as plt  # type: ignore

        plt.figure()
        equity_series.plot()
        plt.title("Equity Curve")
        plt.tight_layout()
        plt.savefig(out_dir / "equity_curve.png")
        logger.info(f"Saved equity curve to {out_dir / 'equity_curve.png'}")
        # Drawdown plot
        plt.figure()
        dd = equity_series / equity_series.cummax() - 1.0
        dd.plot()
        plt.title("Drawdown")
        plt.tight_layout()
        plt.savefig(out_dir / "drawdown.png")
        logger.info(f"Saved drawdown plot to {out_dir / 'drawdown.png'}")
    except Exception as e:
        # Make plotting optional to avoid build issues on newer Python/macOS
        logger.warning(f"Skipping equity curve plot (matplotlib unavailable): {e}")

    # Inline analysis summary
    try:
        logger.info("Running trade analysis summary...")
        analyze_trades(trades_path)
    except Exception as e:
        logger.warning(f"Trade analysis summary failed: {e}")

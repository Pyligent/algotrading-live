import time
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Any

import pandas as pd
from ib_insync import IB, util

from src.core.config_loader import AppConfig
from src.core.enums import OrderSide
from src.core.logging_utils import get_logger
from src.broker.ib_client import IBClient
from src.broker.order_types import OrderRequest
from src.data.bar_generator import Bar
from src.strategy.spy_qqq_ensemble import SpyQqqEnsembleStrategy
from src.risk.risk_limits import RiskManager
from src.risk.risk_checker import size_order, should_flatten_all
from src.core.trade_logger import TradeBlotter, DailySummaryLogger, TradeRecord

logger = get_logger(__name__)


def run_live_trading(config: AppConfig):
    log_file = Path(config.live.log_dir) / "live.log"
    _ = get_logger("live", str(log_file))

    ib_client = IBClient(config.ibkr, config.universe)
    try:
        ib_client.connect()
    except Exception as e:
        logger.error(f"Failed to connect to IBKR: {e}")
        return

    ib: IB = ib_client.ib

    # Build strategy based on profile
    params_obj = config.strategy.parameters
    params = params_obj.model_dump() if hasattr(params_obj, "model_dump") else params_obj
    if config.strategy.name == "spy_qqq_vwap_mr":
        from src.strategy.spy_qqq_vwap_mr import SpyQqqVWAPMRStrategy
        strategy = SpyQqqVWAPMRStrategy(params=params)
    else:
        strategy = SpyQqqEnsembleStrategy(params=params)
    risk_mgr = RiskManager(config.risk)

    equity = config.backtest.initial_capital
    positions = ib_client.get_positions()
    risk_mgr.reset_for_new_day(equity, positions)

    blotter = TradeBlotter(config.live.log_dir)
    daily_logger = DailySummaryLogger(config.live.log_dir)

    context: Dict[str, Any] = {
        "universe": config.universe,
        "ml": config.ml,
        "symbol_overrides": config.symbol_overrides,
        "profile_name": config.active_profile or "default",
    }
    strategy.on_start(context)

    last_bar_time: Dict[str, datetime | None] = {s: None for s in config.universe.symbols}
    intraday_dfs: Dict[str, pd.DataFrame] = {
        s: pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"]).set_index("date")
        for s in config.universe.symbols
    }

    # Track entries to compute realized PnL on exits we trigger
    # Track per-position entry, stop, target
    open_entries: Dict[str, dict] = {}
    last_summary_date: date | None = None

    try:
        while True:
            now = datetime.now()
            if not risk_mgr.is_within_trading_hours(now):
                if config.risk.emergency_flatten_on_exit:
                    for symbol in config.universe.symbols:
                        try:
                            if not config.live.dry_run:
                                ib_client.flatten_symbol(symbol)
                            else:
                                logger.info(f"[DRY RUN] Would flatten {symbol}")
                        except Exception as e:
                            logger.error(f"Failed to flatten {symbol}: {e}")
                    # Log daily summary once per day after flattening
                    if last_summary_date != now.date():
                        ending_equity = equity
                        daily_logger.log(
                            d=now.date(),
                            mode="live",
                            starting_equity=risk_mgr.state.starting_equity_today,
                            ending_equity=ending_equity,
                            max_daily_loss_hit=risk_mgr.is_daily_loss_limit_hit(),
                        )
                        last_summary_date = now.date()
                time.sleep(config.live.polling_interval_seconds)
                continue

            if should_flatten_all(risk_mgr):
                for symbol in config.universe.symbols:
                    try:
                        if not config.live.dry_run:
                            ib_client.flatten_symbol(symbol)
                        else:
                            logger.info(f"[DRY RUN] Would flatten {symbol} due to daily loss limit")
                    except Exception as e:
                        logger.error(f"Failed to flatten {symbol}: {e}")
                # Write summary at time of max daily loss
                ending_equity = equity
                daily_logger.log(
                    d=now.date(),
                    mode="live",
                    starting_equity=risk_mgr.state.starting_equity_today,
                    ending_equity=ending_equity,
                    max_daily_loss_hit=True,
                )
                time.sleep(config.live.polling_interval_seconds)
                continue

            for symbol in config.universe.symbols:
                contract = ib_client.get_contract(symbol)
                try:
                    bars = ib.reqHistoricalData(
                        contract,
                        endDateTime="",
                        durationStr="180 D",
                        barSizeSetting=config.data.bar_size,
                        whatToShow=config.data.what_to_show,
                        useRTH=True,
                        formatDate=1,
                    )
                except Exception as e:
                    logger.error(f"Historical data request failed for {symbol}: {e}")
                    continue
                if not bars:
                    logger.warning(f"No bars returned for {symbol}")
                    continue
                df = util.df(bars)
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
                intraday_dfs[symbol] = df

                bar_ts = df.index[-1].to_pydatetime()
                if last_bar_time[symbol] is not None and bar_ts == last_bar_time[symbol]:
                    continue

                last_bar_time[symbol] = bar_ts
                row = df.iloc[-1]
                bar = Bar(
                    symbol=symbol,
                    timestamp=bar_ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )

                actions = strategy.on_bar(bar, context)
                positions = ib_client.get_positions()
                risk_mgr.state.open_positions = positions

                for a in actions:
                    if a["type"] == "EXIT" and positions.get(symbol, 0) != 0:
                        qty = abs(positions[symbol])
                        side = OrderSide.SELL if positions[symbol] > 0 else OrderSide.BUY
                        try:
                            if qty <= 0:
                                continue
                            if not config.live.dry_run:
                                ib_client.place_market_order(
                                    OrderRequest(symbol=symbol, quantity=qty, side=side)
                                )
                            else:
                                logger.info(
                                    f"[DRY RUN] Would place EXIT market order {symbol} {side.value} x{qty}"
                                )
                            # PnL calc if we have entry info
                            realized_pnl = None
                            if symbol in open_entries:
                                entry = open_entries.pop(symbol)
                                entry_qty = entry["qty"]
                                entry_price = entry["entry_price"]
                                realized_pnl = (bar.close - entry_price) * entry_qty if side == OrderSide.SELL else (entry_price - bar.close) * entry_qty
                                equity += realized_pnl
                                risk_mgr.add_realized_pnl(realized_pnl)
                                risk_mgr.set_equity(equity)
                            blotter.log(
                                TradeRecord(
                                    timestamp=bar_ts,
                                    mode="live",
                                    symbol=symbol,
                                    side=side.value,
                                    quantity=qty,
                                    price=bar.close,
                                    realized_pnl=realized_pnl,
                                    reason="SIGNAL",
                                    profile_name=context["profile_name"],
                                )
                            )
                        except Exception as e:
                            logger.error(f"Order placement failed for EXIT {symbol}: {e}")

                for a in actions:
                    if a["type"] == "ENTER_LONG" and positions.get(symbol, 0) == 0:
                        if not risk_mgr.can_open_new_position():
                            continue
                        # derive stop distance from config trend params if present
                        # Select stop params for active strategy
                        if config.strategy.name == "spy_qqq_vwap_mr":
                            mr_params = config.strategy.parameters["vwap_mr"] if isinstance(config.strategy.parameters, dict) else config.strategy.parameters.vwap_mr
                            stop_pct = mr_params.get("stop_distance_pct", 0.7)
                        else:
                            trend_params = config.strategy.parameters.trend
                            stop_pct = trend_params.stop_distance_pct if trend_params.stop_distance_pct is not None else 0.7
                        # Block entries if kill-switch or cooldown for symbol
                        if risk_mgr.is_kill_switch_triggered():
                            continue
                        if risk_mgr.is_symbol_in_cooldown(symbol):
                            logger.info(f"COOLDOWN: blocking entries on {symbol}")
                            continue
                        order_req = size_order(
                            symbol=symbol,
                            side=OrderSide.BUY,
                            price=bar.close,
                            stop_distance_pct=float(stop_pct),
                            risk_manager=risk_mgr,
                            symbol_overrides=config.symbol_overrides,
                        )
                        if order_req is not None:
                            try:
                                if order_req.quantity <= 0:
                                    continue
                                # Extra sanity checks
                                if symbol not in config.universe.symbols:
                                    logger.warning(f"Symbol {symbol} not in configured universe, skipping")
                                    continue
                                if not risk_mgr.is_within_trading_hours(bar.timestamp):
                                    logger.warning("Outside trading hours, skipping order")
                                    continue
                                if not config.live.dry_run:
                                    ib_client.place_market_order(order_req)
                                else:
                                    logger.info(
                                        f"[DRY RUN] Would place ENTER_LONG market order {symbol} BUY x{order_req.quantity}"
                                    )
                                # Compute and store stop/target levels (ATR or percent)
                                stop_price = None
                                target_price = None
                                if config.strategy.name == "spy_qqq_vwap_mr":
                                    mr = mr_params
                                    stop_mode = (mr.get("stop_mode", "percent")).lower()
                                    stop_atr_mult = mr.get("stop_atr_multiple")
                                    atr_lookback = int(mr.get("atr_lookback", 14))
                                    take_profit_rr = mr.get("take_profit_rr")
                                else:
                                    stop_mode = (trend_params.stop_mode or "percent").lower()
                                    stop_atr_mult = trend_params.stop_atr_multiple
                                    atr_lookback = int(trend_params.atr_lookback)
                                    take_profit_rr = trend_params.take_profit_rr
                                if symbol in config.symbol_overrides and config.symbol_overrides[symbol].trend and config.symbol_overrides[symbol].trend.stop_atr_multiple is not None:
                                    stop_atr_mult = float(config.symbol_overrides[symbol].trend.stop_atr_multiple)
                                if stop_mode == "atr" and stop_atr_mult is not None:
                                    df_sym = intraday_dfs[symbol]
                                    if len(df_sym) >= atr_lookback:
                                        atr_val = float((df_sym["high"].tail(atr_lookback) - df_sym["low"].tail(atr_lookback)).mean())
                                        stop_distance = stop_atr_mult * atr_val
                                        stop_price = bar.close - stop_distance
                                        if take_profit_rr is not None:
                                            rr = float(take_profit_rr)
                                            target_price = bar.close + rr * stop_distance
                                if stop_price is None:
                                    pct = mr.get("stop_distance_pct") if config.strategy.name == "spy_qqq_vwap_mr" else trend_params.stop_distance_pct
                                    if pct is not None:
                                        stop_price = bar.close * (1.0 - float(pct) / 100.0)
                                        rr_val = take_profit_rr
                                        if rr_val is not None:
                                            tp_pct = float(rr_val) * float(pct)
                                            target_price = bar.close * (1.0 + tp_pct / 100.0)
                                open_entries[symbol] = {
                                    "qty": order_req.quantity,
                                    "entry_price": bar.close,
                                    "stop_price": stop_price,
                                    "target_price": target_price,
                                    "entry_atr": float((intraday_dfs[symbol]["high"].tail(trend_params.atr_lookback) - intraday_dfs[symbol]["low"].tail(trend_params.atr_lookback)).mean()) if trend_params.atr_lookback and len(intraday_dfs[symbol]) >= trend_params.atr_lookback else None,  # type: ignore
                                    "stop_mode": stop_mode,
                                    "stop_atr_multiple": stop_atr_mult,
                                }
                                blotter.log(
                                    TradeRecord(
                                        timestamp=bar_ts,
                                        mode="live",
                                        symbol=symbol,
                                        side="BUY",
                                        quantity=order_req.quantity,
                                        price=bar.close,
                                        reason=a.get("reason") if not config.live.dry_run else f"dry_run=True; {a.get('reason')}",
                                        profile_name=context["profile_name"],
                                    )
                                )
                            except Exception as e:
                                logger.error(f"Order placement failed for ENTER_LONG {symbol}: {e}")

                # 2) Apply hard stop/target checks on live positions
                if positions.get(symbol, 0) != 0 and symbol in open_entries:
                    entry = open_entries[symbol]
                    stop_price = entry.get("stop_price")
                    target_price = entry.get("target_price")
                    qty_live = abs(positions[symbol])
                    side = OrderSide.SELL if positions[symbol] > 0 else OrderSide.BUY
                    hit_reason = None
                    exit_px = None
                    if stop_price is not None and bar.low <= float(stop_price):
                        hit_reason = "STOP"
                        exit_px = float(stop_price)
                    elif target_price is not None and bar.high >= float(target_price):
                        hit_reason = "TARGET"
                        exit_px = float(target_price)
                    if hit_reason is not None:
                        try:
                            if not config.live.dry_run:
                                ib_client.place_market_order(
                                    OrderRequest(symbol=symbol, quantity=qty_live, side=side)
                                )
                            else:
                                logger.info(f"[DRY RUN] Would exit {symbol} {side.value} x{qty_live} REASON={hit_reason}")
                            realized_pnl = (exit_px - entry["entry_price"]) * entry["qty"] if side == OrderSide.SELL else (entry["entry_price"] - exit_px) * entry["qty"]
                            equity += realized_pnl
                            risk_mgr.add_realized_pnl(realized_pnl)
                            risk_mgr.set_equity(equity)
                            if hit_reason == "STOP":
                                atr_entry = entry.get("entry_atr")
                                logger.warning(
                                    f"STOP_EXIT symbol={symbol} qty={entry['qty']} entry={entry['entry_price']:.2f} "
                                    f"exit={exit_px:.2f} pnl={realized_pnl:.2f} atr_at_entry={atr_entry} "
                                    f"stop_mode={entry.get('stop_mode')} stop_multiple={entry.get('stop_atr_multiple')}"
                                )
                            # Set cooldown after STOP
                            if config.risk.stop_cooldown.enabled and hit_reason == "STOP":
                                bars = int(config.risk.stop_cooldown.cooldown_bars or 0)
                                risk_mgr.set_stop_cooldown(symbol, bars)
                                logger.warning(f"COOLDOWN set for {symbol}: {bars} bars after STOP")
                            blotter.log(
                                TradeRecord(
                                    timestamp=bar_ts,
                                    mode="live",
                                    symbol=symbol,
                                    side=side.value,
                                    quantity=qty_live,
                                    price=exit_px,
                                    realized_pnl=realized_pnl,
                                    reason=hit_reason,
                                    profile_name=context["profile_name"],
                                )
                            )
                            open_entries.pop(symbol, None)
                        except Exception as e:
                            logger.error(f"Order placement failed for {hit_reason} exit {symbol}: {e}")

            time.sleep(config.live.polling_interval_seconds)

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, flattening and exiting...")
        if config.risk.emergency_flatten_on_exit:
            for symbol in config.universe.symbols:
                try:
                    if not config.live.dry_run:
                        ib_client.flatten_symbol(symbol)
                    else:
                        logger.info(f"[DRY RUN] Would flatten {symbol} on shutdown")
                except Exception as e:
                    logger.error(f"Failed to flatten {symbol} on shutdown: {e}")
        # Daily summary on shutdown
        now = datetime.now()
        try:
            daily_logger.log(
                d=now.date(),
                mode="live",
                starting_equity=risk_mgr.state.starting_equity_today,
                ending_equity=equity,
                max_daily_loss_hit=risk_mgr.is_daily_loss_limit_hit(),
            )
        except Exception as e:
            logger.error(f"Failed to write daily summary on shutdown: {e}")
    finally:
        ib_client.disconnect()

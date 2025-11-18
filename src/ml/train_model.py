import argparse
from pathlib import Path

import joblib  # type: ignore
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier  # type: ignore
from sklearn.metrics import accuracy_score, precision_recall_fscore_support, confusion_matrix  # type: ignore

from src.core.config_loader import load_config
from src.data.bar_generator import Bar
from src.ml.features import compute_features
from src.strategy.spy_qqq_ensemble import SpyQqqEnsembleStrategy


def build_dataset_for_symbol(df: pd.DataFrame, cfg, symbol: str) -> pd.DataFrame:
    # Build strategy to identify candidate entries for this symbol
    params_obj = cfg.strategy.parameters
    params = params_obj.model_dump() if hasattr(params_obj, "model_dump") else params_obj
    strategy = SpyQqqEnsembleStrategy(params=params)
    context = {"universe": cfg.universe}
    strategy.on_start(context)
    records = []
    closes = df["close"].values
    idx_list = df.index.to_list()
    H = cfg.ml.horizon_bars
    thr = cfg.ml.target_move_pct
    # Iterate bars
    # Resolve strategy param accessors for feature engineering
    sp = cfg.strategy.parameters
    if hasattr(sp, "trend"):
        ema_fast_cfg = sp.trend.ema_fast
        ema_slow_cfg = sp.trend.ema_slow
        atr_lookback_cfg = sp.trend.atr_lookback
        rsi_period_cfg = sp.pullback.rsi_period
        vwap_lb_cfg = sp.vwap.lookback_bars_slope
    else:
        ema_fast_cfg = sp["trend"]["ema_fast"]
        ema_slow_cfg = sp["trend"]["ema_slow"]
        atr_lookback_cfg = sp["trend"]["atr_lookback"]
        rsi_period_cfg = sp["pullback"]["rsi_period"]
        vwap_lb_cfg = sp["vwap"]["lookback_bars_slope"]

    for i, ts in enumerate(idx_list):
        row = df.iloc[i]
        bar = Bar(
            symbol=symbol,
            timestamp=ts.to_pydatetime(),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )
        actions = strategy.on_bar(bar, context)
        if any(a.get("type") == "ENTER_LONG" and a.get("symbol") == symbol for a in actions):
            df_slice = df.iloc[: i + 1]
            feat = compute_features(
                df_slice,
                ema_fast=ema_fast_cfg,
                ema_slow=ema_slow_cfg,
                vwap_lookback=vwap_lb_cfg,
                rsi_period=rsi_period_cfg,
                atr_lookback=atr_lookback_cfg,
                signals=None,
            )
            if not feat:
                continue
            if i + 1 >= len(closes):
                continue
            j_end = min(len(closes), i + 1 + H)
            fwd_max = float(np.max(closes[i + 1 : j_end]))
            entry = float(closes[i])
            fwd_ret_pct = (fwd_max - entry) / entry * 100.0
            y = 1 if fwd_ret_pct >= thr else 0
            feat["label"] = y
            feat["ts"] = ts.to_pydatetime()
            feat["ts_label_end"] = df.index[j_end - 1].to_pydatetime()
            records.append(feat)
    df_feat = pd.DataFrame(records).dropna()
    return df_feat


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="Path to config YAML")
    parser.add_argument(
        "--symbols",
        nargs="*",
        help="Symbols to train on (default: all in config.universe.symbols)",
    )
    parser.add_argument(
        "--model-path",
        type=str,
        help="Override output model path (defaults to cfg.ml.model_path)",
    )
    parser.add_argument("--train-start", type=str, help="Train period start (YYYY-MM-DD) [overrides config]")
    parser.add_argument("--train-end", type=str, help="Train period end (YYYY-MM-DD) [overrides config]")
    parser.add_argument("--val-start", type=str, help="Validation period start (YYYY-MM-DD) [overrides config]")
    parser.add_argument("--val-end", type=str, help="Validation period end (YYYY-MM-DD) [overrides config]")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    symbols = args.symbols if args.symbols else cfg.universe.symbols

    X_list = []
    for sym in symbols:
        data_path = Path(cfg.data.storage_path) / f"{sym}_5min.csv"
        if not data_path.exists():
            print(f"Skipping {sym}: {data_path} not found")
            continue
        df = pd.read_csv(data_path, parse_dates=["date"], index_col="date")
        df_sym = build_dataset_for_symbol(df, cfg, sym)
        if len(df_sym) > 0:
            X_list.append(df_sym)

    if not X_list:
        raise RuntimeError("No training data built; ensure CSVs exist and strategy generates candidates.")

    X_all = pd.concat(X_list, axis=0).reset_index(drop=True)
    # Ensure datetime columns
    X_all["ts"] = pd.to_datetime(X_all["ts"])
    X_all["ts_label_end"] = pd.to_datetime(X_all["ts_label_end"])
    y_all = X_all["label"].astype(int)
    # Optionally apply explicit date splits without leakage (label end must be within split)
    # Prefer CLI ranges, else use config ranges if present
    cli_explicit = all([args.train_start, args.train_end, args.val_start, args.val_end])
    cfg_explicit = all(
        [
            cfg.ml.train_start_date,
            cfg.ml.train_end_date,
            cfg.ml.val_start_date,
            cfg.ml.val_end_date,
        ]
    )
    use_explicit = cli_explicit or cfg_explicit
    if use_explicit:
        train_start = pd.to_datetime(args.train_start or cfg.ml.train_start_date)
        train_end = pd.to_datetime(args.train_end or cfg.ml.train_end_date)
        val_start = pd.to_datetime(args.val_start or cfg.ml.val_start_date)
        val_end = pd.to_datetime(args.val_end or cfg.ml.val_end_date)
        train_mask = (X_all["ts"] >= train_start) & (X_all["ts"] <= train_end) & (X_all["ts_label_end"] <= train_end)
        val_mask = (X_all["ts"] >= val_start) & (X_all["ts"] <= val_end) & (X_all["ts_label_end"] <= val_end)
        X_train = X_all.loc[train_mask].copy()
        y_train = y_all.loc[train_mask].copy()
        X_val = X_all.loc[val_mask].copy()
        y_val = y_all.loc[val_mask].copy()
    else:
        # Chronological split (80/20) without enforcing horizon bounds
        n = len(X_all)
        split = int(n * 0.8)
        X_train = X_all.iloc[:split].copy()
        y_train = y_all.iloc[:split].copy()
        X_val = X_all.iloc[split:].copy()
        y_val = y_all.iloc[split:].copy()

    # Drop label/time columns from features
    for d in (X_train, X_val):
        for col in ("label", "ts", "ts_label_end"):
            if col in d.columns:
                d.drop(columns=[col], inplace=True)

    print(
        f"Dataset sizes | train: {len(X_train)} (from {X_all['ts'].min()} to {X_all['ts'].max()}) "
        f"| val: {len(X_val)}"
    )

    clf = GradientBoostingClassifier(random_state=42)
    clf.fit(X_train, y_train)

    # Validation metrics
    if hasattr(clf, "predict_proba"):
        p = clf.predict_proba(X_val)[:, 1]
    else:
        p = clf.predict(X_val).astype(float)
    y_pred = (p >= 0.5).astype(int)
    acc = accuracy_score(y_val, y_pred)
    prec, rec, f1, _ = precision_recall_fscore_support(y_val, y_pred, average="binary", zero_division=0)
    cm = confusion_matrix(y_val, y_pred)
    print(f"Validation accuracy: {acc:.3f}, precision: {prec:.3f}, recall: {rec:.3f}, f1: {f1:.3f}, samples: {len(X_val)}")
    print(f"Confusion matrix:\n{cm}")
    # Threshold sweep
    thresholds = [0.5, 0.55, 0.6, 0.65]
    rows = []
    base_win_rate = float(y_val.mean()) if len(y_val) > 0 else 0.0
    # Reconstruct entry price and forward return for validation set from available data? We labeled using forward max returns
    # Here we approximate by mapping label==1 as positive event; compute accepted count and win rate proxy.
    for th in thresholds:
        mask = p >= th
        accepted = int(mask.sum())
        win_rate = float(y_val[mask].mean()) if accepted > 0 else 0.0
        rows.append(
            {
                "threshold": th,
                "accepted": accepted,
                "win_rate": win_rate,
                "edge_vs_all": win_rate - base_win_rate,
            }
        )
    report = pd.DataFrame(rows)
    reports_dir = Path("ml_reports")
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / "threshold_report.csv"
    report.to_csv(report_path, index=False)
    print(f"Saved threshold report to {report_path}")

    # Save model with metadata
    model_path = Path(args.model_path) if args.model_path else Path(cfg.ml.model_path)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "model": clf,
        "metadata": {
            "horizon_bars": int(cfg.ml.horizon_bars),
            "target_move_pct": float(cfg.ml.target_move_pct),
            "train_start": str(train_start) if use_explicit else None,
            "train_end": str(train_end) if use_explicit else None,
            "val_start": str(val_start) if use_explicit else None,
            "val_end": str(val_end) if use_explicit else None,
            "symbols": symbols,
        },
        "feature_names": list(X_train.columns),
    }
    joblib.dump(artifact, model_path)
    print(f"Saved model to {model_path}")


if __name__ == "__main__":
    main()



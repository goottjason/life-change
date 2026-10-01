"""Fixed profit hypotheses, evaluated with next-open entries and explicit costs.

This is candle research, not a replay of the live screener/limit-order execution.
Candidate definitions must be frozen before evaluating another period. All candidates
are reported; the largest historical return is not a deployment recommendation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import sys
import threading
import time

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import charter as C
from indicators import ta


@dataclass(frozen=True)
class Candidate:
    name: str
    family: str
    minutes: int = 5
    level: float = 3
    gate: float = .006
    exit_level: float = 70
    stop: float = .025
    lookback: int = 288
    volume: float = 5
    trail: float = 3
    btc_gate: bool = False


# Frozen 2026-10-01 before loading July-September candles. No market/hour sweeps.
B = Candidate("breakout_base", "breakout")
R5 = Candidate("rsi5_base", "rsi")
R15 = Candidate("rsi15_base", "rsi", minutes=15, gate=.01, stop=.03)
CANDIDATES = (
    B, replace(B, name="breakout_vol6", volume=6),
    replace(B, name="breakout_vol10", volume=10),
    replace(B, name="breakout_trail1.5", trail=1.5),
    replace(B, name="breakout_btc_up", btc_gate=True),
    replace(B, name="breakout_15m", minutes=15, lookback=96),
    R5, replace(R5, name="rsi5_entry5", level=5),
    replace(R5, name="rsi5_exit90", exit_level=90),
    replace(R5, name="rsi5_gate80", gate=.0048),
    R15, replace(R15, name="rsi15_entry5", level=5),
    replace(R15, name="rsi15_exit90", exit_level=90),
    replace(R15, name="rsi15_no_signal_exit", exit_level=101),
    Candidate("hourly_momentum", "breakout", minutes=60, lookback=24,
              volume=2, trail=3, btc_gate=True),
)


def fetch_recent(data_dir):
    """Public candles, fixed cutoff; a shared limiter keeps requests below 5/s."""
    import requests
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    next_at = [0.]

    def fetch(sym):
        path = data_dir / f"KRW-{sym}_minute5.csv"
        if path.exists():
            print(sym, "cached", flush=True)
            return
        session = requests.Session()
        to, rows = "2026-10-01T00:00:00Z", []
        while True:
            with lock:
                time.sleep(max(0., next_at[0] - time.monotonic()))
                next_at[0] = time.monotonic() + .21
            for attempt in range(5):
                response = session.get("https://api.upbit.com/v1/candles/minutes/5",
                                       params={"market": f"KRW-{sym}", "count": 200,
                                               "to": to}, timeout=20)
                if response.status_code == 200:
                    break
                time.sleep(2 * (attempt + 1))
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            rows.extend(batch)
            to = batch[-1]["candle_date_time_utc"] + "Z"
            if batch[-1]["candle_date_time_utc"] <= "2026-07-17T00:00:00":
                break
        raw = pd.DataFrame(rows).drop_duplicates("candle_date_time_utc").sort_values("candle_date_time_utc")
        raw = raw[raw.candle_date_time_utc >= "2026-07-17T00:00:00"]
        df = pd.DataFrame({"open": raw.opening_price.values, "high": raw.high_price.values,
                           "low": raw.low_price.values, "close": raw.trade_price.values,
                           "volume": raw.candle_acc_trade_volume.values},
                          index=pd.to_datetime(raw.candle_date_time_kst))
        df.index.name = "time"
        df.to_csv(path)
        print(sym, len(df), flush=True)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(fetch, C.VALIDATED_MARKETS))


def resample(df, minutes):
    if minutes == 5:
        return df
    return df.resample(f"{minutes}min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last",
         "volume": "sum"}).dropna()


def hourly_up(df, index):
    h = df.close.resample("1h").last().dropna()
    # Labels are candle OPEN times. Only a completed hourly candle is observable.
    up = (h > ta.ema(h, 200)).shift(1)
    return up.reindex(index, method="ffill").fillna(False).to_numpy(bool)


def simulate(df, btc, candidate, cost, start, end):
    """Same-bar stop before target; gaps slip to open; signals exit next open.

    Trailing highs activate the following candle, avoiding assumed high/low order.
    OHLC cannot reproduce the live intrabar signal or guarantee stop fills.
    """
    p = candidate
    d = resample(df, p.minutes)
    o, h, lo, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = ta.atr(d).to_numpy(float)
    up = hourly_up(df, d.index)
    rsi = ta.rsi(d.close, 2).to_numpy(float)
    if p.family == "rsi":
        signal = (rsi <= p.level) & (atr / c >= p.gate) & up
    else:
        prev_high = d.high.shift(1).rolling(p.lookback).max().to_numpy(float)
        prev_vol = d.volume.shift(1).rolling(p.lookback).mean().to_numpy(float)
        signal = (c > prev_high) & (d.volume.to_numpy(float) >= prev_vol * p.volume)
        if p.btc_gate:
            signal &= hourly_up(btc, d.index)
    entries = np.flatnonzero(signal)
    free = 0
    records = []
    interval = pd.Timedelta(minutes=p.minutes)
    for i in entries:
        fill = int(i) + 1
        if i < max(300, p.lookback + 15) or fill < free or fill >= len(d):
            continue
        when = d.index[fill]
        if when < start or when >= end:
            continue
        ep, ea = o[fill], atr[i]
        if not (ep > 0 and np.isfinite(ea) and ea > 0):
            continue
        stop = p.stop if p.family == "rsi" else max(ea / ep, .01)
        hard, peak = ep * (1 - stop), ep
        target = ep * (1 + stop)
        pending = None
        last = min(len(d) - 1, int(d.index.searchsorted(end)) - 1)
        xp, why, j = c[last], "period_end", last
        for j in range(fill, last + 1):
            sl = hard if p.family == "rsi" else max(hard, peak - p.trail * ea)
            if o[j] <= sl:
                xp, why = o[j], "gap_stop"
                break
            if pending:
                xp, why = o[j], pending
                break
            if lo[j] <= sl:
                xp, why = sl, "stop"
                break
            if p.family == "rsi" and h[j] >= target:
                xp, why = target, "target"
                break
            peak = max(peak, h[j])
            if p.family == "rsi":
                if rsi[j] >= p.exit_level:
                    pending = "rsi_exit"
                elif (j - fill) * p.minutes >= 480:
                    pending = "time"
            elif (j - fill) * p.minutes >= (720 if p.minutes == 60 else 60) and c[j] / ep - 1 < .003:
                pending = "time"
        else:
            j = last
        records.append({"entry": str(when), "exit": str(d.index[j] + interval),
                        "net": float(xp / ep - 1 - cost), "reason": why,
                        "stop": float(stop)})
        free = j + 1
    return records


def metrics(trades):
    a = np.asarray([t["net"] for t in trades])
    if not len(a):
        return {"n": 0}
    w, l = a[a > 0], a[a <= 0]
    weeks = pd.Series(a, index=pd.to_datetime([t["entry"] for t in trades])).resample("W").sum()
    rng = np.random.default_rng(20261001)
    # Blocks retain common-market/time shocks. This estimates historical precision,
    # not future return; sparse weeks and selection still limit inference.
    groups = {}
    for t in trades:
        week = str(pd.Timestamp(t["entry"]).to_period("W"))
        total, n = groups.get(week, (0., 0))
        groups[week] = (total + t["net"], n + 1)
    g = np.asarray(list(groups.values()))
    ix = rng.integers(0, len(g), size=(3000, len(g)))
    sampled = g[ix].sum(axis=1)
    ci = np.quantile(sampled[:, 0] / sampled[:, 1], [.025, .975])
    return {"n": len(a), "mean_pct": float(a.mean() * 100),
            "win_pct": len(w) / len(a) * 100,
            "pf": float(w.sum() / -l.sum()) if l.sum() else None,
            "ci95_week_pct": (ci * 100).tolist(),
            "positive_weeks": int((weeks > 0).sum()), "weeks": len(weeks),
            "worst_pct": float(a.min() * 100)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", action="store_true")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--cost-mode", choices=("entry", "screen"), default="entry")
    args = parser.parse_args()
    if args.manifest:
        print(json.dumps([asdict(c) for c in CANDIDATES], indent=2))
        return
    if args.fetch:
        fetch_recent(args.data_dir)
    panel = {}
    for sym in C.VALIDATED_MARKETS:
        path = args.data_dir / f"KRW-{sym}_minute5.csv"
        if path.exists():
            panel[sym] = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    if "BTC" not in panel:
        raise ValueError("BTC history required")
    periods = [("august", pd.Timestamp("2026-08-01"), pd.Timestamp("2026-09-01")),
               ("september", pd.Timestamp("2026-09-01"), pd.Timestamp("2026-10-01"))]
    # Old histories have already been used by past research: these are robustness
    # checks, not pristine holdouts. Never label them new independent validation.
    if max(d.index[-1] for d in panel.values()) < pd.Timestamp("2026-08-01"):
        periods = [("old_first", pd.Timestamp("2024-08-01"), pd.Timestamp("2025-08-01")),
                   ("old_second", pd.Timestamp("2025-08-01"), pd.Timestamp("2026-07-26"))]
    results = []
    for p in CANDIDATES:
        for label, start, end in periods:
            trades = []
            for sym, d in panel.items():
                if p.family == "rsi" and p.minutes == 5 and sym in C.STRATEGY_BLACKLIST["rsi2"]:
                    continue
                # Entry caps can exceed universe-screening caps between refreshes.
                # Default is the actual entry ceiling (a cost stress, not measured
                # historical spread). Retain the initial narrower-cost scenario
                # explicitly, so a cost assumption cannot masquerade as an edge.
                name = "breakout" if p.family != "rsi" else ("rsi2" if p.minutes == 5 else "rsi2_15m")
                spread = C.strategy_spread_cap(name, f"KRW-{sym}")
                if args.cost_mode == "screen":
                    spread = C.VALIDATED_MARKETS[sym]
                    if p.family != "rsi" or p.minutes == 5:
                        spread = min(spread, C.MAX_SPREAD_RATIO)
                for t in simulate(d, panel["BTC"], p, C.FEE_ROUNDTRIP + spread, start, end):
                    trades.append({**t, "market": "KRW-" + sym})
            row = {"candidate": p.name, "period": label, **metrics(trades), "trades": trades}
            results.append(row)
            print(p.name, label, json.dumps({k: v for k, v in row.items() if k != "trades"}), flush=True)
    args.output.write_text(json.dumps({"cost_mode": args.cost_mode,
                                      "hypotheses": [asdict(p) for p in CANDIDATES],
                                      "markets": sorted(panel), "results": results}, indent=2))


if __name__ == "__main__":
    main()

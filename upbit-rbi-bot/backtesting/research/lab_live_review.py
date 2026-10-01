"""Read-only live ledger review, with optional offline candle sensitivity checks.

Input: JSON array from SELECT * FROM trades ORDER BY id (no credentials).
Run from upbit-rbi-bot:
    python backtesting/research/lab_live_review.py --ledger logs/review-ledger.json \
        --observed-equity 93176 --data-dir backtesting/research/data

Filtering entry snapshots is a diagnostic, not a simulated trading result:
later signals, freed capital and intrabar fills can change the trading path.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
KST = timezone(timedelta(hours=9))


def kst(ts):
    d = datetime.fromisoformat(ts)
    return (d.replace(tzinfo=KST) if d.tzinfo is None else d.astimezone(KST)).isoformat()


def pair(rows):
    opened, trips, unmatched, overwritten = {}, [], [], []
    for row in sorted(rows, key=lambda r: r["id"]):
        key = row["strategy"], row["market"]
        if row["event"] == "entry":
            if key in opened:
                overwritten.append(opened[key]["id"])
            opened[key] = row
        elif row["event"] == "exit":
            entry = opened.pop(key, None)
            if entry is None:
                unmatched.append(row["id"])
                continue
            try:
                ctx = json.loads(entry.get("context") or "{}")
            except (ValueError, TypeError):
                ctx = {}
            size = entry["size_krw"] or 0
            pnl = row["pnl_krw"] or 0
            trips.append({"id": row["id"], "market": row["market"],
                          "strategy": row["strategy"], "entry": kst(entry["ts"]),
                          "exit": kst(row["ts"]), "pnl": pnl, "size": size,
                          "pct": pnl / size * 100 if size > 0 else None,
                          "reason": row["reason"], "ctx": ctx})
    return trips, {"unmatched_exits": unmatched, "overwritten_entries": overwritten,
                   "unclosed_entries": [r["id"] for r in opened.values()]}


def summary(trips):
    vals = [t["pct"] for t in trips if t["pct"] is not None]
    wins, losses = [v for v in vals if v > 0], [v for v in vals if v < 0]
    n = len(vals)
    mean = statistics.mean(vals) if n else None
    se = statistics.stdev(vals) / n ** .5 if n > 1 else 0
    size = sum(t["size"] for t in trips)
    pnl = sum(t["pnl"] for t in trips)
    return {"n": len(trips), "pnl_krw": round(pnl), "pct_n": n,
            "mean_pct": round(mean, 4) if mean is not None else None,
            "weighted_pct": round(pnl / size * 100, 4) if size else None,
            "winrate": round(len(wins) / n * 100, 1) if n else None,
            "avg_win_pct": round(statistics.mean(wins), 3) if wins else None,
            "avg_loss_pct": round(statistics.mean(losses), 3) if losses else None,
            "t": round(mean / se, 2) if se else None}


def grouped(trips, key):
    groups = defaultdict(list)
    for t in trips:
        groups[key(t)].append(t)
    return {name: summary(ts) for name, ts in sorted(groups.items())}


def live_report(rows, initial, observed, fingerprint=None):
    trips, quality = pair(rows)
    exits = [r for r in rows if r["event"] == "exit"]
    balance, peak, peak_id, peak_ts = initial, initial, 0, None
    for r in sorted(exits, key=lambda r: r["id"]):
        balance += r["pnl_krw"] or 0
        if balance > peak:
            peak, peak_id, peak_ts = balance, r["id"], kst(r["ts"])
    by_strategy = defaultdict(list)
    for r in exits:
        by_strategy[r["strategy"]].append(r)
    if fingerprint is None:
        fingerprint = next((t["ctx"].get("fingerprint") for t in reversed(trips)
                            if t["strategy"] == "breakout" and t["ctx"].get("fingerprint")), None)
    breakout = [t for t in trips if t["strategy"] == "breakout"
                and t["ctx"].get("fingerprint") == fingerprint]
    midpoint = len(breakout) // 2
    sensitivity = {}
    for volume in (5, 5.5, 6):
        def keep(ts):
            return [t for t in ts if (t["ctx"].get("vol_ratio") or 0) >= volume]
        sensitivity[str(volume)] = {"all": summary(keep(breakout)),
                                    "first_half": summary(keep(breakout[:midpoint])),
                                    "second_half": summary(keep(breakout[midpoint:]))}
    return {
        "source": {"rows": len(rows), "first": kst(rows[0]["ts"]),
                   "last": kst(rows[-1]["ts"]), "quality": quality},
        "reconciliation": {"initial_krw": initial, "observed_equity_krw": observed,
                           "seed_plus_logged_exits_krw": round(balance, 2),
                           "unreconciled_difference_krw": round(observed - balance, 2),
                           "note": "No historical account-equity series: log PnL is not account equity."},
        "logged_realized_peak": {"ts": peak_ts, "exit_id": peak_id,
                                 "seed_plus_logged_exits_krw": round(peak, 2)},
        "all_exits_by_strategy": {k: {"n": len(v), "pnl_krw": round(sum(r["pnl_krw"] or 0 for r in v))}
                                 for k, v in sorted(by_strategy.items())},
        "before_logged_peak": grouped([t for t in trips if t["id"] <= peak_id], lambda t: t["strategy"]),
        "after_logged_peak": grouped([t for t in trips if t["id"] > peak_id], lambda t: t["strategy"]),
        "breakout_fingerprint": fingerprint, "breakout": summary(breakout),
        "volume_snapshot_diagnostic": sensitivity,
        "limitations": ["Unmatched/recovered exits contribute to KRW totals, not paired % statistics.",
                        "Entry-snapshot filtering cannot model later entry signals or capital allocation.",
                        "Chronological halves are sensitivity checks, not untouched holdouts.",
                        "Smaller risk budgets reduce gains as well as losses; they do not improve the signal."],
    }


def historical_volume(data_dir):
    """Replay the established close-price model, with fees and capped cached spreads."""
    import pandas as pd
    from backtesting.research.lab_breakout import simulate, MARKETS
    from config import charter as C
    spreads, groups, sources = {}, defaultdict(list), []
    for name in ("spreads.json", "spreads_mid.json"):
        p = data_dir / name
        if p.exists():
            for market, value in json.loads(p.read_text()).items():
                spreads[market] = max(spreads.get(market, 0), value)
    for market in MARKETS:
        path = data_dir / f"{market}_minute5.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if len(df) < 50_000:
            continue
        cap = C.VALIDATED_MARKETS.get(market.split("-")[1], C.MAX_SPREAD_RATIO)
        spread = min(spreads.get(market, cap), cap)
        sources.append({"market": market, "bars": len(df), "first": str(df.index[0]),
                        "last": str(df.index[-1]), "spread": spread})
        split = len(df) // 2
        # Separate halves start flat, each with its own 288-bar warmup.
        for period, part in (("first_half", df.iloc[:split]), ("second_half", df.iloc[split:])):
            for volume in (5, 5.5, 6):
                groups[(period, volume)].extend(simulate(part, spread, n=288, vol_mult=volume, trail=3))
    out = {"sources": sources, "model": "Closed-candle entries and exits; fee 0.1% + capped cached spread. "
           "No live screener, shared capital, order delays or intrabar execution; not a live forecast.",
           "sensitivity": {}}
    for (period, volume), trades in sorted(groups.items()):
        vals = [t["net"] * 100 for t in trades]
        out["sensitivity"][f"{period}:vol={volume}"] = {
            "n": len(vals), "mean_pct": round(statistics.mean(vals), 5) if vals else None,
            "winrate": round(sum(v > 0 for v in vals) / len(vals) * 100, 1) if vals else None}
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--initial-capital", type=float, default=90_000)
    parser.add_argument("--observed-equity", type=float, required=True)
    parser.add_argument("--fingerprint")
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    rows = json.loads(args.ledger.read_text())
    report = live_report(rows, args.initial_capital, args.observed_equity, args.fingerprint)
    if args.data_dir:
        report["historical_volume"] = historical_volume(args.data_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

"""
5분봉 + 15분봉 병행 운용의 실제 계좌 곡선 — 배포할 구성 그대로.

두 전략은 신호 시점이 달라 거래 기회가 늘지만, **같은 코인 중복 보유 금지(§3.3)** 와
**동시 3포지션 한도(§5.5)** 때문에 단순 합산이 되지 않는다. 라이브와 같은 제약을 걸고
합친 계좌 곡선을 낸다.

배포 구성
  rsi2      5분봉  RSI(2)≤3 · ATR≥0.6% · 손절/익절 2.5% · 시간손절 96봉(8h)
  rsi2_15m  15분봉 RSI(2)≤3 · ATR≥1.0% · 손절/익절 3.0% · 시간손절 32봉(8h)

실행: .venv/bin/python backtesting/research/lab_combined.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from config import charter as C
from indicators import ta
from backtesting.research import lab, portfolio
from backtesting.research.data_cache import DATA_DIR
from backtesting.research.fastsim import ExitCfg, Precomp, simulate_arrays, stats_chrono
from backtesting.research.lab_15m import TIME_STOP as TS15, to_15m, trend_1h_on
from backtesting.research.lab_final import SELECTION_START
from backtesting.research.lab_screen import ENTRY_DELAY, LAB_WARMUP

# (이름, 타임프레임, RSI진입, 변동성게이트, 손절, 시간손절봉, 워밍업)
CONFIGS = [
    ("rsi2 (5분)", "minute5", 3.0, 0.006, 0.025, 96, LAB_WARMUP),
    ("rsi2_15m (15분)", "minute15", 3.0, 0.010, 0.030, TS15, 900),
]


def sig(df, th, gate):
    r2 = ta.rsi(df["close"], 2)
    atr_ratio = ta.atr(df) / df["close"]
    enter = ((r2 <= th).fillna(False).to_numpy(bool) & trend_1h_on(df)
             & (atr_ratio >= gate).fillna(False).to_numpy(bool))
    return enter, (r2 >= 70).fillna(False).to_numpy(bool) & ~enter


def collect(panels, med, lo_ts, hi_ts):
    """각 구성의 거래를 모아 (구성별 통계, 전체 PortTrade 목록) 반환."""
    per_cfg, all_pts = {}, []
    for name, tf, th, gate, stop, ts, warm in CONFIGS:
        panel = panels[tf]
        cfg = ExitCfg("pct", stop, 1.0, time_stop_bars=ts, use_exit_signal=True)
        items, pts = [], []
        for m, df in panel.items():
            enter, exit_ = sig(df, th, gate)
            close, high, low = (df[c].to_numpy(float) for c in ("close", "high", "low"))
            atr, open_ = ta.atr(df).to_numpy(float), df["open"].to_numpy(float)
            pre = Precomp(enter, exit_, close, high, low, atr, open_)
            lo = max(warm, int(df.index.searchsorted(lo_ts)))
            hi = int(df.index.searchsorted(hi_ts))
            for t in simulate_arrays(pre, cfg, start=lo, end=hi, slippage=med[m],
                                     entry_delay=ENTRY_DELAY):
                items.append((df.index[t.entry_i], t.pnl))
                pts.append(portfolio.PortTrade(df.index[t.entry_i], df.index[t.exit_i], t.pnl,
                                               cfg.stop_ratio(atr[t.entry_i], close[t.entry_i]), m))
        per_cfg[name] = (stats_chrono(items), pts)
        all_pts += pts
    return per_cfg, all_pts


def main() -> None:
    med = {m: float(v) for m, v in json.loads((DATA_DIR / "spreads.json").read_text()).items()}
    low = {m for m, v in med.items() if v <= C.MAX_SPREAD_RATIO}
    p5 = {m: df for m, df in lab.load_panel("minute5").items() if m in low}
    panels = {"minute5": p5, "minute15": {m: to_15m(df) for m, df in p5.items()}}
    idx = next(iter(p5.values())).index
    start_ts, end_ts = idx[LAB_WARMUP], idx[-1]

    print(f"5분봉 + 15분봉 병행 · {len(p5)}종목 · {start_ts.date()}~{end_ts.date()} · "
          f"동시 {C.MAX_CONCURRENT_POSITIONS}포지션 · 동일코인 중복 금지 · 리스크 "
          f"{C.RISK_PER_TRADE_RATIO:.0%}/거래\n")

    for label, lo, hi in (("홀드아웃(선택 미관여)", start_ts, SELECTION_START),
                          ("전구간 2년", start_ts, end_ts)):
        per_cfg, all_pts = collect(panels, med, lo, hi)
        print(f"── {label} " + "─" * 40)
        print(f"{'구성':20s} {'거래':>6s} {'승률':>6s} {'PF':>5s} {'exp':>8s} {'t':>6s}")
        for name, (s, _) in per_cfg.items():
            pf = "  inf" if s.profit_factor == float("inf") else f"{s.profit_factor:5.2f}"
            print(f"{name:20s} {s.trades:6d} {s.win_rate:6.1%} {pf} {s.exp:+8.3%} {s.t_stat:+6.2f}")
        merged = stats_chrono([(p.entry_ts, p.pnl_ratio) for p in all_pts])
        pf = "  inf" if merged.profit_factor == float("inf") else f"{merged.profit_factor:5.2f}"
        print(f"{'합계(신호 기준)':20s} {merged.trades:6d} {merged.win_rate:6.1%} {pf} "
              f"{merged.exp:+8.3%} {merged.t_stat:+6.2f}")
        r = portfolio.run(all_pts)
        days = (hi - lo).days or 1
        print(f"  계좌: {r.summary()}")
        print(f"  빈도: 체결 {r.taken}건 / {days}일 = 하루 {r.taken/days:.2f}회 "
              f"(신호 {merged.trades}건 중 슬롯·중복으로 {r.skipped_slots}건, "
              f"일손실한도로 {r.skipped_daily_stop}건 스킵)\n")


if __name__ == "__main__":
    main()

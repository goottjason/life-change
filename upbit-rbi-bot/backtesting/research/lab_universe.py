"""
확대된 유니버스 검증 — 새로 편입된 중형 종목에서도 전략이 유효한가.

v1.6에서 추적 풀을 6 → 15로 늘리면서 검증하지 않은 종목(LPT·SUI·ATOM·ENS·AXS·ICP·GAS·KAITO)이
편입될 수 있게 됐다. 원래 검증은 대형 6종목(BTC/ETH/XRP/SOL/LINK/BCH)에서만 했으므로,
**같은 설정을 새 종목에 그대로 적용해** 기댓값이 유지되는지 확인한다.

파라미터는 라이브와 동일하게 고정하고(추가 튜닝 없음 = 이 종목들에는 전 구간이 out-of-sample):
  5분봉  RSI(2)≤3 · ATR≥0.6% · 손절/익절 2.5% · 시간손절 96봉
  15분봉 RSI(2)≤3 · ATR≥1.0% · 손절/익절 3.0% · 시간손절 32봉
비용은 종목별 실측 스프레드(spreads_mid.json) + 수수료 0.1%.

판정: 종목별로 exp > 0 이면 유지, 음수면 블랙리스트(§3 UNIVERSE_BLACKLIST) 후보로 표시.

실행: .venv/bin/python backtesting/research/lab_universe.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from config import charter as C
from indicators import ta
from backtesting.research import data_cache, portfolio
from backtesting.research.data_cache import DATA_DIR
from backtesting.research.fastsim import ExitCfg, Precomp, simulate_arrays, stats_chrono
from backtesting.research.lab_15m import TIME_STOP as TS15, to_15m, trend_1h_on
from backtesting.research.lab_screen import ENTRY_DELAY, LAB_WARMUP

CONFIGS = [("5분", "minute5", 3.0, 0.006, 0.025, 96, LAB_WARMUP),
           ("15분", "minute15", 3.0, 0.010, 0.030, TS15, 900)]


def sig(df, th, gate):
    r2 = ta.rsi(df["close"], 2)
    atr_ratio = ta.atr(df) / df["close"]
    enter = ((r2 <= th).fillna(False).to_numpy(bool) & trend_1h_on(df)
             & (atr_ratio >= gate).fillna(False).to_numpy(bool))
    return enter, (r2 >= 70).fillna(False).to_numpy(bool) & ~enter


def run_market(df, th, gate, stop, ts, warm, slip):
    cfg = ExitCfg("pct", stop, 1.0, time_stop_bars=ts, use_exit_signal=True)
    enter, exit_ = sig(df, th, gate)
    close, high, low = (df[c].to_numpy(float) for c in ("close", "high", "low"))
    atr, open_ = ta.atr(df).to_numpy(float), df["open"].to_numpy(float)
    pre = Precomp(enter, exit_, close, high, low, atr, open_)
    items, pts = [], []
    for t in simulate_arrays(pre, cfg, start=warm, end=len(df), slippage=slip,
                             entry_delay=ENTRY_DELAY):
        items.append((df.index[t.entry_i], t.pnl))
        pts.append(portfolio.PortTrade(df.index[t.entry_i], df.index[t.exit_i], t.pnl,
                                       cfg.stop_ratio(atr[t.entry_i], close[t.entry_i]), "m"))
    return items, pts


def load_spreads() -> dict[str, float]:
    """측정된 스프레드 중앙값을 모두 합친다(spreads.json + spreads_mid.json).

    같은 종목이 양쪽에 있으면 **더 넓은 값**을 쓴다 — 스프레드는 시점에 따라 변하므로
    보수적으로(비용을 크게) 잡는 쪽이 안전하다. 낮은 값을 고르면 유리한 시점만 골라
    검증하는 셈이 된다.
    """
    med: dict[str, float] = {}
    for name in ("spreads.json", "spreads_mid.json"):
        p = DATA_DIR / name
        if not p.exists():
            continue
        for m, v in json.loads(p.read_text()).items():
            med[m] = max(med.get(m, 0.0), float(v))
    return med


def main() -> None:
    med = load_spreads()
    if not med:
        print("스프레드 측정값 없음 — `spread_check.py --sample [mid]` 먼저 실행")
        return
    print("확대 유니버스 검증 · 라이브와 동일 파라미터(추가 튜닝 없음 = 전 구간 OOS)")
    print(f"비용 = 수수료 {C.FEE_ROUNDTRIP:.1%} + 종목별 실측 스프레드 · 다음봉 시가 진입\n")
    print(f"{'종목':8s} {'스프레드':>8s} {'봉':>4s} {'거래':>6s} {'승률':>6s} {'PF':>5s} "
          f"{'exp':>8s} {'t':>6s} {'계좌':>8s}  판정")

    verdict: dict[str, list] = {}
    # 검증 대상: 측정된 스프레드가 상한 이내인 모든 종목(= 유니버스에 들 수 있는 종목)
    targets = sorted(m for m, v in med.items() if v <= C.MAX_SPREAD_RATIO)
    print(f"대상 {len(targets)}종목 (스프레드 중앙값 ≤{C.MAX_SPREAD_RATIO:.1%})\n")
    for m in targets:
        df5 = data_cache.load(m, "minute5")
        if df5 is None or len(df5) < 20_000:
            print(f"{m.replace('KRW-',''):8s} 데이터 부족 — 건너뜀")
            continue
        slip = med.get(m)
        if slip is None:
            print(f"{m.replace('KRW-',''):8s} 스프레드 미측정 — 건너뜀")
            continue
        panels = {"minute5": df5, "minute15": to_15m(df5)}
        for label, tf, th, gate, stop, ts, warm in CONFIGS:
            items, pts = run_market(panels[tf], th, gate, stop, ts, warm, slip)
            s = stats_chrono(items)
            if s.trades == 0:
                print(f"{m.replace('KRW-',''):8s} {slip:8.3%} {label:>4s} {0:6d}   거래 없음")
                continue
            r = portfolio.run(pts) if pts else None
            pf = "  inf" if s.profit_factor == float("inf") else f"{s.profit_factor:5.2f}"
            ok = s.exp > 0
            verdict.setdefault(m, []).append((label, s))
            print(f"{m.replace('KRW-',''):8s} {slip:8.3%} {label:>4s} {s.trades:6d} "
                  f"{s.win_rate:6.1%} {pf} {s.exp:+8.3%} {s.t_stat:+6.2f} "
                  f"{(r.total_return if r else 0):+8.1%}  {'✅' if ok else '❌ 음수'}")

    print("\n── 종목별 판정 " + "─" * 40)
    keep, drop = [], []
    for m, rows in verdict.items():
        total = sum(s.trades for _, s in rows)
        wexp = sum(s.exp * s.trades for _, s in rows) / total if total else 0
        (keep if wexp > 0 else drop).append((m, wexp, total))
    for m, e, n in sorted(keep, key=lambda x: -x[1]):
        print(f"  ✅ {m.replace('KRW-',''):8s} 가중 exp {e:+.3%} ({n}거래) — 유지")
    for m, e, n in sorted(drop, key=lambda x: x[1]):
        print(f"  ❌ {m.replace('KRW-',''):8s} 가중 exp {e:+.3%} ({n}거래) — 블랙리스트 후보")
    if drop:
        syms = ", ".join(f'"{m.split("-")[1]}"' for m, _, _ in drop)
        print(f"\n헌장 §3 적용 예: UNIVERSE_BLACKLIST = {{{syms}}}")
    else:
        print("\n음수 종목 없음 — 확대된 유니버스 유지 가능")


if __name__ == "__main__":
    main()

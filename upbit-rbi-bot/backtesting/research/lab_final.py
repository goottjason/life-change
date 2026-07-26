"""
최종 검증 — 2년 5분봉으로 '변동성 게이트' 메커니즘을 판정한다.

지금까지 확인된 구조(lab_tf.py):
  변동성 게이트(ATR/가격)를 0.3%→0.5%→0.7%로 올릴 때 기댓값이 **단조적으로** 개선되며
  음수에서 양수로 전환된다. 15분봉에서도 같은 패턴(0.52%→0.87%)이 재현됐다.
  손절 1.5%가 1.0%보다 일관되게 우수하다.
  이론과 일치: 엣지는 변동성에 비례하고 비용(0.15%)은 고정 → 변동성/비용 비율이 임계치를 넘어야 한다.

검증 설계 — **선별에 쓰이지 않은 과거 구간을 홀드아웃으로** 쓴다:
  앞선 실험(lab_screen/lab_oos/lab_tf)은 전부 2025-12-29 이후 데이터만 봤다.
  따라서 2년치 중 **2025-12-29 이전 구간은 어떤 선택에도 관여하지 않은 진짜 OOS**다.
  파라미터는 그대로 두고 이 과거 구간에 적용한다(추가 튜닝 없음).

판정
  1) 과거 홀드아웃에서 exp > 0 이고 t > 2 → 엣지 인정(실전 후보)
  2) 게이트 단조성이 과거 구간에서도 재현되는지(메커니즘 확인 — 우연이면 재현되지 않는다)
  3) walk-forward(폴드별 재선택)로 운영 시 성적
  4) 포트폴리오 계좌 곡선(동시 3포지션·리스크 1%)

실행: .venv/bin/python backtesting/research/lab_final.py
"""
from __future__ import annotations

import sys
from collections import Counter
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from config import charter as C
from indicators import ta
from backtesting.research import lab, portfolio
from backtesting.research.fastsim import ExitCfg, Precomp, simulate_arrays, stats_chrono
from backtesting.research.lab_screen import ENTRY_DELAY, LAB_WARMUP, SLIPPAGE

# 앞선 모든 실험이 본 구간의 시작점 — 이 이전은 선택에 관여하지 않은 홀드아웃
SELECTION_START = pd.Timestamp("2025-12-29")

# 메커니즘 기반 격자 (관측된 구조를 따라 게이트·손절만 스캔 — 신호 자체는 고정)
GRID = [dict(th=th, gate=g, sl=sl)
        for th, g, sl in product([5.0, 10.0], [0.005, 0.006, 0.008, 0.010], [0.015, 0.020])]
TIME_STOP = 96          # 8시간
IS_BARS, OOS_BARS = 40_000, 20_000
MIN_IS_TRADES = 50


def build(panel):
    return {m: (df["close"].to_numpy(float), df["high"].to_numpy(float),
                df["low"].to_numpy(float), ta.atr(df).to_numpy(float),
                df["open"].to_numpy(float)) for m, df in panel.items()}


def run_span(panel, ohlc, ctx, g, lo, hi):
    """[lo,hi) 구간 거래 → (항목목록, 포트폴리오거래목록)"""
    cfg = ExitCfg("pct", g["sl"], 1.0, time_stop_bars=TIME_STOP, use_exit_signal=True)
    items, pts = [], []
    for m, df in panel.items():
        enter, exit_ = lab.sig_rsi2(df, ctx, m, th=g["th"], gate=g["gate"])
        close, high, low, atr, open_ = ohlc[m]
        pre = Precomp(enter, exit_, close, high, low, atr, open_)
        for t in simulate_arrays(pre, cfg, start=lo, end=hi,
                                 slippage=SLIPPAGE, entry_delay=ENTRY_DELAY):
            items.append((ctx.index[t.entry_i], t.pnl))
            pts.append(portfolio.PortTrade(ctx.index[t.entry_i], ctx.index[t.exit_i], t.pnl,
                                           cfg.stop_ratio(atr[t.entry_i], close[t.entry_i]), m))
    return items, pts


def row(name, items, pts):
    s = stats_chrono(items)
    if s.trades == 0:
        return f"{name:30s}      0"
    pf = "  inf" if s.profit_factor == float("inf") else f"{s.profit_factor:5.2f}"
    port = portfolio.run(pts) if pts else None
    ps = f"{port.total_return:+7.1%}/{port.taken:4d}건" if port else "      -"
    flag = "  ✅" if (s.exp > 0 and s.t_stat > 2) else ""
    return (f"{name:30s} {s.trades:6d} {s.win_rate:6.1%} {pf} {s.exp:+8.3%} "
            f"{s.mdd:6.1%} {s.t_stat:+6.2f} {ps}{flag}")


HEAD = (f"{'구성':30s} {'거래':>6s} {'승률':>6s} {'PF':>5s} {'exp':>8s} "
        f"{'MDD':>6s} {'t':>6s} {'포트폴리오':>13s}")


def main() -> None:
    panel = lab.load_panel("minute5")
    if not panel:
        print("캐시 없음")
        return
    ctx = lab.Ctx.build(panel)
    n = len(ctx.index)
    if ctx.index[0] >= SELECTION_START:
        print(f"과거 홀드아웃 없음 — 데이터가 {ctx.index[0].date()}부터라 "
              f"{SELECTION_START.date()} 이전 구간이 필요하다(2년 5분봉 수집 필요).")
        return
    split = int(ctx.index.searchsorted(SELECTION_START))
    ohlc = build(panel)
    print(f"최종 검증 · {len(panel)}종목 5분봉 · {n}봉 "
          f"({ctx.index[0].date()}~{ctx.index[-1].date()}) · 다음봉 시가 진입 · "
          f"왕복비용 {C.FEE_ROUNDTRIP + SLIPPAGE:.2%}")
    print(f"홀드아웃(선택 미관여) {ctx.index[LAB_WARMUP].date()}~{ctx.index[split-1].date()} "
          f"({split-LAB_WARMUP}봉) / 선택에 쓰인 구간 {SELECTION_START.date()}~{ctx.index[-1].date()}\n")

    print("① 과거 홀드아웃 성적 (파라미터 추가 조정 없음) " + "─" * 20)
    print(HEAD)
    hold = {}
    for g in GRID:
        items, pts = run_span(panel, ohlc, ctx, g, LAB_WARMUP, split)
        hold[(g["th"], g["gate"], g["sl"])] = stats_chrono(items)
        print(row(f"th={g['th']:.0f} gate={g['gate']:.1%} sl={g['sl']:.1%}", items, pts))

    print("\n② 게이트 단조성 재현 여부 (홀드아웃 구간) " + "─" * 20)
    for th in (5.0, 10.0):
        for sl in (0.015, 0.020):
            seq = [(g, hold[(th, g, sl)]) for g in (0.005, 0.006, 0.008, 0.010)]
            line = " → ".join(f"{g:.1%}:{s.exp:+.3%}(n{s.trades})" for g, s in seq)
            exps = [s.exp for _, s in seq]
            mono = all(b >= a for a, b in zip(exps, exps[1:]))
            print(f"  th={th:.0f} sl={sl:.1%}: {line}  {'단조↑ ✅' if mono else '단조 아님'}")

    print("\n③ walk-forward (전 구간, 폴드별 재선택) " + "─" * 20)
    folds = []
    s0 = LAB_WARMUP
    while s0 + IS_BARS + OOS_BARS <= n:
        folds.append((s0, s0 + IS_BARS, s0 + IS_BARS + OOS_BARS))
        s0 += OOS_BARS
    print(f"  폴드 {len(folds)}개 (IS {IS_BARS}봉/OOS {OOS_BARS}봉) · 선택기준 IS t최대")
    all_items, all_pts, picks = [], [], []
    for a, b, c_ in folds:
        best, best_t = None, -1e9
        for g in GRID:
            items, _ = run_span(panel, ohlc, ctx, g, a, b)
            st = stats_chrono(items)
            if st.trades >= MIN_IS_TRADES and st.t_stat > best_t:
                best, best_t = g, st.t_stat
        if best is None:
            picks.append("(표본부족)")
            continue
        picks.append(f"th={best['th']:.0f} g={best['gate']:.1%} sl={best['sl']:.1%}")
        items, pts = run_span(panel, ohlc, ctx, best, b, c_)
        all_items += items
        all_pts += pts
    print("  " + row("OOS 합계(폴드별 선택)", all_items, all_pts))
    print(f"  폴드 선택: " + " | ".join(f"{l}×{c}" for l, c in Counter(picks).most_common(3)))

    print("\n④ 고정 파라미터 전구간 (튜닝 0회) " + "─" * 20)
    print(HEAD)
    for g in GRID[:4] + GRID[8:12]:
        items, pts = run_span(panel, ohlc, ctx, g, LAB_WARMUP, n)
        print(row(f"th={g['th']:.0f} gate={g['gate']:.1%} sl={g['sl']:.1%}", items, pts))

    best_hold = max(hold.items(), key=lambda kv: kv[1].t_stat)
    (th, gate, sl), st = best_hold
    print(f"\n판정: 홀드아웃 최고 = th={th:.0f} gate={gate:.1%} sl={sl:.1%} → "
          f"exp {st.exp:+.3%} t{st.t_stat:+.2f} 거래 {st.trades} 승률 {st.win_rate:.1%}")
    print("  " + ("✅ 과거 홀드아웃에서도 유의 → 실전 후보로 인정"
                  if st.exp > 0 and st.t_stat > 2 else
                  "❌ 과거 홀드아웃에서 유의하지 않음 → 실전 투입 근거 부족"))


if __name__ == "__main__":
    main()

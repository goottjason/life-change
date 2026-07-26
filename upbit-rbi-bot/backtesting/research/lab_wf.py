"""
5분봉 단타 후보의 walk-forward 검증 (2년 데이터) — 검정력 확보용 최종 판정.

`lab_screen`+`lab_oos`는 단일 분할(70/30)이라 OOS 거래가 100건 수준이었다.
+0.09%/거래 같은 얇은 엣지는 그 표본으로는 통계적으로 판정할 수 없다
(필요 거래수 ≈ (2σ/엣지)² ≈ 240건+). 여기서는 2년치를 여러 폴드로 나눠
**폴드마다 IS에서 파라미터를 고르고 다음 OOS에 적용**, OOS 거래를 전부 합쳐 판정한다.

사전 등록 규칙 (결과를 보기 전 고정)
 - 폴드: IS 40,000봉(≈139일) / OOS 20,000봉(≈69일), OOS 비중복 rolling
 - 선택: 해당 폴드 IS에서 거래 ≥ 50 인 조합 중 **t값 최대**
 - 체결: 신호 다음 봉 시가 진입, 왕복비용 0.15%(수수료 0.1% + 슬리피지 0.05%)
 - 판정: 전 폴드 OOS 합계가 exp > 0 **AND** t > 2.0 이면 실전 후보로 인정
 - 함께 보고: 동시 3포지션·리스크 1% 사이징을 반영한 포트폴리오 계좌 곡선

실행: .venv/bin/python backtesting/research/lab_wf.py [후보이름 ...]
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from config import charter as C
from indicators import ta
from backtesting.research import lab, portfolio
from backtesting.research.fastsim import (ExitCfg, Precomp, simulate_arrays, stats_chrono)
from backtesting.research.lab_screen import EXITS, ENTRY_DELAY, LAB_WARMUP, SLIPPAGE

IS_BARS, OOS_BARS = 40_000, 20_000
MIN_IS_TRADES = 50
PASS_T = 2.0


def main(only: list[str]) -> None:
    panel = lab.load_panel("minute5")
    if not panel:
        print("캐시 없음 — data_cache.py lab2 실행 필요")
        return
    ctx = lab.Ctx.build(panel)
    n = len(ctx.index)
    folds = []
    s = LAB_WARMUP
    while s + IS_BARS + OOS_BARS <= n:
        folds.append((s, s + IS_BARS, s + IS_BARS + OOS_BARS))
        s += OOS_BARS
    if not folds:
        print(f"데이터 {n}봉 — 폴드 1개({IS_BARS + OOS_BARS + LAB_WARMUP}봉)에도 미달. "
              f"2년 5분봉 수집이 끝나야 한다.")
        return

    print(f"5분봉 walk-forward · {len(panel)}종목 · {n}봉 "
          f"({ctx.index[0].date()}~{ctx.index[-1].date()}) · 폴드 {len(folds)}개")
    print(f"IS {IS_BARS}봉/OOS {OOS_BARS}봉 · 다음봉 시가 진입 · 왕복비용 "
          f"{C.FEE_ROUNDTRIP + SLIPPAGE:.2%} · 선택기준 IS t최대(거래≥{MIN_IS_TRADES})")
    print(f"OOS 전체 구간 {ctx.index[folds[0][1]].date()}~{ctx.index[folds[-1][2]-1].date()}\n")

    ohlc = {m: (df["close"].to_numpy(float), df["high"].to_numpy(float),
                df["low"].to_numpy(float), ta.atr(df).to_numpy(float),
                df["open"].to_numpy(float)) for m, df in panel.items()}

    cands = [c for c in lab.CANDIDATES if not only or c.name in only]
    # (fold, cand, combo) -> (IS stats, OOS 거래목록)
    is_stats: dict = {}
    oos_items: dict = {}
    oos_ptrades: dict = {}

    for cand in cands:
        for pi, params in enumerate(cand.params):
            sigs = {m: cand.fn(df, ctx, m, **params) for m, df in panel.items()}
            for ei, cfg0 in enumerate(EXITS):
                cfg = ExitCfg(cfg0.stop_kind, cfg0.stop_val, cfg0.rr,
                              time_stop_bars=cfg0.time_stop_bars,
                              use_exit_signal=cand.has_exit, trail=cfg0.trail)
                for fi, (a, b, c_) in enumerate(folds):
                    is_it, oos_it, pts = [], [], []
                    for m, (enter, exit_) in sigs.items():
                        close, high, low, atr, open_ = ohlc[m]
                        pre = Precomp(enter, exit_, close, high, low, atr, open_)
                        for t in simulate_arrays(pre, cfg, start=a, end=b,
                                                 slippage=SLIPPAGE, entry_delay=ENTRY_DELAY):
                            is_it.append((ctx.index[t.entry_i], t.pnl))
                        for t in simulate_arrays(pre, cfg, start=b, end=c_,
                                                 slippage=SLIPPAGE, entry_delay=ENTRY_DELAY):
                            oos_it.append((ctx.index[t.entry_i], t.pnl))
                            pts.append(portfolio.PortTrade(
                                ctx.index[t.entry_i], ctx.index[t.exit_i], t.pnl,
                                cfg.stop_ratio(atr[t.entry_i], close[t.entry_i]), m))
                    key = (fi, cand.name, (pi, ei))
                    is_stats[key] = stats_chrono(is_it)
                    oos_items[key] = oos_it
                    oos_ptrades[key] = pts

    def label(cand_name: str, combo) -> str:
        cand = next(c for c in lab.CANDIDATES if c.name == cand_name)
        pi, ei = combo
        pstr = ",".join(f"{k}={v}" for k, v in cand.params[pi].items())
        cfg0 = EXITS[ei]
        return f"{pstr} | {cfg0.label()}"

    print(f"{'후보':10s} {'OOS거래':>7s} {'승률':>6s} {'PF':>5s} {'exp':>8s} "
          f"{'MDD':>6s} {'t':>6s}  판정")
    verdicts = []
    for cand in cands:
        items, ptrades, picks = [], [], []
        for fi in range(len(folds)):
            pool = [(k, v) for k, v in is_stats.items() if k[0] == fi and k[1] == cand.name
                    and v.trades >= MIN_IS_TRADES]
            if not pool:
                picks.append("(표본부족)")
                continue
            best_key = max(pool, key=lambda kv: kv[1].t_stat)[0]
            picks.append(label(cand.name, best_key[2]))
            items += oos_items[best_key]
            ptrades += oos_ptrades[best_key]
        s = stats_chrono(items)
        pf = "  inf" if s.profit_factor == float("inf") else f"{s.profit_factor:5.2f}"
        ok = s.exp > 0 and s.t_stat > PASS_T
        print(f"{cand.name:10s} {s.trades:7d} {s.win_rate:6.1%} {pf} {s.exp:+8.3%} "
              f"{s.mdd:6.1%} {s.t_stat:+6.2f}  {'✅ 인정' if ok else '❌ 미달'}")
        if ptrades:
            r = portfolio.run(ptrades)
            print(f"{'':10s} 포트폴리오: {r.summary()}")
        top = Counter(picks).most_common(2)
        print(f"{'':10s} 폴드별 선택: " + " | ".join(f"{l}×{c}" for l, c in top))
        verdicts.append((cand.name, s, ok))

    # 후보 구분 없이 전체 격자에서 폴드마다 최적을 고르는 버전(더 공격적인 튜닝)
    items, ptrades, picks = [], [], []
    for fi in range(len(folds)):
        pool = [(k, v) for k, v in is_stats.items() if k[0] == fi and v.trades >= MIN_IS_TRADES]
        if not pool:
            continue
        best_key = max(pool, key=lambda kv: kv[1].t_stat)[0]
        picks.append(f"{best_key[1]} {label(best_key[1], best_key[2])}")
        items += oos_items[best_key]
        ptrades += oos_ptrades[best_key]
    s = stats_chrono(items)
    print(f"\n전체격자 튜닝(후보까지 폴드마다 교체): 거래 {s.trades} 승률 {s.win_rate:.1%} "
          f"exp {s.exp:+.3%} t{s.t_stat:+.2f} MDD {s.mdd:.1%} "
          f"{'✅ 인정' if s.exp > 0 and s.t_stat > PASS_T else '❌ 미달'}")
    if ptrades:
        print(f"  포트폴리오: {portfolio.run(ptrades).summary()}")
    for p in picks:
        print(f"   폴드 선택: {p}")

    passed = [v for v in verdicts if v[2]]
    print(f"\n판정: {len(passed)}/{len(verdicts)} 후보가 OOS exp>0 & t>{PASS_T} 통과")
    if not passed:
        print("→ 5분봉에서 수수료·스프레드를 넘는 통계적으로 확인된 엣지는 이 후보군에 없다.")


if __name__ == "__main__":
    main([a for a in sys.argv[1:] if not a.startswith("-")])

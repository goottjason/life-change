"""
장기 히스토리 전략×타임프레임 재검증 (in-sample 전수 — '가설 생성'용).

이전 세션은 ~1개월 데이터였다. 여기서는 캐시된 장기 히스토리(1h/4h 약 2년, day 3년+)로
현 헌장 v1.2 파라미터를 다시 측정하고, 연도별로 쪼개 국면(상승/하락/횡보) 의존성을 본다.

주의: 이 표는 in-sample 전수 측정이라 '통과'가 나와도 엣지 증거가 아니다.
      판정은 `walkforward.py`(out-of-sample)로 한다.

실행: .venv/bin/python backtesting/research/sweep_long.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from config import charter as C
from backtesting.research import data_cache
from backtesting.research.fastsim import (Params, Stats, charter_pass, simulate, stats,
                                          stats_chrono)

INTERVALS = ["minute5", "minute15", "minute60", "minute240", "day"]
SPAN = {"minute5": "5분", "minute15": "15분", "minute60": "1시간", "minute240": "4시간", "day": "일"}


def charter_params(strategy: str, **over) -> Params:
    spec = C.STRATEGY_SPECS[strategy]
    return Params(strategy=strategy, atr_mult=spec.atr_stop_mult, rr=spec.rr, **over)


def buy_hold(interval: str) -> tuple[float, str]:
    """같은 구간 단순 보유 수익률(마켓 평균) — 비교 기준선."""
    rets, span = [], ""
    for m in data_cache.MARKETS:
        df = data_cache.load(m, interval)
        if df is None or len(df) < 300:
            continue
        rets.append(float(df["close"].iloc[-1] / df["close"].iloc[0] - 1))
        span = f"{df.index[0].date()}~{df.index[-1].date()}"
    return (float(np.mean(rets)) if rets else 0.0), span


def row(name: str, s: Stats, extra: str = "") -> str:
    pf = "  inf" if s.profit_factor == float("inf") else f"{s.profit_factor:5.2f}"
    flag = "  ✅§11" if charter_pass(s) else ("  +exp" if s.exp > 0 else "")
    return (f"{name:22s} {s.trades:6d} {s.win_rate:6.1%} {pf} {s.exp:+8.3%} "
            f"{s.total:+10.1%} {s.mdd:6.1%} {s.t_stat:+6.2f}{extra}{flag}")


HEAD = f"{'전략/구성':22s} {'거래':>6s} {'승률':>6s} {'PF':>5s} {'exp/거래':>8s} {'누적수익':>10s} {'MDD':>6s} {'t':>6s}"


def main() -> None:
    print(f"장기 히스토리 재검증 · 수수료 {C.FEE_ROUNDTRIP:.1%} · 손절하한 {C.MIN_STOP_RATIO:.0%} "
          f"· 체결모델 intrabar(손절우선) · 신호창 200봉(라이브 동일)")
    print(f"종목 {', '.join(m.replace('KRW-','') for m in data_cache.MARKETS)}\n")

    per_year: dict[tuple[str, int], list[tuple]] = defaultdict(list)

    for interval in INTERVALS:
        bh, span = buy_hold(interval)
        if not span:
            print(f"[{SPAN[interval]}봉] 캐시 없음\n")
            continue
        print(f"── {SPAN[interval]}봉 · {span} · 단순보유(평균) {bh:+.1%} " + "─" * 20)
        print(HEAD)
        for strategy in C.STRATEGY_SPECS:
            base = charter_params(strategy)
            variants = [("현 헌장 v1.2", base),
                        ("+역방향청산 제거", charter_params(strategy, use_reverse=False))]
            for label, p in variants:
                intra: list[tuple] = []
                close_: list[tuple] = []
                for m in data_cache.MARKETS:
                    df = data_cache.load(m, interval)
                    if df is None or len(df) < 300:
                        continue
                    ts = df.index
                    for t in simulate(df, p, exit_mode="intrabar", dfkey=f"{m}_{interval}"):
                        intra.append((ts[t.entry_i], t.pnl))
                        if label == "현 헌장 v1.2" and interval == "minute60":
                            per_year[(strategy, ts[t.entry_i].year)].append((ts[t.entry_i], t.pnl))
                    close_ += [(ts[t.entry_i], t.pnl)
                               for t in simulate(df, p, exit_mode="close", dfkey=f"{m}_{interval}")]
                s = stats_chrono(intra)
                sc = stats_chrono(close_)
                print(row(f"{strategy} {label}", s, extra=f"  종가모델exp {sc.exp:+.3%}"))
        print()

    if per_year:
        print("── 연도별 분해 (1시간봉, 현 헌장 v1.2, intrabar) " + "─" * 20)
        print(f"{'전략/연도':22s} {'거래':>6s} {'승률':>6s} {'PF':>5s} {'exp/거래':>8s} "
              f"{'누적수익':>10s} {'MDD':>6s} {'t':>6s}")
        for (strategy, year) in sorted(per_year):
            print(row(f"{strategy} {year}", stats_chrono(per_year[(strategy, year)])))
        print()

    print("※ in-sample 전수 측정 — 여기서 '+'가 나와도 엣지 증거 아님. 판정은 walkforward.py.")


if __name__ == "__main__":
    main()

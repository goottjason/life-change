"""
fastsim(벡터화) ↔ 전략 클래스(라이브 코드) 신호 일치 검증.

벡터화가 빠르지만, 라이브와 다른 신호를 내면 백테스트 결론 전체가 무의미하다.
매 봉마다 라이브와 동일한 200봉 창을 잘라 `strategy.signal()`을 호출해
fastsim 의 enter/exit 배열과 한 봉씩 비교한다.

실행: .venv/bin/python backtesting/research/verify_fastsim.py
합격 기준: 모든 전략에서 enter/exit 불일치 0.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from bot.trader import build_strategies
from strategies.base import Action
from backtesting.research import data_cache
from backtesting.research.fastsim import (LOOKBACK, Params, signals, simulate,
                                          simulate_naive)

BARS = 1500  # 검증 표본(느린 참조 구현이라 짧게)


def main() -> int:
    df = None
    for interval in ("minute60", "minute15", "minute5"):
        df = data_cache.load("KRW-BTC", interval)
        if df is not None:
            print(f"검증 데이터: KRW-BTC {interval}")
            break
    if df is None:
        print("캐시 없음 — 먼저 data_cache.py 실행")
        return 1
    df = df.iloc[-BARS:].reset_index(drop=False)

    bad = 0
    for name, strat in build_strategies().items():
        p = Params(strategy=name, atr_mult=strat.spec.atr_stop_mult, rr=strat.spec.rr)
        enter, exit_ = signals(df, p)
        mis_e = mis_x = 0
        for i in range(LOOKBACK, len(df)):
            w = df.iloc[i - LOOKBACK + 1: i + 1]          # 라이브가 보는 창과 동일
            act = strat.signal(w).action
            if (act == Action.ENTER_LONG) != bool(enter[i]):
                mis_e += 1
            if (act == Action.EXIT) != bool(exit_[i]):
                mis_x += 1
        n = len(df) - LOOKBACK
        print(f"{name:5s} 검증봉 {n:5d}  enter신호 {int(enter[LOOKBACK:].sum()):5d}  "
              f"exit신호 {int(exit_[LOOKBACK:].sum()):5d}  불일치 enter={mis_e} exit={mis_x}"
              f"{'  ❌' if (mis_e or mis_x) else '  ✅'}")
        bad += mis_e + mis_x

    # 창 한정 ATR vs 전체 ATR 오차 (SL/TP 거리에 영향)
    from indicators import ta
    full = ta.atr(df).to_numpy(float)
    win = np.array([float(ta.atr(df.iloc[i - LOOKBACK + 1: i + 1]).iloc[-1])
                    for i in range(LOOKBACK, len(df), 25)])
    ref = full[LOOKBACK:len(df):25]
    rel = np.abs(win - ref) / ref
    print(f"\nATR(200봉 창) vs ATR(전체) 상대오차: 평균 {rel.mean():.2%} 최대 {rel.max():.2%}")

    # 최적화 시뮬레이터 ↔ 참조 순차 루프 동일성 + 속도
    print()
    full = data_cache.load("KRW-BTC", "minute5")
    if full is None:
        full = df
    for strategy in ("macd", "rsi", "cvd"):
        for mode in ("intrabar", "close"):
            for rev in (True, False):
                p = Params(strategy, 1.5, 2.0, use_reverse=rev)
                t0 = time.time()
                fast = simulate(full, p, exit_mode=mode)
                t_fast = time.time() - t0
                t0 = time.time()
                slow = simulate_naive(full, p, exit_mode=mode)
                t_slow = time.time() - t0
                same = (len(fast) == len(slow) and all(
                    a.entry_i == b.entry_i and a.exit_i == b.exit_i
                    and abs(a.pnl - b.pnl) < 1e-12 and a.reason == b.reason
                    for a, b in zip(fast, slow)))
                if not same:
                    bad += 1
                print(f"{strategy:5s} {mode:8s} rev={str(rev):5s} 거래 {len(fast):5d} "
                      f"{'일치 ✅' if same else '불일치 ❌'}  "
                      f"{t_slow/max(t_fast,1e-9):5.1f}배 빠름({t_fast*1000:.0f}ms)")

    print("\n결과:", "✅ 신호 일치 — fastsim 사용 가능" if bad == 0 else f"❌ 불일치 {bad}건")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

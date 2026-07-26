"""
Walk-forward (out-of-sample) 검증 — 과최적화 방지의 핵심 판정 도구.

절차 (rolling, OOS 구간 비중복):
  [ IS 구간 ]→ 파라미터 그리드 전수 탐색해 거래당 기댓값 최고 조합 선택
  [ OOS 구간 ]→ 그 조합을 **한 번만** 적용해 성적 기록 (이후 IS를 앞으로 밀고 반복)
  마지막에 모든 폴드의 OOS 거래를 합쳐 판정한다.

세 가지를 나란히 본다:
  1) OOS(무튜닝): 현 헌장 v1.2 파라미터 그대로 — 지금 라이브에서 도는 설정의 실제 성적
  2) OOS(튜닝):   폴드마다 IS 최적 파라미터를 골라 다음 구간에 적용 — 튜닝이 유효한지
  3) IS(튜닝):    선택된 파라미터의 IS 성적 — 1·2와의 격차가 곧 과최적화 크기

실행: .venv/bin/python backtesting/research/walkforward.py [interval ...]
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
from backtesting.research import data_cache
from backtesting.research.fastsim import (WARMUP, Params, Stats, charter_pass,
                                          simulate, stats_chrono)

# 폴드 크기 (IS봉, OOS봉) — 타임프레임별로 기간이 비슷하게
FOLDS = {
    "minute5":   (4_000, 2_000),    # IS ≈14일 / OOS ≈7일
    "minute15":  (4_000, 2_000),    # IS ≈42일 / OOS ≈21일
    "minute60":  (3_000, 1_500),    # IS ≈125일 / OOS ≈62일
    "minute240": (1_200,   600),    # IS ≈200일 / OOS ≈100일
    "day":       (  400,   200),    # IS ≈13개월 / OOS ≈7개월
}
SPAN = {"minute5": "5분", "minute15": "15분", "minute60": "1시간", "minute240": "4시간", "day": "일"}

MIN_IS_TRADES = 30          # IS 표본이 이보다 적은 조합은 선택 대상에서 제외(노이즈 방지)
EXIT_MODE = "intrabar"      # 보수적 체결 모델 (손절 우선)


def grid(strategy: str) -> list[Params]:
    """파라미터 그리드. 청산규칙(k, rr, 역방향, 추세필터)은 공통, 신호 파라미터는 전략별."""
    out: list[Params] = []
    common = list(product([True, False], [0, 200]))          # use_reverse, trend_ema
    if strategy == "macd":
        for (f, s, sig), k, rr, (rev, ema) in product(
                [(3, 15, 3), (12, 26, 9), (5, 35, 5)], [1.0, 1.5, 2.5], [1.5, 2.0, 3.0], common):
            out.append(Params("macd", k, rr, use_reverse=rev, trend_ema=ema,
                              macd_fast=f, macd_slow=s, macd_signal=sig))
    elif strategy == "rsi":
        for os_, xl, k, rr, (rev, ema) in product(
                [25.0, 30.0, 35.0], [50.0, 60.0], [1.0, 1.2, 2.0], [1.2, 1.6, 2.5], common):
            out.append(Params("rsi", k, rr, use_reverse=rev, trend_ema=ema,
                              rsi_oversold=os_, rsi_exit=xl))
    else:
        for lb, k, rr, (rev, ema) in product(
                [3, 5, 10], [1.0, 1.3, 2.0], [1.2, 1.7, 2.5], common):
            out.append(Params("cvd", k, rr, use_reverse=rev, trend_ema=ema, cvd_lookback=lb))
    return out


def charter_params(strategy: str) -> Params:
    spec = C.STRATEGY_SPECS[strategy]
    return Params(strategy=strategy, atr_mult=spec.atr_stop_mult, rr=spec.rr)


def load_all(interval: str) -> dict[str, pd.DataFrame]:
    out = {}
    for m in data_cache.MARKETS:
        df = data_cache.load(m, interval)
        if df is not None and len(df) > WARMUP + 300:
            out[m] = df
    return out


def make_folds(ref: pd.DatetimeIndex, is_bars: int, oos_bars: int) -> list[tuple]:
    """기준 종목(BTC) 인덱스로 폴드 경계 타임스탬프 생성 → 모든 종목이 같은 기간을 본다."""
    folds = []
    s = WARMUP
    while s + is_bars + oos_bars <= len(ref):
        folds.append((ref[s], ref[s + is_bars], ref[s + is_bars + oos_bars - 1]))
        s += oos_bars
    return folds


def run_span(dfs: dict[str, pd.DataFrame], interval: str, p: Params,
             t0: pd.Timestamp, t1: pd.Timestamp) -> list[tuple]:
    """[t0, t1] 구간에서 전 종목 합산 거래 — (진입시각, 손익률) 목록."""
    out: list[tuple] = []
    for m, df in dfs.items():
        idx = df.index
        a = int(idx.searchsorted(t0))
        b = int(idx.searchsorted(t1, side="right"))
        if b - a < 50:
            continue
        out += [(idx[t.entry_i], t.pnl)
                for t in simulate(df, p, start=max(a, WARMUP), end=b,
                                  exit_mode=EXIT_MODE, dfkey=f"{m}_{interval}")]
    return out


def buy_hold(dfs: dict[str, pd.DataFrame], t0: pd.Timestamp, t1: pd.Timestamp) -> float:
    rets = []
    for df in dfs.values():
        w = df.loc[t0:t1]
        if len(w) > 10:
            rets.append(float(w["close"].iloc[-1] / w["close"].iloc[0] - 1))
    return float(np.mean(rets)) if rets else 0.0


HEAD = (f"{'구성':24s} {'거래':>6s} {'승률':>6s} {'PF':>5s} {'exp/거래':>9s} "
        f"{'누적수익':>10s} {'MDD':>6s} {'t':>6s}")


def line(name: str, s: Stats) -> str:
    pf = "  inf" if s.profit_factor == float("inf") else f"{s.profit_factor:5.2f}"
    flag = "  ✅§11통과" if charter_pass(s) else ("  +exp" if s.exp > 0 else "")
    return (f"{name:24s} {s.trades:6d} {s.win_rate:6.1%} {pf} {s.exp:+9.3%} "
            f"{s.total:+10.1%} {s.mdd:6.1%} {s.t_stat:+6.2f}{flag}")


def main(intervals: list[str]) -> None:
    print(f"Walk-forward 검증 · 수수료 {C.FEE_ROUNDTRIP:.1%} · 체결 {EXIT_MODE}(손절우선) "
          f"· 신호창 {WARMUP}봉 · IS최소거래 {MIN_IS_TRADES}")
    print(f"종목 {', '.join(m.replace('KRW-', '') for m in data_cache.MARKETS)}")
    print(f"판정: OOS(무튜닝/튜닝) 기댓값이 0 초과 + 헌장 §11(승률≥{C.BACKTEST_MIN_WINRATE:.0%}, "
          f"PF≥{C.BACKTEST_MIN_PROFIT_FACTOR}, MDD≤{C.BACKTEST_MAX_DRAWDOWN:.0%}, "
          f"거래≥{C.BACKTEST_MIN_TRADES}) 충족\n")

    verdicts = []
    for interval in intervals:
        dfs = load_all(interval)
        if len(dfs) < 2:
            print(f"[{SPAN.get(interval, interval)}봉] 캐시 부족 — data_cache.py 실행 필요\n")
            continue
        is_bars, oos_bars = FOLDS[interval]
        ref = dfs[data_cache.MARKETS[0]].index
        folds = make_folds(ref, is_bars, oos_bars)
        if not folds:
            print(f"[{SPAN.get(interval, interval)}봉] 데이터가 폴드 1개에도 못 미침\n")
            continue
        oos_span = (folds[0][1].date(), folds[-1][2].date())
        print(f"── {SPAN.get(interval, interval)}봉 · 폴드 {len(folds)}개 "
              f"(IS {is_bars}봉/OOS {oos_bars}봉) · OOS 구간 {oos_span[0]}~{oos_span[1]} "
              f"· 단순보유 {buy_hold(dfs, folds[0][1], folds[-1][2]):+.1%} " + "─" * 12)
        print(HEAD)

        for strategy in C.STRATEGY_SPECS:
            g = grid(strategy)
            default = charter_params(strategy)
            oos_default: list[tuple] = []
            oos_tuned: list[tuple] = []
            is_tuned: list[tuple] = []
            picked: list[str] = []

            for is_t0, split_t, oos_t1 in folds:
                # 1) 무튜닝(현 헌장) OOS
                oos_default += run_span(dfs, interval, default, split_t, oos_t1)

                # 2) IS 전수 탐색 → 최적 조합
                best, best_exp, best_pnls = None, -1e9, []
                for p in g:
                    items = run_span(dfs, interval, p, is_t0, split_t)
                    if len(items) < MIN_IS_TRADES:
                        continue
                    e = float(np.mean([v for _, v in items]))
                    if e > best_exp:
                        best, best_exp, best_pnls = p, e, items
                if best is None:                     # IS 표본 부족 → 이 폴드는 튜닝 불가
                    picked.append("(표본부족)")
                    continue
                picked.append(best.label())
                is_tuned += best_pnls
                # 3) 선택 조합을 다음 구간(OOS)에 적용
                oos_tuned += run_span(dfs, interval, best, split_t, oos_t1)

            s_def = stats_chrono(oos_default)
            s_oos = stats_chrono(oos_tuned)
            s_is = stats_chrono(is_tuned)
            print(line(f"{strategy} OOS(무튜닝 헌장)", s_def))
            print(line(f"{strategy} OOS(폴드별 튜닝)", s_oos))
            print(line(f"{strategy} IS(튜닝, 참고)", s_is))
            top = Counter(picked).most_common(3)
            print(f"{'':24s} IS 선택 파라미터: " +
                  " | ".join(f"{lbl}×{n}" for lbl, n in top))
            verdicts.append((interval, strategy, s_def, s_oos, s_is))
        print()

    if verdicts:
        print("── 종합 판정 " + "─" * 40)
        ok = [v for v in verdicts if v[3].exp > 0 and charter_pass(v[3])]
        pos = [v for v in verdicts if v[3].exp > 0 or v[2].exp > 0]
        print(f"검증 조합 {len(verdicts)}개 중 OOS 양의 기댓값 {len(pos)}개, "
              f"헌장 §11 통과 {len(ok)}개")
        for interval, strategy, s_def, s_oos, s_is in verdicts:
            if s_oos.exp > 0 or s_def.exp > 0:
                print(f"  · {SPAN.get(interval, interval)}봉 {strategy}: "
                      f"무튜닝 exp {s_def.exp:+.3%}(t{s_def.t_stat:+.1f}) / "
                      f"튜닝 exp {s_oos.exp:+.3%}(t{s_oos.t_stat:+.1f}, 거래{s_oos.trades})")
        if not ok:
            print("  → 헌장 §11 기준 통과 조합 없음: 현 신호 체계는 실전 부적합(§11).")
        print("\n과최적화 크기 = IS(튜닝) − OOS(튜닝) 기댓값 격차:")
        for interval, strategy, s_def, s_oos, s_is in verdicts:
            print(f"  · {SPAN.get(interval, interval):3s}봉 {strategy:5s} "
                  f"IS {s_is.exp:+.3%} → OOS {s_oos.exp:+.3%} "
                  f"(격차 {s_is.exp - s_oos.exp:+.3%})")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    main(args or list(FOLDS))

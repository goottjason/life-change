"""
살아남은 단 하나의 후보를 회의적으로 검증한다.

후보: **일봉 MACD(3/15/3) + BTC가 EMA200 위일 때만 진입** (헌장 k=1.5, rr=2.0)
 - `regime_split.py` 에서 exp +0.771%/거래, 387거래, t=+2.11 (2023-04~2026-07)로 유일하게 유의해 보였다.
 - 그러나 이 버킷은 여러 조합(전략4×TF4×버킷6 ≈ 100회 비교) 중에서 사후 선택된 것이다.
   다중비교 때문에 우연히 t>2 가 나올 수 있어, 아래 네 가지를 모두 통과해야 후보로 인정한다.

검증 항목
 1) 시간 분할: 전반기 / 후반기 — 한쪽만 벌었으면 시대 의존(=엣지 아님)
 2) 종목 분할: 엣지가 한 종목에 몰려 있는지
 3) 벤치마크: 같은 노출(BTC 상승장 구간만 보유)의 단순보유 대비 초과수익이 있는지
 4) 헌장 §11 기준 충족 여부

BTC EMA200 게이트는 진입 시점에 계산 가능한 인과적 필터다(lookahead 없음).

실행: .venv/bin/python backtesting/research/candidate_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from config import charter as C
from indicators import ta
from backtesting.research import data_cache
from backtesting.research.fastsim import (WARMUP, Params, charter_pass, simulate, stats,
                                          stats_chrono)

INTERVAL = "day"
CANDIDATE = Params("macd", atr_mult=1.5, rr=2.0)          # 헌장 v1.2 기본값 그대로


def btc_bull_mask(interval: str) -> pd.Series:
    btc = data_cache.load("KRW-BTC", interval)
    return (btc["close"] > ta.ema(btc["close"], 200))


def aligned_mask(bull: pd.Series, idx: pd.DatetimeIndex) -> np.ndarray:
    """BTC 게이트를 대상 종목 인덱스에 정렬(직전 값 사용 — 미래 정보 없음)."""
    return bull.reindex(idx, method="ffill").fillna(False).to_numpy(dtype=bool)


def line(name: str, items: list[tuple], bench: float | None = None) -> str:
    s = stats_chrono(items)
    if s.trades == 0:
        return f"{name:26s}      0"
    pf = "  inf" if s.profit_factor == float("inf") else f"{s.profit_factor:5.2f}"
    flag = "  ✅§11" if charter_pass(s) else ("  +exp" if s.exp > 0 else "")
    b = f" {bench:+9.1%}" if bench is not None else ""
    return (f"{name:26s} {s.trades:6d} {s.win_rate:6.1%} {pf} {s.exp:+8.3%} "
            f"{s.total:+10.1%}{b} {s.mdd:6.1%} {s.t_stat:+6.2f}{flag}")


HEAD = (f"{'구성':26s} {'거래':>6s} {'승률':>6s} {'PF':>5s} {'exp/거래':>8s} "
        f"{'누적수익':>10s} {'단순보유':>9s} {'MDD':>6s} {'t':>6s}")


def bull_only_hold(df: pd.DataFrame, mask: np.ndarray, lo: int, hi: int) -> float:
    """같은 노출 벤치마크: BTC 상승장 구간에만 보유한 복리 수익률(수수료 무시 — 후보에 불리하게)."""
    r = df["close"].pct_change().to_numpy(dtype=float)
    seg = np.nan_to_num(r[lo:hi]) * mask[lo:hi]
    return float(np.prod(1 + seg) - 1)


def main() -> None:
    bull = btc_bull_mask(INTERVAL)
    print(f"후보 검증 · {INTERVAL} · MACD(3/15/3) k=1.5 rr=2.0 + BTC EMA200 상승장 게이트 "
          f"· 수수료 {C.FEE_ROUNDTRIP:.1%} · intrabar\n")

    per_market: dict[str, list[tuple]] = {}
    halves: dict[str, list[tuple]] = {"전반기": [], "후반기": []}
    all_pnls: list[tuple] = []
    bench_all, bench_h = [], {"전반기": [], "후반기": []}
    spans = {}

    for m in data_cache.MARKETS:
        df = data_cache.load(m, INTERVAL)
        if df is None or len(df) < 400:
            continue
        mask = aligned_mask(bull, df.index)
        trades = simulate(df, CANDIDATE, dfkey=f"{m}_{INTERVAL}", entry_mask=mask)
        per_market[m] = [(df.index[t.entry_i], t.pnl) for t in trades]
        all_pnls += per_market[m]
        mid = (len(df) + WARMUP) // 2
        spans = {"전반기": (df.index[WARMUP].date(), df.index[mid].date()),
                 "후반기": (df.index[mid].date(), df.index[-1].date())}
        for t in trades:
            halves["전반기" if t.entry_i < mid else "후반기"].append((df.index[t.entry_i], t.pnl))
        bench_all.append(bull_only_hold(df, mask, WARMUP, len(df)))
        bench_h["전반기"].append(bull_only_hold(df, mask, WARMUP, mid))
        bench_h["후반기"].append(bull_only_hold(df, mask, mid, len(df)))

    print(HEAD)
    print(line("후보 전체", all_pnls, float(np.mean(bench_all))))
    print()
    for k in ("전반기", "후반기"):
        print(line(f"{k} {spans[k][0]}~{spans[k][1]}", halves[k], float(np.mean(bench_h[k]))))
    print()
    for m, pnls in per_market.items():
        print(line(f"종목 {m.replace('KRW-', '')}", pnls))

    s = stats_chrono(all_pnls)
    s1, s2 = stats_chrono(halves["전반기"]), stats_chrono(halves["후반기"])
    pos_markets = sum(1 for p in per_market.values() if stats_chrono(p).exp > 0)
    print("\n── 판정 " + "─" * 50)
    print(f"1) 시간 안정성: 전반 {s1.exp:+.3%}(t{s1.t_stat:+.2f}) / 후반 {s2.exp:+.3%}(t{s2.t_stat:+.2f})"
          f" → {'양쪽 +' if s1.exp > 0 and s2.exp > 0 else '❌ 한쪽 이상 음수 = 시대 의존'}")
    print(f"2) 종목 안정성: {pos_markets}/{len(per_market)} 종목에서 양의 기댓값"
          f" → {'❌ 소수 종목 편중' if pos_markets <= len(per_market)//2 else '분산됨'}")
    print(f"3) 벤치마크: 후보 누적 {s.total:+.1%} vs 같은노출 단순보유 {np.mean(bench_all):+.1%}"
          f" → {'초과' if s.total > np.mean(bench_all) else '❌ 열위(보유가 더 나음)'}")
    print(f"4) 헌장 §11: 거래{s.trades}(≥{C.BACKTEST_MIN_TRADES}) 승률{s.win_rate:.1%}"
          f"(≥{C.BACKTEST_MIN_WINRATE:.0%}) PF{s.profit_factor:.2f}(≥{C.BACKTEST_MIN_PROFIT_FACTOR})"
          f" MDD{s.mdd:.1%}(≤{C.BACKTEST_MAX_DRAWDOWN:.0%})"
          f" → {'✅ 통과' if charter_pass(s) else '❌ 불통과'}")


if __name__ == "__main__":
    main()

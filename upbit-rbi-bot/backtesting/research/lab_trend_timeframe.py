"""
추세 필터 타임프레임 — 5분봉과 15분봉이 **같은 1시간봉**을 보는 게 맞는가.

## 문제 제기
현재 두 전략 모두 `trend_up_from_hourly()` 로 **1시간봉 EMA200** 위인지만 본다. 그런데
매매 봉 기준으로 환산하면 두 전략이 보는 '추세의 길이'가 전혀 다르다:

    5분봉  전략 : 1시간봉 EMA200 = 200시간 = **2,400개 매매봉**  (배율 12배)
    15분봉 전략 : 1시간봉 EMA200 = 200시간 =   **800개 매매봉**  (배율 4배)

즉 5분봉 전략은 자기 봉 기준으로 3배나 긴 추세를 본다. 통상적인 멀티 타임프레임 관례
(상위 봉 = 매매 봉의 4~6배)로 보면 5분봉의 짝은 30분봉(6배)이지 1시간봉(12배)이 아니다.

## 두 가지 해석 — 어느 쪽이 옳은지는 논리가 아니라 데이터가 정한다
  (A) **시계(wall-clock) 기준이 맞다**: "지금 시장이 상승 국면인가"는 내가 몇 분봉을
      보든 상관없는 시장의 성질이다. 두 전략이 같은 국면 정의를 공유하는 게 자연스럽다.
  (B) **봉 배율 기준이 맞다**: 매매 주기가 짧으면 추세도 짧게 봐야 한다. 12배는 과하게
      느려서, 이미 꺾인 단기 흐름을 '아직 상승'으로 잘못 읽는다.

## 사전 등록 (돌리기 전에 고정)
각 전략에 대해 추세 판정 봉을 바꿔가며 전부 보고한다. 청산·진입선·게이트는 전부 고정.
  5분봉  : 15분 / 30분 / **1시간(현행)** / 2시간 / 4시간
  15분봉 : 30분 / 1시간(현행) / 2시간 / 4시간 / 1일
EMA 길이는 200 고정(=그 봉 기준 200개). 홀드아웃 분리는 하지 않는다 — **현행(1시간)을
기준선으로 두고 '유의미하게 더 나은 것이 있는가'만 본다.** 없으면 현행 유지가 결론이다.
비슷비슷하면 그것 자체가 답이다: "이 선택은 결과를 좌우하지 않는다".

실행: .venv/bin/python backtesting/research/lab_trend_timeframe.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from config import charter as C
from indicators import ta
from backtesting.research.data_cache import load
from backtesting.research import lab_easy_teaching as L
from backtesting.research.lab_exit_timing import stats

WARM = 300
EXIT_LEVEL = 70.0
TREND_TFS = {
    "5min":  ["15min", "30min", "1h", "2h", "4h"],
    "15min": ["30min", "1h", "2h", "4h", "1D"],
}
CURRENT = "1h"


def run(sym: str, tf: str, spec, trend_tf: str) -> list[float]:
    d5 = load(f"KRW-{sym}", "minute5")
    if d5 is None:
        return []
    df = L.resample(d5, tf)
    if len(df) < WARM + 200:
        return []
    o, h = df["open"].to_numpy(), df["high"].to_numpy()
    lo, cl = df["low"].to_numpy(), df["close"].to_numpy()
    rsi2 = ta.rsi(df["close"], 2).to_numpy()
    atr_ratio = (ta.atr(df) / df["close"]).to_numpy()

    # 추세: 지정한 봉의 EMA200 위인가 (라이브와 동일하게 **직전 완성봉** 기준)
    htf = df["close"].resample(trend_tf).last().dropna()
    if len(htf) < 210:
        return []
    up = (htf > ta.ema(htf, 200)).shift(1).reindex(df.index, method="ffill")
    up = up.astype(float).fillna(0.0).to_numpy(bool)

    cost = L.cost_ratio(f"KRW-{sym}")
    tstop = C.time_stop_bars_for(spec)
    out: list[float] = []
    pos = None
    for i in range(WARM, len(df) - 1):
        if pos is not None:
            px = None
            if lo[i] <= pos["sl"]:
                px = pos["sl"]
            elif h[i] >= pos["tp"]:
                px = pos["tp"]
            elif rsi2[i] >= EXIT_LEVEL:
                px = cl[i]
            elif i - pos["bar"] >= tstop:
                px = o[i]
            if px is not None:
                out.append(((px / pos["e"] - 1) - cost) * 100)
                pos = None
        if pos is not None:
            continue
        if not (rsi2[i] <= spec.entry_level and up[i] and atr_ratio[i] >= spec.min_atr_ratio):
            continue
        e = o[i + 1]
        pos = {"e": e, "sl": e * (1 - spec.stop_pct),
               "tp": e * (1 + spec.stop_pct * spec.rr), "bar": i + 1}
    return out


BAR_MIN = {"5min": 5, "15min": 15}
TF_MIN = {"15min": 15, "30min": 30, "1h": 60, "2h": 120, "4h": 240, "1D": 1440}


def main() -> None:
    for name, tf in (("rsi2", "5min"), ("rsi2_15m", "15min")):
        spec = C.STRATEGY_SPECS[name]
        print(f"\n{'='*80}\n{name} ({tf}) · 진입선 {spec.entry_level} · 게이트 {spec.min_atr_ratio:.2%}")
        print(f"{'추세 봉':10} {'배율':>6} {'추세길이':>9} {'거래':>6} {'승률%':>6} {'PF':>5} "
              f"{'거래당%':>8} {'t':>6} {'MDD%p':>7}")
        base = None
        for ttf in TREND_TFS[tf]:
            v = []
            for sym in C.VALIDATED_MARKETS:
                if sym in C.STRATEGY_BLACKLIST.get(name, set()):
                    continue
                v += run(sym, tf, spec, ttf)
            s = stats(v)
            if not s["n"]:
                print(f"{ttf:10} 데이터 부족"); continue
            mult = TF_MIN[ttf] / BAR_MIN[tf]
            days = TF_MIN[ttf] * 200 / 60 / 24
            cur = " ← 현행" if ttf == CURRENT else ""
            if ttf == CURRENT:
                base = s
            print(f"{ttf:10} {mult:5.0f}x {days:8.1f}일 {s['n']:6d} {s['wr']:6.1f} {s['pf']:5.2f} "
                  f"{s['exp']:+8.3f} {s['t']:+6.2f} {s['mdd']:7.1f}{cur}")
        if base:
            print(f"  기준선(1시간) 거래당 {base['exp']:+.3f}% · t {base['t']:+.2f}")
    print("\n해석: 현행(1시간)이 최선이 아니더라도 차이가 작고 t가 비슷하면 "
          "'이 선택은 결과를 좌우하지 않는다'가 결론이다. 큰 폭으로 더 나은 봉이 있어야 개정 근거가 된다.")


if __name__ == "__main__":
    main()

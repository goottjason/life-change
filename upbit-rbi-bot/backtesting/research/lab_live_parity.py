"""
라이브 전략 클래스 ↔ 리서치 신호 일치 검증 (배포 전 마지막 관문).

'검증한 것'과 '배포하는 것'이 다르면 백테스트 성적은 아무 의미가 없다.
여기서는 실제 라이브 코드(`strategies/rsi2_pullback.Rsi2PullbackStrategy`)를
라이브와 동일한 조건 — **200봉 창** + ctx["trend_up"](1시간봉 EMA200) — 으로 호출해,
리서치에서 쓴 신호 배열과 한 봉씩 비교한다.

합격 기준: 진입/청산 신호 불일치 0.

실행: .venv/bin/python backtesting/research/lab_live_parity.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from config.charter import STRATEGY_SPECS
from strategies.base import Action
from strategies.rsi2_pullback import Rsi2PullbackStrategy, trend_up_from_hourly
from backtesting.research import data_cache, lab
from backtesting.research.fastsim import LOOKBACK
from backtesting.research.lab_live_variant import signals, trend_1h

BARS = 4_000          # 검증 표본(라이브와 같은 창 크기로 매 봉 호출 → 느리므로 제한)
MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]


def main() -> int:
    strat = Rsi2PullbackStrategy(STRATEGY_SPECS["rsi2"])
    total_bad = 0
    print("라이브 전략 클래스 ↔ 리서치 신호 일치 검증")
    print(f"창 {LOOKBACK}봉(라이브와 동일) · ctx['trend_up'] = 1시간봉 EMA200\n")
    print(f"{'종목':9s} {'검증봉':>7s} {'진입신호':>8s} {'청산신호':>8s} {'불일치':>7s}")
    for m in MARKETS:
        df = data_cache.load(m, "minute5")
        if df is None:
            print(f"{m:9s} 캐시 없음")
            continue
        df = df.iloc[-BARS:]
        enter, exit_ = signals(df, m, trend_1h)      # 리서치 기준 신호
        up = trend_1h(df)
        mis_e = mis_x = 0
        for i in range(LOOKBACK, len(df)):
            window = df.iloc[i - LOOKBACK + 1: i + 1]
            act = strat.signal(window, {"trend_up": bool(up[i])}).action
            if (act == Action.ENTER_LONG) != bool(enter[i]):
                mis_e += 1
            if (act == Action.EXIT) != bool(exit_[i]):
                mis_x += 1
        bad = mis_e + mis_x
        total_bad += bad
        print(f"{m:9s} {len(df) - LOOKBACK:7d} {int(enter[LOOKBACK:].sum()):8d} "
              f"{int(exit_[LOOKBACK:].sum()):8d} {bad:7d} {'✅' if bad == 0 else '❌'}")

    # ctx 를 주지 않으면(1시간봉 조회 실패) 진입하지 않는지 — 안전 기본값 확인
    df = data_cache.load("KRW-BTC", "minute5")
    if df is not None:
        w = df.iloc[-LOOKBACK:]
        assert strat.signal(w, None).action != Action.ENTER_LONG
        assert strat.signal(w, {}).action != Action.ENTER_LONG
        print("\n추세 정보 없을 때 진입 안 함: ✅")

    # 1시간봉 헬퍼가 리샘플 기준과 같은 판정을 내는지 (라이브는 minute60 캔들을 직접 받는다)
    df5 = data_cache.load("KRW-BTC", "minute5")
    if df5 is not None:
        hourly = df5.resample("1h").agg({"open": "first", "high": "max", "low": "min",
                                         "close": "last", "volume": "sum"}).dropna()
        live_call = trend_up_from_hourly(hourly)
        research = bool(trend_1h(df5)[-1])
        print(f"1시간봉 추세 판정 일치: 라이브 {live_call} / 리서치 {research} "
              f"{'✅' if live_call == research else '❌'}")
        total_bad += 0 if live_call == research else 1

    print("\n결과:", "✅ 라이브 코드가 검증된 신호와 동일" if total_bad == 0
          else f"❌ 불일치 {total_bad}건 — 배포 금지")
    return 0 if total_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""
라이브 전략 클래스 ↔ 리서치 신호 일치 검증 (배포 전 마지막 관문).

'검증한 것'과 '배포하는 것'이 다르면 백테스트 성적은 아무 의미가 없다.
여기서는 실제 라이브 코드(`strategies/rsi2_pullback.Rsi2PullbackStrategy`)를
라이브와 동일한 조건 — **200봉 창** + ctx["trend_up"](1시간봉 EMA200) — 으로 호출해,
리서치에서 쓴 신호 배열과 한 봉씩 비교한다.

v2.3부터 **두 전략을 모두** 검사한다:
  · rsi2      (5분봉)  ↔ lab_live_variant.signals   (진입선 3 · 게이트 0.6%)
  · rsi2_15m  (15분봉) ↔ lab_15m.signals            (진입선 7 · 게이트 0.83%)
진입선/게이트는 **헌장 스펙에서 읽어** 리서치 구현에 그대로 넘긴다 — 상수를 손으로 맞추면
스펙만 바뀌고 검증은 옛 값으로 돌아가는 사고가 나기 때문이다(5분봉은 리서치 모듈 상수가
스펙과 같은지 먼저 확인한다).

합격 기준: 진입/청산 신호 불일치 0.

실행: .venv/bin/python backtesting/research/lab_live_parity.py   (약 5분 — 매 봉 재계산)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.charter import STRATEGY_SPECS
from strategies.base import Action
from strategies.rsi2_pullback import Rsi2PullbackStrategy, trend_up_from_hourly
from backtesting.research import data_cache, lab_15m
from backtesting.research.fastsim import LOOKBACK
from backtesting.research import lab_live_variant
from backtesting.research.lab_live_variant import signals, trend_1h

# 검증 표본(라이브와 같은 창 크기로 매 봉 호출 → 느리므로 제한).
# 표본을 잡을 때는 **진입 신호가 실제로 포함되는 길이**가 중요하다 — 진입이 0건인 구간만
# 비교하면 '둘 다 진입 안 함'만 확인되고 진입 경로의 일치는 검증되지 않는다.
# 그래서 두 타임프레임 모두 캐시 전 구간(2년)을 쓰고, 아래 리포트에 진입 비교 건수를 찍는다.
BARS_5M = 60_000       # 5분봉 ≈208일
BARS_15M = 70_000      # 15분봉 = 2년 5분봉 리샘플 전량(≈70k봉)
MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]


def compare(strat, df, enter, exit_, up) -> int:
    """라이브 클래스를 200봉 창으로 매 봉 호출해 리서치 신호와 비교. 반환: 불일치 건수."""
    mis = 0
    for i in range(LOOKBACK, len(df)):
        window = df.iloc[i - LOOKBACK + 1: i + 1]
        act = strat.signal(window, {"trend_up": bool(up[i])}).action
        if (act == Action.ENTER_LONG) != bool(enter[i]):
            mis += 1
        if (act == Action.EXIT) != bool(exit_[i]):
            mis += 1
    return mis


def check_5m() -> int:
    """rsi2(5분봉) ↔ lab_live_variant. 리서치 모듈 상수가 스펙과 같은지 먼저 확인한다."""
    spec = STRATEGY_SPECS["rsi2"]
    print(f"[rsi2 · 5분봉] 진입선 {spec.entry_level:.0f} · 게이트 {spec.min_atr_ratio:.2%}")
    if (lab_live_variant.TH, lab_live_variant.GATE) != (spec.entry_level, spec.min_atr_ratio):
        print(f"  ❌ 리서치 상수(th={lab_live_variant.TH}, gate={lab_live_variant.GATE})가 "
              f"스펙(th={spec.entry_level}, gate={spec.min_atr_ratio})과 다르다 — 검증 대상 불일치")
        return 1
    strat = Rsi2PullbackStrategy(spec)
    bad = 0
    print(f"{'종목':9s} {'검증봉':>7s} {'진입신호':>8s} {'청산신호':>8s} {'불일치':>7s}")
    for m in MARKETS:
        df = data_cache.load(m, "minute5")
        if df is None:
            print(f"{m:9s} 캐시 없음")
            continue
        df = df.iloc[-BARS_5M:]
        enter, exit_ = signals(df, m, trend_1h)      # 리서치 기준 신호
        mis = compare(strat, df, enter, exit_, trend_1h(df))
        bad += mis
        print(f"{m:9s} {len(df) - LOOKBACK:7d} {int(enter[LOOKBACK:].sum()):8d} "
              f"{int(exit_[LOOKBACK:].sum()):8d} {mis:7d} {'✅' if mis == 0 else '❌'}")
    return bad


def check_15m() -> int:
    """rsi2_15m(15분봉) ↔ lab_15m.signals. 진입선/게이트를 스펙에서 읽어 넘긴다 (v2.3)."""
    spec = STRATEGY_SPECS["rsi2_15m"]
    th, gate = spec.entry_level, spec.min_atr_ratio
    print(f"\n[rsi2_15m · 15분봉] 진입선 {th:.0f} · 게이트 {gate:.2%}")
    strat = Rsi2PullbackStrategy(spec)
    bad = 0
    print(f"{'종목':9s} {'검증봉':>7s} {'진입신호':>8s} {'청산신호':>8s} {'불일치':>7s}")
    for m in MARKETS:
        df5 = data_cache.load(m, "minute5")
        if df5 is None:
            print(f"{m:9s} 캐시 없음")
            continue
        df = lab_15m.to_15m(df5).iloc[-BARS_15M:]    # 업비트 15분봉과 동일한 집계
        enter, exit_ = lab_15m.signals(df, th, gate)
        mis = compare(strat, df, enter, exit_, lab_15m.trend_1h_on(df))
        bad += mis
        print(f"{m:9s} {len(df) - LOOKBACK:7d} {int(enter[LOOKBACK:].sum()):8d} "
              f"{int(exit_[LOOKBACK:].sum()):8d} {mis:7d} {'✅' if mis == 0 else '❌'}")
    return bad


def check_shared() -> int:
    """안전 기본값 + 1시간봉 헬퍼 일치 + 두 전략의 진입선 분리 확인."""
    bad = 0
    strat = Rsi2PullbackStrategy(STRATEGY_SPECS["rsi2"])

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
        bad += 0 if live_call == research else 1

    # 진입선이 전략별로 분리돼 있는지 (v2.3 — 공용 상수로 되돌아가면 15분봉이 조용해진다)
    e5 = STRATEGY_SPECS["rsi2"].entry_level
    e15 = STRATEGY_SPECS["rsi2_15m"].entry_level
    ok = (e5, e15) == (3.0, 7.0)
    print(f"전략별 진입선 분리: 5분 {e5:.0f} / 15분 {e15:.0f} {'✅' if ok else '❌'}")
    bad += 0 if ok else 1
    return bad


def main() -> int:
    print("라이브 전략 클래스 ↔ 리서치 신호 일치 검증")
    print(f"창 {LOOKBACK}봉(라이브와 동일) · ctx['trend_up'] = 1시간봉 EMA200\n")
    total_bad = check_5m() + check_15m() + check_shared()
    print("\n결과:", "✅ 라이브 코드가 검증된 신호와 동일" if total_bad == 0
          else f"❌ 불일치 {total_bad}건 — 배포 금지")
    return 0 if total_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

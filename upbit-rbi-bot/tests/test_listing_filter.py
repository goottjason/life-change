"""
상장 경과일 필터 테스트 (헌장 §3.2-b, v1.5 → v2.4).

거래대금 상위만 보면 **상장 당일 코인이 1위로 올라온다**(2026-07-26 실측: EUL 상장 0일, 587억 1위).
v2.4에서 하한을 365일 → **10일**로 낮췄다. 근거는 '나이'가 아니라 **1시간봉 201개 요건**이다:
상장 201시간(=8.4일) 미만 종목은 추세 판정이 불가해 애초에 진입할 수 없으므로(아래 불변식 테스트),
필터의 역할은 '나쁜 신호 차단'이 아니라 **거래 불가능한 종목이 유한한 15칸을 먹지 않게 하는 것**이다.
10일 = 8.4일 + 거래소 캔들 공백 여유(실측 최대 415분).

특히 중요한 회귀 테스트: **조회 실패를 '히스토리 없음'으로 캐시하면 안 된다.**
실제로 레이트리밋 한 번에 ETH가 영구 제외되는 버그를 겪었다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config import charter as C
from data.screener import Screener
from strategies.base import Action
from strategies.rsi2_pullback import (
    Rsi2PullbackStrategy, TREND_EMA_BARS, trend_up_from_hourly,
)


class FakeScreener(Screener):
    def __init__(self, old_markets, fail_markets=(), **kw):
        super().__init__(**kw)
        self.old = set(old_markets)          # 상장 경과일이 하한 이상인 종목
        self.fail = set(fail_markets)        # 조회가 실패하는 종목
        self.calls: list[str] = []

    def _has_min_history(self, market):
        self.calls.append(market)
        if market in self.fail:
            return None                      # 판정 불가
        ok = market in self.old
        self._history_ok[market] = ok         # 확정 응답만 캐시(실제 구현과 동일)
        return ok


def test_신규상장_코인은_제외된다():
    s = FakeScreener(old_markets={"KRW-BTC", "KRW-ETH"})
    kept = s._apply_history_filter(["KRW-BTC", "KRW-EUL", "KRW-ETH", "KRW-PIEVERSE"])
    assert kept == ["KRW-BTC", "KRW-ETH"]
    assert s.rejected_new == ["KRW-EUL", "KRW-PIEVERSE"]


def test_조회실패는_캐시하지_않고_다음주기에_재시도():
    """레이트리밋으로 한 번 실패한 종목이 영구 제외되면 안 된다 (실제 겪은 버그)."""
    s = FakeScreener(old_markets={"KRW-ETH"}, fail_markets={"KRW-ETH"})
    assert s._apply_history_filter(["KRW-ETH"]) == []      # 1차: 판정 불가 → 보류
    assert "KRW-ETH" not in s._history_ok                  # 캐시되지 않았다
    s.fail.clear()                                         # API 회복
    assert s._apply_history_filter(["KRW-ETH"]) == ["KRW-ETH"]   # 2차: 통과


def test_확정판정은_캐시해_재조회하지_않는다():
    """상장일은 변하지 않으므로 한 번 확인하면 재조회 불필요(API 절약)."""
    s = FakeScreener(old_markets={"KRW-BTC"})
    s._apply_history_filter(["KRW-BTC", "KRW-EUL"])
    n = len(s.calls)
    s._apply_history_filter(["KRW-BTC", "KRW-EUL"])
    assert len(s.calls) == n, "캐시된 종목을 다시 조회하면 안 됨"


def test_판정불가는_rejected_new에_넣지_않는다():
    """'신규 상장'과 'API 실패'는 다른 사유다 — 대시보드 표기가 오해를 부르지 않게."""
    s = FakeScreener(old_markets={"KRW-BTC"}, fail_markets={"KRW-XRP"})
    s._apply_history_filter(["KRW-BTC", "KRW-XRP", "KRW-EUL"])
    assert s.rejected_new == ["KRW-EUL"]


def test_헌장_기준값():
    """v2.4: 10일 = 1시간봉 201개(8.4일) + 거래소 캔들 공백 여유."""
    assert C.MIN_LISTING_DAYS == 10
    assert C.MIN_LISTING_DAYS * 24 >= 201, "하한이 1시간봉 201개 요건보다 짧으면 안 된다"


# ── v2.4 안전성 불변식: 1시간봉 201개 미만은 진입 불가 ──────────
# 이 불변식이 깨지면 MIN_LISTING_DAYS=10 의 근거가 사라진다(=신규 상장 종목이 추세 판정 없이
# 진입할 수 있게 된다). 값을 낮춘 판단 전체가 여기에 걸려 있으므로 명시적으로 고정한다.

def _hourly(bars: int) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=bars, freq="1h")
    c = pd.Series([100.0] * bars, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c,
                         "volume": pd.Series(1.0, index=idx)})


@pytest.mark.parametrize("bars", [0, 1, 100, 199, 200])
def test_1시간봉_201개_미만은_추세판정_불가(bars):
    assert trend_up_from_hourly(_hourly(bars)) is None


def test_1시간봉_201개부터_추세판정_가능():
    """경계: 201개에서 처음으로 bool 이 나온다(200개 완성봉 + 진행봉 1개)."""
    assert isinstance(trend_up_from_hourly(_hourly(201)), bool)


def test_추세판정_불가면_어떤_캔들에도_진입하지_않는다():
    """
    트레이더는 판정 불가 시 ctx 를 비워 넘긴다(bot/trader.py:_trend_ctx).
    라이브 창은 200봉이고 ctx 없는 폴백은 TREND_EMA_BARS(2400)봉을 요구하므로 우회 경로도 없다.
    """
    assert TREND_EMA_BARS > 200, "라이브 창(200봉)으로 폴백 추세판정이 되면 불변식이 깨진다"
    strat = Rsi2PullbackStrategy(C.STRATEGY_SPECS["rsi2"])
    # RSI(2)≈0 + 변동성 충분 → 추세만 있으면 진입하는 캔들
    closes = [10_000 * (1 - 0.004) ** i for i in range(200)]
    idx = pd.date_range("2026-07-01", periods=200, freq="5min")
    close = pd.Series(closes, index=idx, dtype=float)
    df = pd.DataFrame({
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": pd.Series(np.full(200, 100.0), index=idx),
    })
    assert strat.signal(df, {"trend_up": True}).action == Action.ENTER_LONG   # 대조군
    for ctx in ({}, {"trend_up": None}, None):
        sig = strat.signal(df, ctx)
        assert sig.action == Action.HOLD, f"ctx={ctx} 에서 진입이 발생했다"
        assert sig.meta["trend_up"] is None

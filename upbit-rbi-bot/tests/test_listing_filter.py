"""
상장 경과일 필터 테스트 (헌장 §3, v1.5).

거래대금 상위만 보면 **상장 당일 코인이 1위로 올라온다**(2026-07-26 실측: EUL 상장 0일, 587억 1위).
신규 상장 코인은 변동성이 극단적이라 ATR 게이트를 아주 쉽게 통과 → 전략이 미검증 종목을
선호하는 선택 편향이 생긴다. 검증은 2년 히스토리 종목에서만 했다.

특히 중요한 회귀 테스트: **조회 실패를 '히스토리 없음'으로 캐시하면 안 된다.**
실제로 레이트리밋 한 번에 ETH가 영구 제외되는 버그를 겪었다.
"""
from __future__ import annotations

import pytest

from config import charter as C
from data.screener import Screener


class FakeScreener(Screener):
    def __init__(self, old_markets, fail_markets=(), **kw):
        super().__init__(**kw)
        self.old = set(old_markets)          # 상장 1년 이상인 종목
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
    assert C.MIN_LISTING_DAYS == 365

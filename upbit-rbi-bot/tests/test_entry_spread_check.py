"""
진입 직전 스프레드 재확인 테스트 (헌장 §6, v1.6).

유니버스는 10분 주기로 갱신되므로 스크리닝 시점의 스프레드는 최신이 아니다.
거래당 기댓값이 0.26% 수준이라 스프레드 0.1%p 차이가 손익을 가르므로,
주문 직전에 다시 확인하고 넓어졌으면 진입을 취소한다.
v1.6에서 종목 풀을 6 → 15로 늘렸기 때문에 이 확인이 특히 중요해졌다.
"""
from __future__ import annotations

import pandas as pd
import pytest

from config import charter as C
from bot.risk_manager import RiskManager
from bot.trader import Trader, build_strategies
from data.screener import Screener
from data.upbit_client import OrderResult


class _Null:
    def send(self, *a, **k): pass


class _Rec:
    def __init__(self): self.events = []
    def log(self, event, **kw): self.events.append((event, kw))


class FakeScreener(Screener):
    """spread_now 를 주입값으로 대체 — 네트워크 없이 진입 직전 확인을 검증."""

    def __init__(self, spread_value, **kw):
        super().__init__(**kw)
        self.spread_value = spread_value
        self.checks = 0

    def spread_now(self, market):
        self.checks += 1
        return self.spread_value


def df_ok(bars=60):
    idx = pd.date_range("2026-07-01", periods=bars, freq="5min")
    closes = [10_000 * (1 - 0.004) ** i for i in range(bars)]
    c = pd.Series(closes, index=idx)
    return pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "high": c * 1.01,
                         "low": c * 0.99, "close": c,
                         "volume": pd.Series(100.0, index=idx)})


def trader_with(spread_value):
    t = Trader.__new__(Trader)
    t.risk = RiskManager()
    t.risk.update_capital(90_000.0, 90_000.0)
    t.positions, t.last_prices, t.regimes = {}, {}, {}
    t.trend_up, t._trend_at, t.signal_view = {}, {}, {}
    t.logger, t.notifier = _Rec(), _Null()
    t.strategies = build_strategies(("rsi2",))
    t.screener = FakeScreener(spread_value)
    t.ordered = []

    class Orders:
        def enter_long(self, market, price, krw):
            t.ordered.append((market, krw))
            return OrderResult(ok=True, filled_volume=krw / price, avg_price=price)
    t.orders = Orders()
    return t


def test_스프레드_정상이면_주문한다():
    t = trader_with(0.0005)          # 0.05% — 상한 이내
    strat = t.strategies["rsi2"]
    t._open("rsi2", strat, "KRW-BTC", 10_000.0, df_ok(), note="테스트")
    assert len(t.ordered) == 1
    assert t.screener.checks == 1, "진입 직전 확인이 호출돼야 함"


def test_스프레드_넓어지면_진입_취소():
    t = trader_with(0.005)           # 0.5% — 상한 초과
    strat = t.strategies["rsi2"]
    t._open("rsi2", strat, "KRW-DOGE", 10_000.0, df_ok(), note="테스트")
    assert t.ordered == [], "스프레드가 넓어졌으면 주문하면 안 됨"
    events = [e for e, _ in t.logger.events]
    assert "entry_skip" in events
    assert "스프레드" in t.logger.events[0][1]["reason"]


def test_스프레드_확인실패시_진입_금지():
    """확인 불가 상태로 실거래하지 않는다(비용 미확인 = 진입 금지)."""
    t = trader_with(None)
    strat = t.strategies["rsi2"]
    t._open("rsi2", strat, "KRW-XRP", 10_000.0, df_ok(), note="테스트")
    assert t.ordered == []
    assert "확인 실패" in t.logger.events[0][1]["reason"]


def test_tradable_now_판정():
    s = FakeScreener(0.0008)
    ok, why = s.tradable_now("KRW-BTC")
    assert ok and "0.080%" in why
    s.spread_value = 0.002
    ok, why = s.tradable_now("KRW-BTC")
    assert not ok and "상한" in why


def test_헌장_풀_확대값():
    assert C.UNIVERSE_TOP_N == 15                     # 6 → 15 (v1.6)
    assert C.MIN_TURNOVER_24H_KRW == 100_000_000      # 100억 → 30억 → 1억 (v2.0)
    assert C.SPREAD_CANDIDATE_MULT == 8               # 후보 스캔 범위 확대
    assert C.VERIFY_SPREAD_ON_ENTRY is True
    # 동시 보유 한도는 그대로 — 풀이 커져도 리스크 노출은 자본이 제한한다
    assert C.MAX_CONCURRENT_POSITIONS == 3

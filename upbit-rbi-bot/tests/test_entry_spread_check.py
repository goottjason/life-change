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


# 실측(2026-07-26~28): 스프레드 상한 0.1%를 통과하는 KRW 종목 수. 유니버스를 실제로
# 제한하는 것은 이 수치이지 UNIVERSE_TOP_N 이 아니어야 한다.
OBSERVED_SPREAD_PASSING_MARKETS = 20


def test_유니버스_상한은_스프레드필터보다_느슨해야_한다():
    """§3 — 유니버스를 정하는 것은 '칸 수'가 아니라 **스프레드 필터**여야 한다 (v2.5).

    UNIVERSE_TOP_N 은 v2.4까지 15였는데 이는 근거 없이 잡은 값이었고, 스프레드를 통과하는
    종목(실측 18~20개)보다 작아 **상한이 먼저 걸리는** 상태였다. 즉 비용 기준으로 거래 가능한
    종목을 칸이 모자라서 떨어뜨리고 있었다.

    이 테스트는 특정 숫자(60)가 아니라 **그 관계**를 고정한다 — 값을 튜닝해도 상한이 다시
    제약이 되는 순간에만 실패한다.
    """
    assert C.UNIVERSE_TOP_N > OBSERVED_SPREAD_PASSING_MARKETS, (
        f"UNIVERSE_TOP_N={C.UNIVERSE_TOP_N} 이 스프레드 통과 종목수"
        f"({OBSERVED_SPREAD_PASSING_MARKETS})보다 작거나 같다 → 비용상 거래 가능한 종목을"
        " 칸 부족으로 버리게 된다. 상한이 아니라 스프레드가 유니버스를 정해야 한다(§3)."
    )


def test_유니버스_상한_확대가_주기·레이트리밋을_깨지_않는다():
    """v2.5 — 상한을 올려도 되는 근거(실측 캔들 왕복 0.215초)를 산술로 고정한다.

    호출은 직렬이므로 N 을 키워도 **처리율이 아니라 소요시간**만 늘어난다. tick 루프는
    `tick(); sleep(interval)`(deploy/run_bot.py) 이라 긴 tick 은 겹치지 않고 주기만 늘린다.
    """
    quote_rtt_sec = 0.215          # 실측 중앙값 (n=6, 최대 0.234)
    calls_per_market = 2           # minute5 + minute15
    trend_rtt_sec = 0.215          # 1시간봉 — TREND_REFRESH_SEC 캐시 갱신 tick 에만

    # 최악(추세 갱신이 겹친 tick)의 주기가 5분봉 한 봉의 절반을 넘지 않아야 한다.
    worst_cycle = C.UNIVERSE_TOP_N * (calls_per_market * quote_rtt_sec + trend_rtt_sec) + 10
    assert worst_cycle < 300 / 2, f"최악 주기 {worst_cycle:.0f}초가 5분봉(300초) 대비 과도"

    # 처리율은 N 과 무관하게 일정하다 — 업비트 시세 한도 10 req/s 안에 있어야 한다.
    assert 1 / quote_rtt_sec < 10

    # 후보 스캔은 이미 전 종목으로 포화돼 있어 N 을 키워도 오더북 호출이 늘지 않는다.
    assert C.UNIVERSE_TOP_N * C.SPREAD_CANDIDATE_MULT >= 270


def test_헌장_풀_확대값():
    assert C.MIN_TURNOVER_24H_KRW == 100_000_000      # 100억 → 30억 → 1억 (v2.0)
    # 후보 스캔: 하한을 넘는 전 종목 확인 (top_n×20 ≥ KRW 270) — v2.0
    assert C.UNIVERSE_TOP_N * C.SPREAD_CANDIDATE_MULT >= 270
    assert C.VERIFY_SPREAD_ON_ENTRY is True
    # 풀이 커져도 리스크 노출은 자본이 제한한다 — 트랙별 배정 합이 자본을 못 넘는다
    # (v4.0: 검증 트랙 배분 2/3×1 + 실험 트랙 예산 3×10,000원/90,000원 = 1.0)
    assert C.MAX_CONCURRENT_POSITIONS >= 1
    assert (C.ALLOC_PER_STRATEGY_RATIO * C.MAX_POSITIONS_VALIDATED
            + C.MAX_POSITIONS_EXPERIMENTAL * C.EXPERIMENT_MAX_ORDER_KRW
            / C.DEFAULT_CAPITAL_KRW) <= 1.0 + 1e-9

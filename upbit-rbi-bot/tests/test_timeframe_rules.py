"""
종목별 허용 타임프레임 규칙 테스트 (헌장 §3.2-h, v2.1).

핵심 발견: **스프레드가 0.1%를 넘는 종목은 5분봉에서 전부 음수, 15분봉에서는 대부분 양수.**
5분봉은 한 봉 움직임(0.196%)이 작아 비용에 민감하고 15분봉(0.323%)은 감당한다.
그래서 종목을 차단하는 대신 허용 타임프레임을 종목별로 다르게 둔다.
"""
from __future__ import annotations

import pandas as pd
import pytest

from config import charter as C
from bot.risk_manager import RiskManager
from bot.trader import Trader, build_strategies
from data.screener import Screener, cap_for
from data.upbit_client import OrderResult


class _Null:
    def send(self, *a, **k): pass


class _Rec:
    def __init__(self): self.events = []
    def log(self, event, **kw): self.events.append((event, kw))


def dip(bars=60, tf="5min"):
    idx = pd.date_range("2026-07-01", periods=bars, freq=tf)
    c = pd.Series([10_000 * (1 - 0.004) ** i for i in range(bars)], index=idx)
    return pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "high": c * 1.01,
                         "low": c * 0.99, "close": c,
                         "volume": pd.Series(100.0, index=idx)})


def trader():
    t = Trader.__new__(Trader)
    t.risk = RiskManager()
    t.risk.update_capital(90_000.0, 90_000.0)
    t.positions, t.last_prices, t.regimes = {}, {}, {}
    t.trend_up, t._trend_at, t.signal_view = {}, {}, {}
    t.logger, t.notifier = _Rec(), _Null()
    t.strategies = build_strategies()
    t._trend_ctx = lambda m: {"trend_up": True}
    t.ordered = []

    class Orders:
        def enter_long(self, market, price, krw):
            t.ordered.append((market, krw))
            return OrderResult(ok=True, filled_volume=krw / price, avg_price=price)
    t.orders = Orders()

    class Scr:
        spreads = {}
        def tradable_now(self, market, cap=None): return True, "스프레드 0.050%"
    t.screener = Scr()
    return t


FRAMES = {"minute5": dip(tf="5min"), "minute15": dip(tf="15min")}


def test_5분봉_음수종목은_5분봉_진입금지():
    """AVAX 는 5분봉 −0.096% / 15분봉 +0.560% → 15분봉만 진입해야 한다."""
    t = trader()
    t._process_market("KRW-AVAX", FRAMES)
    assert len(t.positions) == 1
    assert next(iter(t.positions.values())).strategy == "rsi2_15m"


def test_양쪽_양수종목은_둘다_가능():
    """XRP 는 5분 +0.158% / 15분 +0.361% → 어느 쪽이든 진입 가능(중복은 §3.3이 막는다)."""
    t = trader()
    t._process_market("KRW-XRP", FRAMES)
    assert len(t.positions) == 1
    assert next(iter(t.positions.values())).strategy == "rsi2"   # 5분봉이 먼저 순회


def test_전략별_스프레드_상한():
    assert C.strategy_spread_cap("rsi2") == 0.001          # 5분봉은 기본 상한
    assert C.strategy_spread_cap("rsi2_15m") == 0.0025     # 15분봉은 완화


def test_종목별_스프레드_상한():
    """15분봉 검증 통과 종목만 완화된 상한을 받는다."""
    assert cap_for("KRW-AVAX", 0.001) == 0.0025
    assert cap_for("KRW-XRP", 0.001) == 0.001
    assert cap_for("KRW-UNKNOWN", 0.001) == 0.001


def test_안정성_판정도_종목별_상한을_쓴다():
    s = Screener()
    s._spread_hist["KRW-AVAX"] = [0.0015, 0.0018, 0.0016, 0.0020]   # 0.15~0.20%
    assert s._spread_stable("KRW-AVAX") is True     # AVAX 상한 0.25% → 통과
    s._spread_hist["KRW-XRP"] = [0.0015, 0.0018, 0.0016, 0.0020]
    assert s._spread_stable("KRW-XRP") is False     # XRP 상한 0.10% → 탈락


def test_양쪽_음수종목은_블랙리스트():
    """KAITO·GAS·TRUMP·TRX 는 두 타임프레임 모두 음수로 확인됐다."""
    assert {"KAITO", "GAS", "TRUMP", "TRX"} <= C.UNIVERSE_BLACKLIST


def test_15분봉_전용종목은_완화상한과_5분금지가_짝을_이룬다():
    """완화 상한을 받은 종목은 반드시 5분봉이 금지돼야 한다(비용 초과 진입 방지)."""
    for sym in C.WIDE_SPREAD_ALLOWED:
        assert sym in C.STRATEGY_BLACKLIST["rsi2"], sym


# ── v2.1: 검증된 종목만 거래 (§3.2-i, 헌장 §0.3) ─────────────
def test_미검증_종목은_유니버스에_들어오지_않는다():
    """스프레드가 좁아도 개별 검증을 통과하지 않으면 거래하지 않는다(KAITO 사례)."""
    from data.screener import Screener, select_universe

    class S(Screener):
        def _fetch_tickers(self):
            return [{"market": "KRW-XRP", "acc_trade_price_24h": 9e11},
                    {"market": "KRW-NEWCOIN", "acc_trade_price_24h": 9e11}]
        def _flagged(self): return set()
        def _apply_history_filter(self, markets): return markets
        def _apply_spread_filter(self, c): return c      # 스프레드는 통과시킴
    s = S(top_n=5, min_turnover=0)
    picked = s.eligible()
    assert "KRW-XRP" in picked
    assert "KRW-NEWCOIN" not in picked, "미검증 종목이 통과하면 안 됨"
    assert s.skipped_unvalidated == 1


def test_검증테이블과_상한이_일치한다():
    """VALIDATED_MARKETS 값이 곧 그 종목의 스프레드 상한이다."""
    from data.screener import cap_for
    for sym, cap in C.VALIDATED_MARKETS.items():
        assert cap_for(f"KRW-{sym}", C.MAX_SPREAD_RATIO) == cap, sym


def test_블랙리스트와_검증테이블은_상충하지_않는다():
    assert not (set(C.VALIDATED_MARKETS) & C.UNIVERSE_BLACKLIST)

"""
동시 다중 포지션 + 타임프레임 분리 테스트 (헌장 v1.4).

v1.3까지는 '전략당 1포지션'이라 rsi2 단독 가동 시 동시 1개만 보유했다. 그러나 성적을 낸
포트폴리오 시뮬레이션은 **동시 3포지션**을 가정했으므로 라이브를 맞춘다.
또 5분봉/15분봉 전략이 각자의 봉으로 판단해야 백테스트와 일치한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config import charter as C
from bot.position import Position, ExitReason
from bot.risk_manager import RiskManager
from bot.trader import Trader, build_strategies
from data.upbit_client import OrderResult


class _Null:
    def send(self, *a, **k): pass


class _Rec:
    def __init__(self): self.events = []
    def log(self, event, **kw): self.events.append((event, kw))


def dip_df(bars=60, tf="5min", drop=0.004, band=0.02, price=10_000.0):
    """RSI(2)가 0에 가까워지는 하락 캔들 + 충분한 ATR."""
    idx = pd.date_range("2026-07-01", periods=bars, freq=tf)
    closes = [price * (1 - drop) ** i for i in range(bars)]
    c = pd.Series(closes, index=idx)
    return pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "high": c * (1 + band / 2),
                         "low": c * (1 - band / 2), "close": c,
                         "volume": pd.Series(100.0, index=idx)})


def flat_df(bars=60, tf="5min"):
    idx = pd.date_range("2026-07-01", periods=bars, freq=tf)
    c = pd.Series(np.full(bars, 10_000.0), index=idx)
    return pd.DataFrame({"open": c, "high": c * 1.001, "low": c * 0.999, "close": c,
                         "volume": pd.Series(100.0, index=idx)})


def bare_trader(strategies=("rsi2", "rsi2_15m")):
    t = Trader.__new__(Trader)
    t.risk = RiskManager()
    t.risk.update_capital(90_000.0, 90_000.0)
    t.positions = {}
    t.last_prices = {}
    t.regimes = {}
    t.trend_up = {}
    t._trend_at = {}
    t.signal_view = {}
    t.logger = _Rec()
    t.notifier = _Null()
    t.strategies = build_strategies(strategies)
    t.filled = []

    class Orders:
        def enter_long(self, market, price, krw):
            t.filled.append((market, krw))
            return OrderResult(ok=True, filled_volume=krw / price, avg_price=price)

        def exit_position(self, pos, price, reason):
            return OrderResult(ok=True, filled_volume=pos.volume, avg_price=price)
    t.orders = Orders()
    t._trend_ctx = lambda market: {"trend_up": True}      # 추세 통과로 고정

    class Scr:   # 진입 직전 스프레드 확인(v1.6) 통과 스텁
        spreads = {}
        def tradable_now(self, market): return True, "스프레드 0.050%"
    t.screener = Scr()
    return t


FRAMES = {"minute5": dip_df(tf="5min"), "minute15": dip_df(tf="15min")}


def test_두_전략이_같은_코인에_동시진입하지_않는다():
    """§3.3 동일코인 중복 금지 — 5분·15분 신호가 같이 떠도 한 포지션만."""
    t = bare_trader()
    t._process_market("KRW-BTC", FRAMES)
    assert len(t.positions) == 1
    assert len({p.market for p in t.positions.values()}) == 1


def test_서로_다른_코인이면_동시보유():
    t = bare_trader()
    t._process_market("KRW-BTC", FRAMES)
    t._process_market("KRW-ETH", FRAMES)
    assert len(t.positions) == 2
    assert {p.market for p in t.positions.values()} == {"KRW-BTC", "KRW-ETH"}


def test_동시포지션_한도를_넘지_않는다():
    t = bare_trader()
    for m in ("KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-BCH"):
        t._process_market(m, FRAMES)
    assert len(t.positions) == C.MAX_CONCURRENT_POSITIONS == 3


def test_포지션_키는_전략과_코인의_조합():
    t = bare_trader()
    t._process_market("KRW-BTC", FRAMES)
    key = next(iter(t.positions))
    assert ":" in key and key.endswith("KRW-BTC")
    assert t.positions[key].key == key


def test_전략별로_자기_타임프레임_캔들을_쓴다():
    """5분봉은 잠잠하고 15분봉만 과매도면, 15분봉 전략만 진입해야 한다."""
    t = bare_trader()
    t._process_market("KRW-BTC", {"minute5": flat_df(tf="5min"),
                                  "minute15": dip_df(tf="15min")})
    assert len(t.positions) == 1
    assert next(iter(t.positions.values())).strategy == "rsi2_15m"


def test_15분봉_포지션은_15분봉_손절폭을_쓴다():
    t = bare_trader(("rsi2_15m",))
    t._process_market("KRW-BTC", FRAMES)
    pos = next(iter(t.positions.values()))
    assert pos.sl_ratio == pytest.approx(0.030)      # 15분봉 검증값
    assert pos.tp_ratio == pytest.approx(0.030)

    t5 = bare_trader(("rsi2",))
    t5._process_market("KRW-BTC", FRAMES)
    pos5 = next(iter(t5.positions.values()))
    assert pos5.sl_ratio == pytest.approx(0.025)     # 5분봉 검증값


def test_한_타임프레임_조회실패시_다른_전략은_계속_동작():
    t = bare_trader()
    t._process_market("KRW-BTC", {"minute15": dip_df(tf="15min")})   # 5분봉 누락
    assert len(t.positions) == 1
    assert next(iter(t.positions.values())).strategy == "rsi2_15m"


def test_청산은_해당_전략의_타임프레임으로_판정():
    """15분봉 포지션은 15분봉 캔들로 청산 판정 — 5분봉만 오면 판정을 보류한다."""
    t = bare_trader(("rsi2_15m",))
    pos = Position(strategy="rsi2_15m", market="KRW-BTC", entry_price=10_000.0,
                   size_krw=30_000.0, volume=3.0,
                   entry_time=pd.Timestamp("2026-07-01"), entry_atr=100.0)
    t.positions[pos.key] = pos
    t.risk.s.open_positions = 1
    t._process_market("KRW-BTC", {"minute5": flat_df(tf="5min")})    # 15분봉 없음
    assert pos.key in t.positions, "타임프레임 캔들이 없으면 청산 판정을 보류해야 함"

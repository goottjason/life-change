"""
보유 포지션은 어떤 상황에서도 청산 관리를 받아야 한다 (§4·§9, v4.1 회귀).

2026-08-18 실사고: breakout 이 KRW-VVV 를 샀고 직후 업비트가 투자주의 플래그를 붙여
스크리너가 유니버스에서 뺐다. tick() 이 **유니버스만 순회**했으므로 그 순간부터 VVV 는
캔들 조회·청산 판정·가격 갱신이 전부 멈췄고, 손절선(−1%)을 지나 −8% 까지 방치됐다.
같은 부류의 구멍(MDD 정지 시 관리 중단, 한 종목 예외로 나머지 건너뜀, 유니버스 밖
종목에 재진입)을 함께 막는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import charter as C
from bot.position import Position
from bot.risk_manager import RiskManager
from bot.trader import Trader, build_strategies
from data.upbit_client import OrderResult


class _Null:
    def send(self, *a, **k): pass


class _Rec:
    def __init__(self): self.events = []
    def log(self, event, **kw): self.events.append((event, kw))


def _flat(bars=320, price=10_000.0, tf="5min"):
    idx = pd.date_range("2026-08-18 00:00", periods=bars, freq=tf)
    c = pd.Series(np.full(bars, price), index=idx)
    return pd.DataFrame({"open": c, "high": c * 1.001, "low": c * 0.999, "close": c,
                         "volume": pd.Series(100.0, index=idx)})


def _crash(bars=320, price=10_000.0, drop=0.05, tf="5min"):
    """마지막 봉만 -5% 급락 — 어떤 손절 규칙이든 걸린다."""
    df = _flat(bars, price, tf)
    df.iloc[-1, df.columns.get_loc("close")] = price * (1 - drop)
    df.iloc[-1, df.columns.get_loc("low")] = price * (1 - drop)
    return df


class _Client:
    """종목별로 캔들을 정해두고, 특정 종목은 예외를 던지게 할 수 있다."""
    def __init__(self, frames: dict, raise_for: set | None = None):
        self.frames = frames; self.raise_for = raise_for or set()
        self.fetched: list[str] = []
    def get_candles(self, market, interval="minute5", count=200):
        self.fetched.append(market)
        if market in self.raise_for:
            return None          # 조회는 성공했지만 빈 응답 → 처리 단계에서 예외가 난다
        return self.frames[market]
    def get_account_equity(self, price_of):
        return 90_000.0, 90_000.0
    def get_price(self, market):
        return float(self.frames[market]["close"].iloc[-1])


class _Failsafe:
    class _HB:
        def beat(self): pass
    heartbeat = _HB()
    def check_feed(self): return True
    def on_api_error(self, e): pass


class _Screener:
    def __init__(self, universe): self._u = list(universe); self.spreads = {}
    def eligible(self): return list(self._u)
    def tradable_now(self, market, cap=None): return True, "ok"


def _trader(universe, frames, strategies=("breakout",), raise_for=None):
    t = Trader.__new__(Trader)
    t.client = _Client(frames, raise_for)
    t.risk = RiskManager(); t.risk.update_capital(90_000.0, 90_000.0)
    t.strategies = build_strategies(strategies)
    t.positions = {}; t.last_prices = {}; t.regimes = {}
    t.trend_up = {}; t._trend_at = {}; t.signal_view = {}
    t.logger = _Rec(); t.notifier = _Null()
    t.failsafe = _Failsafe(); t.screener = _Screener(universe)
    t._trend_ctx = lambda market: {"trend_up": True}

    class Orders:
        def __init__(s): s.sold = []
        def enter_long(s, market, price, krw):
            return OrderResult(ok=True, filled_volume=krw / price, avg_price=price)
        def exit_position(s, pos, price, reason):
            s.sold.append((pos.market, reason)); return OrderResult(ok=True, filled_volume=pos.volume, avg_price=price)
    t.orders = Orders()
    return t


def _hold(t, market, strategy="breakout", entry=10_000.0):
    pos = Position(strategy=strategy, market=market, entry_price=entry, size_krw=10_000.0,
                   volume=1.0, entry_time=pd.Timestamp("2026-08-18 00:00"), entry_atr=10.0)
    t.positions[pos.key] = pos
    t.risk.on_open(strategy)
    return pos


def test_보유_종목이_유니버스에서_빠져도_청산_판정은_계속된다():
    """VVV 사고 재현: 유니버스 = [BTC] 인데 VVV 를 들고 있고 VVV 가 -5% 급락 → 손절돼야 한다."""
    frames = {"KRW-BTC": _flat(), "KRW-VVV": _crash()}
    t = _trader(universe=["KRW-BTC"], frames=frames)
    _hold(t, "KRW-VVV")
    t.tick()
    assert "KRW-VVV" in t.client.fetched, "유니버스 밖 보유 종목의 캔들을 받아야 한다"
    assert not t.positions, "손절선을 지났는데 포지션이 남아 있다 — 관리에서 빠졌다"
    assert t.orders.sold and t.orders.sold[0][0] == "KRW-VVV"
    assert t.last_prices["KRW-VVV"] == frames["KRW-VVV"]["close"].iloc[-1]   # 대시보드 가격도 갱신


def test_유니버스_밖_보유_종목은_청산_후_같은_tick_재진입_금지():
    """청산 직후 다른 전략(또는 같은 전략)이 유니버스 밖 종목을 다시 사면 안 된다.
    (플래그 종목은 진입 자체가 금지돼야 하므로 '청산만 관리'다)"""
    # 돌파 신호가 서는 캔들: 직전 평평 → 마지막 봉 +1%·거래량 6배 (breakout ENTER)
    df = _flat(); df.iloc[-1, df.columns.get_loc("close")] = 10_100.0
    df.iloc[-1, df.columns.get_loc("high")] = 10_100.0
    df.iloc[-1, df.columns.get_loc("volume")] = 600.0
    frames = {"KRW-BTC": _flat(), "KRW-VVV": df}
    t = _trader(universe=["KRW-BTC"], frames=frames)
    t.tick()
    assert not [p for p in t.positions.values() if p.market == "KRW-VVV"], \
        "유니버스 밖 종목에 신규 진입했다"


def test_MDD_정지_중에도_보유_포지션의_손절은_작동한다():
    """§5.4 전면 정지는 '신규 진입 금지'이지 '보유 포지션 방치'가 아니다.
    halted 인 채로 tick 이 통째로 return 하면 손절선이 없는 포지션이 된다."""
    frames = {"KRW-BTC": _crash()}
    t = _trader(universe=["KRW-BTC"], frames=frames)
    _hold(t, "KRW-BTC")
    t.risk.s.halted = True; t.risk.s.halt_reason = "MDD 50% (§5.4)"
    t.tick()
    assert not t.positions, "정지 중이라고 손절을 건너뛰면 안 된다"
    # 정지 중 신규 진입은 여전히 막혀 있어야 한다
    ok, _ = t.risk.can_enter("breakout")
    assert not ok


def test_한_종목_처리_예외가_다른_종목_청산을_막지_않는다():
    """BTC 처리 중 예외(빈 캔들 → 처리 단계 TypeError)가 나도 ETH 손절은 이번 tick 에 이뤄져야 한다.
    (get_candles 의 예외는 이미 잡히지만 _process_market 안의 예외는 tick 전체를 죽였다)"""
    frames = {"KRW-BTC": _flat(), "KRW-ETH": _crash()}
    t = _trader(universe=["KRW-BTC", "KRW-ETH"], frames=frames, raise_for={"KRW-BTC"})
    _hold(t, "KRW-ETH")
    t.tick()
    assert not t.positions, "다른 종목 예외 때문에 ETH 손절이 건너뛰어졌다"

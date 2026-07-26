"""스크리너 유니버스 순회 + ATR 사이징 통합 (헌장 v1.2)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bot.trader import Trader, build_strategies
from bot.risk_manager import RiskManager
from bot.position import Position
from config import charter as C
from data.upbit_client import OrderResult
from indicators import ta


class _Null:
    def send(self, msg): pass


class _Rec:
    def __init__(self): self.events = []
    def log(self, event, **kw): self.events.append((event, kw))


def _df():
    idx = pd.date_range("2026-07-26", periods=30, freq="5min")
    close = pd.Series(np.linspace(100, 110, 30), index=idx)
    return pd.DataFrame({"open": close, "high": close + 1.0,
                         "low": close - 1.0, "close": close, "volume": 10.0})


def test_open_uses_atr_sizing_and_sets_ratios():
    t = Trader.__new__(Trader)
    t.risk = RiskManager()
    t.positions = {}
    t.logger = _Rec()
    t.notifier = _Null()
    captured = {}

    class Orders:
        def enter_long(self, market, price, krw):
            captured["krw"] = krw
            return OrderResult(ok=True, filled_volume=krw / price, avg_price=price)
    t.orders = Orders()

    df = _df()
    price = float(df["close"].iloc[-1])
    strat = build_strategies(("macd",))["macd"]   # v1.3: 기본 가동목록에 없어 명시 생성
    t._open("macd", strat, "KRW-BTC", price, df)

    entry_atr = float(ta.atr(df).iloc[-1])
    stop_ratio = strat.spec.atr_stop_mult * entry_atr / price
    expected = C.position_size_krw(stop_ratio, t.risk.s.capital, t.risk.s.available_krw)
    assert captured["krw"] == pytest.approx(expected)
    assert "macd" in t.positions
    p = t.positions["macd"]
    assert p.sl_ratio == pytest.approx(stop_ratio)
    assert p.tp_ratio == pytest.approx(stop_ratio * 2.0)


def test_open_skips_when_no_atr():
    t = Trader.__new__(Trader)
    t.risk = RiskManager()
    t.positions = {}
    t.logger = _Rec()
    t.notifier = _Null()

    class Orders:
        def enter_long(self, *a, **k):
            raise AssertionError("ATR 없으면 진입하면 안 됨")
    t.orders = Orders()

    short_df = _df().iloc[:5]   # 14봉 미만 → ATR 불가
    strat = build_strategies(("macd",))["macd"]   # v1.3: 기본 가동목록에 없어 명시 생성
    t._open("macd", strat, "KRW-BTC", 100.0, short_df)
    assert "macd" not in t.positions
    assert "entry_fail" in [e for e, _ in t.logger.events]


def test_tick_scans_screener_markets():
    t = Trader.__new__(Trader)
    t.risk = RiskManager()
    t.positions = {}
    t.last_prices = {}
    t.regimes = {}
    t.notifier = _Null()
    scanned = []

    class Client:
        def get_account_equity(self, lookup): return (90_000.0, 90_000.0)
        def get_candles(self, market):
            scanned.append(market)
            raise RuntimeError("skip processing")
    t.client = Client()

    from safety.failsafe import Failsafe
    t.failsafe = Failsafe(t.client, order_manager=None, notifier=_Null())

    class Scr:
        def eligible(self): return ["KRW-AAA", "KRW-BBB", "KRW-CCC"]
    t.screener = Scr()

    t.tick()
    assert scanned == ["KRW-AAA", "KRW-BBB", "KRW-CCC"]

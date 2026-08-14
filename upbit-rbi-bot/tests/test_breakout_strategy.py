"""
breakout 전략 단위 테스트 (v4.0 실험 트랙).
진입 판정만 검증한다 — 청산은 Position(트레일링·시간손절)이 전담하며 test_position.py 가 본다.
"""
from __future__ import annotations

import pandas as pd
import pytest

from config.charter import STRATEGY_SPECS
from strategies.base import Action
from strategies.breakout import BreakoutStrategy


def _df(closes, highs=None, volumes=None):
    n = len(closes)
    closes = pd.Series(closes, dtype=float)
    highs = pd.Series(highs if highs is not None else closes, dtype=float)
    idx = pd.date_range("2026-08-14 09:00", periods=n, freq="5min")
    return pd.DataFrame({
        "open": closes.values, "high": highs.values, "low": (closes * 0.999).values,
        "close": closes.values,
        "volume": pd.Series(volumes if volumes is not None else [1.0] * n, dtype=float).values,
    }, index=idx)


@pytest.fixture
def strat():
    return BreakoutStrategy(STRATEGY_SPECS["breakout"])


def _base(n=310, price=100.0):
    """직전 구간이 평평한(고가 100) 캔들. 마지막 봉만 테스트가 바꾼다."""
    return [price] * n, [price] * n, [1.0] * n


def test_enters_on_breakout_with_volume(strat):
    closes, highs, vols = _base()
    closes[-1] = 101.0; highs[-1] = 101.0; vols[-1] = 6.0   # 돌파 + 거래량 6배(>5배)
    sig = strat.signal(_df(closes, highs, vols))
    assert sig.action == Action.ENTER_LONG
    # 코호트 축이 meta에 전부 있어야 한다 (trader 가 이 dict를 JSON으로 저장한다)
    for key in ("breakout_pct", "vol_ratio", "atr_pct", "trend_up", "n", "vol_mult", "trail"):
        assert key in sig.meta


def test_holds_without_breakout(strat):
    closes, highs, vols = _base()
    vols[-1] = 2.0                                          # 거래량만 있고 돌파 없음
    assert strat.signal(_df(closes, highs, vols)).action == Action.HOLD


def test_holds_without_volume_confirmation(strat):
    closes, highs, vols = _base()
    closes[-1] = 101.0; highs[-1] = 101.0; vols[-1] = 1.2   # 돌파했지만 거래량 1.2배 < 1.5
    sig = strat.signal(_df(closes, highs, vols))
    assert sig.action == Action.HOLD
    assert "거래량" in sig.reason


def test_trend_recorded_but_not_gating(strat):
    """추세 필터는 없다 — trend_up=False여도 진입한다. 단 meta에 기록은 남는다."""
    closes, highs, vols = _base()
    closes[-1] = 101.0; highs[-1] = 101.0; vols[-1] = 6.0
    sig = strat.signal(_df(closes, highs, vols), ctx={"trend_up": False})
    assert sig.action == Action.ENTER_LONG
    assert sig.meta["trend_up"] is False


def test_never_emits_exit(strat):
    """청산은 Position 전담 — 어떤 입력에도 EXIT를 내지 않는다."""
    closes, highs, vols = _base()
    closes[-1] = 90.0                                       # 급락
    assert strat.signal(_df(closes, highs, vols)).action == Action.HOLD


def test_insufficient_bars(strat):
    closes, highs, vols = _base(n=100)
    assert strat.signal(_df(closes, highs, vols)).action == Action.HOLD

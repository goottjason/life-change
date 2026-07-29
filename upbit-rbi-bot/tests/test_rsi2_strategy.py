"""
rsi2 전략(헌장 v1.3) 단위 테스트 — 백테스트로 검증된 규칙이 코드에 그대로 들어갔는지 확인.

검증 규칙(5분봉): RSI(2)≤3 AND 1시간봉 EMA200 위 AND ATR/가격≥0.6% → 진입 / RSI(2)≥70 → 청산
v2.3: 진입선과 변동성 게이트는 **전략 스펙**에서 읽는다 — 15분봉은 RSI(2)≤7 · ATR≥0.83%.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.charter import STRATEGY_SPECS, stop_ratio_for, time_stop_bars_for
from strategies.base import Action
from strategies.rsi2_pullback import Rsi2PullbackStrategy, trend_up_from_hourly

SPEC = STRATEGY_SPECS["rsi2"]
SPEC_15M = STRATEGY_SPECS["rsi2_15m"]


def make_df(closes, high_mult=1.0, low_mult=1.0) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2026-07-01", periods=n, freq="5min")
    close = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close * high_mult,
        "low": close * low_mult,
        "close": close,
        "volume": pd.Series(np.full(n, 100.0), index=idx),
    })


def dumping_df(bars: int = 60, drop_per_bar: float = 0.004, band: float = 0.02):
    """계속 하락하는 캔들 → RSI(2)는 0에 가까워지고, band로 ATR 크기를 조절한다."""
    closes = [10_000 * (1 - drop_per_bar) ** i for i in range(bars)]
    return make_df(closes, high_mult=1 + band / 2, low_mult=1 - band / 2)


def mild_dip_df(bars: int = 60, drop_per_bar: float = 0.004, band: float = 0.02,
                bounce: float = 0.00025):
    """RSI(2)가 3과 7 **사이**(≈5.8)에 놓이는 얕은 눌림 — 진입선 차이를 가르는 캔들.

    마지막 봉만 아주 조금 반등시키면 RSI(2)가 0에서 5~6대로 올라온다.
    5분봉(진입선 3)은 보류하고 15분봉(진입선 7)만 진입해야 한다.
    """
    closes = [10_000 * (1 - drop_per_bar) ** i for i in range(bars)]
    closes[-1] = closes[-2] * (1 + bounce)
    return make_df(closes, high_mult=1 + band / 2, low_mult=1 - band / 2)


def rising_df(bars: int = 60, up_per_bar: float = 0.004, band: float = 0.02):
    """상승하지만 중간중간 작은 눌림이 섞인 현실적인 캔들.

    (전부 상승인 합성 데이터는 RSI 분모가 0이 되어 NaN이 된다 — 실제 시장에는 없는 형태이므로
     테스트 데이터에 작은 하락을 섞는다.)
    """
    closes, p = [], 10_000.0
    for i in range(bars - 2):
        p *= (1 - up_per_bar / 2) if i % 5 == 4 else (1 + up_per_bar)
        closes.append(p)
    closes += [p * 1.006, p * 1.012]      # 마지막 2봉은 상승 → RSI(2) 고점(되돌림 완료)
    return make_df(closes, high_mult=1 + band / 2, low_mult=1 - band / 2)


@pytest.fixture
def strat():
    return Rsi2PullbackStrategy(SPEC)


def test_진입_세조건_모두_충족시_매수(strat):
    df = dumping_df()
    sig = strat.signal(df, {"trend_up": True})
    assert sig.action == Action.ENTER_LONG
    # 사유 문구는 대시보드에 그대로 노출되므로 사람이 읽을 수 있어야 한다
    assert "과매도" in sig.reason and "상승추세" in sig.reason and "변동성" in sig.reason
    assert sig.meta["rsi2"] <= 3 and sig.meta["trend_up"] is True


def test_하락추세면_진입금지(strat):
    """1시간봉 EMA200 아래면 아무리 과매도여도 사지 않는다 (백테스트: 하락장 진입 −0.25%)."""
    df = dumping_df()
    sig = strat.signal(df, {"trend_up": False})
    assert sig.action == Action.HOLD
    assert "하락 추세" in sig.reason


def test_추세정보없으면_진입보류(strat):
    """1시간봉을 못 받았으면 추세 확인 없이 진입하지 않는다."""
    df = dumping_df()
    sig = strat.signal(df, None)
    assert sig.action == Action.HOLD
    assert "추세 판정 불가" in sig.reason


def test_변동성_게이트_미달시_진입금지(strat):
    """ATR/가격이 0.6% 미만이면 되돌림 폭이 비용을 못 넘으므로 진입 금지."""
    df = dumping_df(drop_per_bar=0.0002, band=0.0005)   # 아주 조용한 하락
    sig = strat.signal(df, {"trend_up": True})
    assert sig.action == Action.HOLD
    assert "변동성 부족" in sig.reason


def test_과매도_아니면_보류(strat):
    df = make_df([10_000 + i for i in range(40)] + [10_030] * 5)   # RSI2 중간대
    sig = strat.signal(df, {"trend_up": True})
    assert sig.action == Action.HOLD


def test_되돌림_완료시_청산(strat):
    df = rising_df()
    sig = strat.signal(df, {"trend_up": True})
    assert sig.action == Action.EXIT
    assert "되돌림 완료" in sig.reason


def test_봉수_부족하면_보류(strat):
    assert strat.signal(make_df([100] * 10), {"trend_up": True}).action == Action.HOLD


def test_rsi가_NaN이면_보류(strat):
    """하락이 전혀 없어 RSI 분모가 0인 경우(합성 데이터) — 매매하지 않고 보류한다."""
    df = make_df([10_000 * 1.004 ** i for i in range(40)])
    sig = strat.signal(df, {"trend_up": True})
    assert sig.action == Action.HOLD


def test_ctx_없어도_장기캔들이면_자체계산(strat):
    """백테스트 경로: 2400봉 이상이면 5분봉으로 추세를 직접 계산한다."""
    closes = [10_000 * (1 + 0.0005) ** i for i in range(2500)]
    closes += [closes[-1] * (1 - 0.004) ** i for i in range(1, 30)]   # 마지막에 급락
    df = make_df(closes, high_mult=1.01, low_mult=0.99)
    sig = strat.signal(df, None)
    assert sig.action == Action.ENTER_LONG      # 상승 추세 + 과매도 + 변동성 충분


# ── v2.3: 진입선을 스펙에서 읽는다 (5분봉 3 / 15분봉 7) ──────
def test_진입선은_스펙에서_읽는다():
    """
    같은 캔들(RSI2≈5.8)에서 5분봉은 보류하고 15분봉은 진입해야 한다.

    모듈 상수(ENTRY_LEVEL)를 쓰면 두 전략이 같은 판정을 내므로 이 테스트가 깨진다 —
    즉 이 테스트가 '진입선이 스펙에서 온다'는 사실을 지킨다.
    """
    df = mild_dip_df()
    rsi2 = Rsi2PullbackStrategy(SPEC).signal(df, {"trend_up": True}).meta["rsi2"]
    assert 3.0 < rsi2 < 7.0, f"테스트 캔들의 RSI2({rsi2})가 두 진입선 사이가 아니다"

    five = Rsi2PullbackStrategy(SPEC).signal(df, {"trend_up": True})
    assert five.action == Action.HOLD
    assert "진입선 3" in five.reason

    fifteen = Rsi2PullbackStrategy(SPEC_15M).signal(df, {"trend_up": True})
    assert fifteen.action == Action.ENTER_LONG


def test_진입선을_meta로_노출한다():
    """대시보드 진입 진단이 전략별 진입선을 표시하려면 meta에 있어야 한다 (v2.3)."""
    df = dumping_df()
    assert Rsi2PullbackStrategy(SPEC).signal(df, {"trend_up": True}).meta["entry"] == 3.0
    m15 = Rsi2PullbackStrategy(SPEC_15M).signal(df, {"trend_up": True}).meta
    assert m15["entry"] == 7.0 and m15["gate"] == 0.004   # 게이트는 v2.6 테스트값


def test_15분봉_진입선_초과는_보류():
    """15분봉도 진입선(7)을 넘으면 보류한다 — 게이트만 느슨해진 게 아님을 확인."""
    df = mild_dip_df(bounce=0.001)          # RSI2 ≈ 19.9
    sig = Rsi2PullbackStrategy(SPEC_15M).signal(df, {"trend_up": True})
    assert sig.action == Action.HOLD
    assert "진입선 7" in sig.reason


def test_15분봉_변동성_게이트_위에서는_진입():
    """게이트(v2.6 테스트값 0.4%) 위 변동성(0.83%~1.0%)에서 15분봉은 진입한다."""
    strat = Rsi2PullbackStrategy(SPEC_15M)
    sig = strat.signal(dumping_df(drop_per_bar=0.004, band=0.009), {"trend_up": True})
    assert sig.meta["atr_pct"] is not None
    assert 0.83 <= sig.meta["atr_pct"] < 1.0, sig.meta["atr_pct"]
    assert sig.action == Action.ENTER_LONG      # 옛 게이트(1.0%)라면 막혔을 구간


# ── 스펙(헌장 값)이 검증된 설정과 일치하는지 ─────────────────
def test_스펙이_검증된_값과_일치():
    assert SPEC.stop_pct == 0.025           # 손절/익절 2.5% 고정
    assert SPEC.rr == 1.0
    assert SPEC.min_atr_ratio == 0.003      # 변동성 게이트 0.3% (v2.6 테스트값, 검증값은 0.006)
    assert SPEC.entry_level == 3.0           # 진입선 RSI(2) ≤ 3
    assert time_stop_bars_for(SPEC) == 96   # 8시간
    assert SPEC.use_dead_extras is False    # 부가 청산 규칙 미사용(백테스트 재현성)
    assert SPEC.always_active is True       # 레짐 필터 미적용


def test_고정손절이_ATR을_무시한다():
    """stop_pct 가 있으면 ATR·MIN_STOP_RATIO 와 무관하게 2.5%."""
    assert stop_ratio_for(SPEC, entry_atr=1.0, entry_price=10_000) == 0.025
    assert stop_ratio_for(SPEC, entry_atr=0.0, entry_price=10_000) == 0.025


# ── 1시간봉 추세 판정 ────────────────────────────────────────
def make_hourly(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="1h")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c,
                         "volume": pd.Series(1.0, index=idx)})


def test_1시간봉_추세_상승():
    up = make_hourly([100 * (1 + 0.002) ** i for i in range(260)])
    assert trend_up_from_hourly(up) is True


def test_1시간봉_추세_하락():
    down = make_hourly([100 * (1 - 0.002) ** i for i in range(260)])
    assert trend_up_from_hourly(down) is False


def test_1시간봉_부족하면_None():
    assert trend_up_from_hourly(make_hourly([100] * 50)) is None
    assert trend_up_from_hourly(None) is None


def test_진행중인_1시간봉은_판정에서_제외():
    """마지막 봉(진행 중)에 이상값이 와도 판정이 흔들리지 않아야 한다."""
    closes = [100 * (1 + 0.002) ** i for i in range(260)]
    normal = trend_up_from_hourly(make_hourly(closes))
    spiked = trend_up_from_hourly(make_hourly(closes + [0.01]))   # 진행봉 폭락
    assert normal is True and spiked is True


def test_rsi_ok는_반올림_전_값으로_판정한다():
    """
    대시보드 모순 방지 — RSI2 7.032 는 표시값이 7.0 이라 화면에서 다시 비교하면 '≤7 ✅'가
    되지만 봇은 진입하지 않는다. 실제로 그 행이 'RSI2 7.0 / ≤7 ✅ + 과매도 대기'로 관측됐다.
    판정 결과 자체를 meta로 내려보내 화면이 다시 계산하지 않게 한다.
    """
    sig = Rsi2PullbackStrategy(SPEC_15M).signal(mild_dip_df(bounce=0.000305),
                                                {"trend_up": True})
    assert sig.meta["rsi2"] == 7.0            # 표시값은 진입선과 같아 보이고
    assert sig.action == Action.HOLD          # 봇은 진입하지 않는다 (실제 7.032)
    assert sig.meta["rsi_ok"] is False        # 화면도 ✅ 를 켜면 안 된다


def test_rsi_ok는_진입할_때_참이다():
    sig = Rsi2PullbackStrategy(SPEC_15M).signal(dumping_df(), {"trend_up": True})
    assert sig.action == Action.ENTER_LONG and sig.meta["rsi_ok"] is True

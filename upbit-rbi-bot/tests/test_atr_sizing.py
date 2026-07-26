"""ATR 스톱거리 기반 포지션 사이징 (헌장 v1.2 §7)."""
from config import charter as C


def test_size_is_capital_1pct_over_stop_ratio():
    # 자본 90k, 1거래 리스크 1%(900원), 손절거리 3% → 900/0.03 = 30,000원
    assert C.position_size_krw(0.03, 90_000) == 30_000


def test_equal_risk_across_volatilities_when_not_alloc_capped():
    # 손절거리가 달라도 실제 KRW 리스크(=size×stop)는 자본 1%(900원)로 동일
    cap = 90_000
    for stop in (0.04, 0.05, 0.06):   # 모두 raw < alloc(30,000) 구간
        size = C.position_size_krw(stop, cap, available_krw=10_000_000)
        assert round(size * stop) == 900


def test_alloc_cap_limits_size():
    # 손절거리가 아주 작으면 전략당 배분 상한(자본 1/3)에 걸린다
    assert C.position_size_krw(0.01, 90_000, available_krw=10_000_000) == 30_000


def test_available_krw_caps_size():
    assert C.position_size_krw(0.03, 90_000, available_krw=12_000) == 12_000


def test_zero_or_negative_stop_ratio_returns_zero():
    assert C.position_size_krw(0.0, 90_000) == 0.0
    assert C.position_size_krw(-0.01, 90_000) == 0.0

"""ATR 스톱거리 기반 포지션 사이징 (헌장 v1.2 §7)."""
from config import charter as C


def test_size_is_risk_budget_over_stop_ratio():
    """포지션 크기 = 1거래리스크 ÷ 손절거리 (배분상한·잔고에 걸리지 않는 한)."""
    cap, stop = 90_000, 0.03
    expected = min(cap * C.RISK_PER_TRADE_RATIO / stop,
                   cap * C.ALLOC_PER_STRATEGY_RATIO)
    assert C.position_size_krw(stop, cap) == expected


def test_equal_risk_across_volatilities_when_not_alloc_capped():
    """손절거리가 달라도 실제 KRW 리스크(=size×stop)는 동일하다 — 배분상한 미적용 구간에서."""
    cap = 90_000
    budget = cap * C.RISK_PER_TRADE_RATIO
    alloc = cap * C.ALLOC_PER_STRATEGY_RATIO
    tested = 0
    for stop in (0.04, 0.05, 0.06, 0.08, 0.10):
        if budget / stop >= alloc:        # 배분상한이 구속하면 이 검증 대상이 아니다
            continue
        size = C.position_size_krw(stop, cap, available_krw=10_000_000)
        assert round(size * stop) == round(budget)
        tested += 1
    assert tested > 0, "배분상한 미적용 구간이 하나도 없다 — 파라미터 조합을 확인할 것"


def test_alloc_cap_limits_size():
    """손절거리가 아주 작으면 전략당 배분 상한에 걸린다."""
    cap = 90_000
    assert C.position_size_krw(0.001, cap, available_krw=10_000_000) == \
        cap * C.ALLOC_PER_STRATEGY_RATIO


def test_available_krw_caps_size():
    assert C.position_size_krw(0.03, 90_000, available_krw=12_000) == 12_000


def test_zero_or_negative_stop_ratio_returns_zero():
    assert C.position_size_krw(0.0, 90_000) == 0.0
    assert C.position_size_krw(-0.01, 90_000) == 0.0


def test_stop_ratio_from_atr_normal():
    assert C.stop_ratio_from_atr(1.5, 2.0, 100.0) == 0.03


def test_stop_ratio_from_atr_fallback_when_atr_non_positive():
    assert C.stop_ratio_from_atr(1.5, 0.0, 100.0) == C.FALLBACK_STOP_RATIO


def test_stop_ratio_from_atr_fallback_when_price_non_positive():
    assert C.stop_ratio_from_atr(1.5, 2.0, 0.0) == C.FALLBACK_STOP_RATIO


def test_stop_ratio_floored_to_min_when_atr_tiny():
    # 5분봉 저변동 BTC: k×ATR/price ≈ 0.00008 (수수료 0.1% 밑) → MIN_STOP_RATIO 로 바닥
    tiny = C.stop_ratio_from_atr(1.5, 500.0, 94_000_000.0)
    assert tiny == C.MIN_STOP_RATIO
    assert C.MIN_STOP_RATIO > C.FEE_ROUNDTRIP   # 반드시 왕복 수수료보다 커야 함


def test_stop_ratio_not_floored_when_atr_large_enough():
    # k×ATR/price = 0.03 > MIN_STOP_RATIO → 그대로 유지(변동성 큰 코인은 ATR 존중)
    assert C.stop_ratio_from_atr(1.5, 2.0, 100.0) == 0.03

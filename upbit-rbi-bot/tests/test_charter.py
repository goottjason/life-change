"""
헌장 규칙이 코드에 정확히 반영됐는지 검증 (핵심 값의 회귀 방지).
자본은 동적(계좌 잔고)이므로 함수에 capital 을 넣어 검증한다.
    pytest tests/
"""
from config import charter as C

CAP = 90_000  # 기준 자본(예시): 계좌에 9만원 있을 때


def test_risk_per_trade_scales_with_capital():
    # §5.1: 1거래 최대손실 = 자본 1%
    assert C.max_loss_per_trade_krw(90_000) == 900
    assert C.max_loss_per_trade_krw(300_000) == 3_000  # 입금하면 커진다


def test_position_size_matches_stop_loss():
    # §7.2: 손절 -3% → 30,000원, 손절 -6% → 15,000원 (capital=90k)
    assert C.position_size_krw(0.03, CAP) == 30_000
    assert C.position_size_krw(0.06, CAP) == 15_000


def test_position_size_scales_with_capital():
    # 자본이 3배면 포지션도 3배
    assert C.position_size_krw(0.03, 270_000) == 90_000


def test_position_size_capped_by_available_krw():
    # 주문가능 원화가 부족하면 그 이하로 clamp
    assert C.position_size_krw(0.03, CAP, available_krw=12_000) == 12_000


def test_daily_loss_limit_scales():
    # §5.2
    assert C.daily_loss_limit_krw(90_000) == 2_700
    assert C.daily_loss_limit_krw(200_000) == 6_000


def test_strategy_risk_reward_ge_1_5():
    # §4: 모든 전략 손익비 >= 1.5
    for spec in C.STRATEGY_SPECS.values():
        assert spec.risk_reward >= 1.5, spec.name


def test_tiny_capital_returns_below_min_order():
    # 자본/잔고가 너무 작으면 MIN_ORDER 미만을 반환(강제로 올리지 않음) → 호출측이 스킵
    assert C.position_size_krw(0.03, 100_000, available_krw=1_000) < C.MIN_ORDER_KRW

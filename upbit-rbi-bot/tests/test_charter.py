"""
헌장 규칙이 코드에 정확히 반영됐는지 검증 (핵심 값의 회귀 방지).
    pytest tests/
"""
from config import charter as C


def test_risk_per_trade_is_900():
    # §5.1: 1거래 최대손실 = 자본 1% = 900원
    assert C.max_loss_per_trade_krw() == 900


def test_position_size_matches_stop_loss():
    # §7.2: 손절 -3% → 30,000원, 손절 -6% → 15,000원
    assert C.position_size_krw(0.03) == 30_000
    assert C.position_size_krw(0.06) == 15_000


def test_daily_loss_limit_is_2700():
    # §5.2
    assert C.daily_loss_limit_krw() == 2_700


def test_strategy_risk_reward_ge_1_5():
    # §4: 모든 전략 손익비 >= 1.5
    for spec in C.STRATEGY_SPECS.values():
        assert spec.risk_reward >= 1.5, spec.name


def test_position_size_respects_min_order():
    # §6.5: 최소주문금액 이상
    assert C.position_size_krw(0.5) >= C.MIN_ORDER_KRW

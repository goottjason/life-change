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


def test_strategy_risk_reward_rule():
    """
    §4 손익비 규칙 (v1.3 개정).

    v1.2까지는 '모든 전략 손익비 ≥1.5'였다. 그러나 손익비만으로는 기댓값을 보장하지 못하고,
    반대로 승률이 높은 평균회귀 전략(손익비 1.0)이 더 나은 기댓값을 낼 수 있다.
    실제로 손익비 1.6~2.0인 macd/rsi/cvd는 전부 음의 기댓값이었고,
    손익비 1.0인 rsi2(승률 69%)만 §11을 통과했다.

    개정 규칙: ATR 기반 전략은 손익비 ≥1.5, **고정 손절(stop_pct) 전략은 손익비 ≥1.0 +
    백테스트로 양의 기댓값 입증**. 손익분기 승률 = 1/(1+rr) 을 넘겨야 한다.
    """
    for spec in C.STRATEGY_SPECS.values():
        if spec.stop_pct is None:
            assert spec.risk_reward >= 1.5, spec.name
        else:
            assert spec.risk_reward >= 1.0, spec.name
            breakeven_wr = 1 / (1 + spec.risk_reward)     # 수수료 제외 손익분기 승률
            assert breakeven_wr <= 0.55, f"{spec.name}: 요구 승률 {breakeven_wr:.0%} 과도"


def test_rsi2_스펙_검증값():
    """검증된 설정이 헌장에 그대로 들어갔는지 (backtesting/research/README.md)."""
    spec = C.STRATEGY_SPECS["rsi2"]
    assert (spec.stop_pct, spec.rr, spec.min_atr_ratio) == (0.025, 1.0, 0.006)
    assert C.time_stop_bars_for(spec) == 96
    assert C.ACTIVE_STRATEGIES == ("rsi2",)


def test_스프레드_상한과_추세갱신주기():
    assert C.MAX_SPREAD_RATIO == 0.001        # §3 v1.3
    assert C.SPREAD_CANDIDATE_MULT >= 2
    assert C.TREND_REFRESH_SEC == 900         # §2 v1.3


def test_tiny_capital_returns_below_min_order():
    # 자본/잔고가 너무 작으면 MIN_ORDER 미만을 반환(강제로 올리지 않음) → 호출측이 스킵
    assert C.position_size_krw(0.03, 100_000, available_krw=1_000) < C.MIN_ORDER_KRW

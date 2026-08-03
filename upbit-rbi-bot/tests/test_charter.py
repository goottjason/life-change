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
    """검증된 설정이 헌장에 그대로 들어갔는지 (backtesting/research/README.md).

    v2.3에서 15분봉만 진입선/게이트를 바꿨다 — 5분봉은 검증값 그대로여야 한다.
    """
    spec = C.STRATEGY_SPECS["rsi2"]
    assert (spec.stop_pct, spec.rr, spec.min_atr_ratio) == (0.025, 1.0, 0.003)  # 게이트는 v2.6 테스트값
    assert spec.entry_level == 3.0                # 진입선 RSI(2) ≤ 3 (v1.3 검증값)
    assert C.time_stop_bars_for(spec) == 96
    assert spec.timeframe == "minute5"


def test_rsi2_15m_스펙_검증값():
    """15분봉 병행(v1.4) · 진입선·게이트 개정(v2.3).

    v2.3 근거(선택구간 209일·보수적 스프레드): th=7/gate 0.83% → 59거래 승률 81.4%
    보수적 기댓값 +0.2585% vs 기존 th=3/gate 1.00% → 7거래 승률 42.9% −0.3378%.
    통계적 입증은 아니다(walk-forward t=+1.08) — 측정된 최선값이다.
    """
    spec = C.STRATEGY_SPECS["rsi2_15m"]
    assert (spec.stop_pct, spec.rr, spec.min_atr_ratio) == (0.030, 1.0, 0.004)  # 게이트는 v2.6 테스트값
    assert spec.entry_level == 7.0                # 진입선 RSI(2) ≤ 7 (v2.3)
    assert C.time_stop_bars_for(spec) == 32       # 8시간 = 15분 × 32봉
    assert spec.timeframe == "minute15"
    assert spec.use_dead_extras is False and spec.always_active is True


def test_두_전략의_진입선은_분리돼_있어야_한다():
    """
    v2.3 회귀 방지 — 진입선은 v2.2까지 `strategies.rsi2_pullback.ENTRY_LEVEL` 모듈 상수를
    두 전략이 공유했다. 리팩터링으로 다시 공유 상수로 합쳐지면 15분봉 진입선이 조용히
    7 → 3 으로 되돌아가 거래가 거의 사라진다(측정: 209일 59거래 → 7거래).
    그래서 '두 스펙의 진입선이 서로 다르다'는 사실 자체를 고정한다.
    """
    five = C.STRATEGY_SPECS["rsi2"]
    fifteen = C.STRATEGY_SPECS["rsi2_15m"]
    assert five.entry_level != fifteen.entry_level, "두 전략이 진입선을 공유하고 있다"
    assert five.min_atr_ratio != fifteen.min_atr_ratio, "두 전략이 변동성 게이트를 공유하고 있다"
    # 스펙 기본값은 5분봉 검증값과 같아야 한다(지정하지 않은 전략의 동작이 바뀌지 않도록)
    assert C.StrategySpec("x", atr_stop_mult=1.0, rr=1.5,
                          regime=C.Regime.RANGE).entry_level == five.entry_level


def test_가동전략_목록():
    assert C.ACTIVE_STRATEGIES == ("rsi2", "rsi2_15m")
    # 시간손절은 두 전략이 같은 실제 시간(8시간)을 쓴다 — 검증 조건과 일치
    assert C.time_stop_bars_for(C.STRATEGY_SPECS["rsi2"]) * 5 == \
           C.time_stop_bars_for(C.STRATEGY_SPECS["rsi2_15m"]) * 15


def test_스프레드_상한과_추세갱신주기():
    assert C.MAX_SPREAD_RATIO == 0.001        # §3 v1.3
    assert C.SPREAD_CANDIDATE_MULT >= 2
    assert C.TREND_REFRESH_SEC == 900         # §2 v1.3


def test_tiny_capital_returns_below_min_order():
    # 자본/잔고가 너무 작으면 MIN_ORDER 미만을 반환(강제로 올리지 않음) → 호출측이 스킵
    assert C.position_size_krw(0.03, 100_000, available_krw=1_000) < C.MIN_ORDER_KRW


def test_대시보드_전략세대_분리():
    """
    v1.3에서 전략을 완전히 교체했으므로 대시보드 기본 조회는 현재 가동 전략만 봐야 한다
    (과거 macd/rsi/cvd 기록이 섞이면 승률·손익이 무의미해진다).
    """
    from dashboard.service import BotService
    from types import SimpleNamespace

    # _scope_clause 는 '지금 가동 중인' 전략(런타임 토글 반영)을 본다 → trader 스텁으로 주입
    svc = SimpleNamespace(trader=SimpleNamespace(
        strategies={n: None for n in C.ACTIVE_STRATEGIES}))
    clause, params = BotService._scope_clause(svc, "current")
    assert "strategy IN" in clause and set(params) == set(C.ACTIVE_STRATEGIES)
    assert BotService._scope_clause(svc, "all") == ("", ())

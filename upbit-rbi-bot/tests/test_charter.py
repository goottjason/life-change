"""
헌장 규칙이 코드에 정확히 반영됐는지 검증 (핵심 값의 회귀 방지).
자본은 동적(계좌 잔고)이므로 함수에 capital 을 넣어 검증한다.
    pytest tests/
"""
from config import charter as C

CAP = 90_000  # 기준 자본(예시): 계좌에 9만원 있을 때


def test_risk_per_trade_scales_with_capital():
    """§5.1 — 1거래 최대손실은 자본에 비례한다."""
    assert C.max_loss_per_trade_krw(90_000) == 90_000 * C.RISK_PER_TRADE_RATIO
    assert C.max_loss_per_trade_krw(300_000) == 300_000 * C.RISK_PER_TRADE_RATIO


def test_position_size_matches_stop_loss():
    """§7.2 — 포지션 크기 × 손절거리 = 1거래 리스크 (배분상한에 걸리지 않는 한)."""
    for stop in (0.03, 0.06):
        size = C.position_size_krw(stop, CAP)
        alloc_cap = CAP * C.ALLOC_PER_STRATEGY_RATIO
        if size < alloc_cap:                     # 리스크 공식이 구속하는 경우
            assert abs(size * stop - CAP * C.RISK_PER_TRADE_RATIO) < 1


def test_effective_risk_matches_documented_cap():
    """
    ★ 2026-08-06 교훈 — **실효 리스크 = min(f, 배분상한 × 손절거리)** 다.
    배분상한이 먼저 걸리면 `RISK_PER_TRADE_RATIO` 를 올려도 아무 효과가 없다.

    v4.0 개정: 실험 예산(3×10,000원) 확보를 위해 배분을 2/3 로 **의도적으로** 캡했다
    (스펙 docs/superpowers/specs/2026-08-14-…, 운영자 승인). 따라서 "실효 f ≈ 명목 f" 를
    강제하던 이전 단언은 폐기하고, 실효 f 가 **문서화된 값(min 공식)과 일치**하는지 검증한다.
    조용히 어긋나는 것을 막는 게 목적이지, 캡 자체를 금지하는 게 아니다.
    """
    for name in C.ACTIVE_STRATEGIES:
        if name in C.EXPERIMENTAL_STRATEGIES:
            continue        # 실험 트랙은 f 사이징이 아니라 고정 상한(position_cap_for)을 쓴다
        spec = C.STRATEGY_SPECS[name]
        stop = spec.stop_pct or (spec.atr_stop_mult * 0.01) or C.FALLBACK_STOP_RATIO
        size = C.position_size_krw(stop, CAP)
        effective = size * stop / CAP
        documented = min(C.RISK_PER_TRADE_RATIO, C.ALLOC_PER_STRATEGY_RATIO * stop)
        assert abs(effective - documented) < 1e-4, (
            f"{name}: 실효 f {effective:.4%} 가 문서화된 min(f, 배분×손절) "
            f"= {documented:.4%} 와 다르다 — 배분/손절/f 중 무엇이 바뀌었는지 확인할 것")


def test_allocation_does_not_exceed_capital():
    """배분 불변식 (v4.2 재정의): **각 트랙의 첫 슬롯은 항상 동시에 열릴 수 있어야 한다.**
    검증 배분 + 실험 1건 ≤ 자본. v4.0의 '실험 전 슬롯 합산' 불변식은 건당 3만원 상향으로
    폐기 — 실험 2·3번째 슬롯은 주문가능 원화 클램프가 선착순으로 줄이거나 건너뛴다
    (현물·무레버리지라 초과 배정은 '주문 축소'로만 나타나고 빚이 되지 않는다)."""
    validated = C.ALLOC_PER_STRATEGY_RATIO * C.MAX_POSITIONS_VALIDATED
    first_experimental = C.EXPERIMENT_MAX_ORDER_KRW / C.DEFAULT_CAPITAL_KRW
    assert validated + first_experimental <= 1.0 + 1e-9


def test_position_size_scales_with_capital():
    """자본이 3배면 포지션도 3배 (상한 종류와 무관하게 둘 다 자본 비례이므로 성립)."""
    assert C.position_size_krw(0.03, 270_000) == 3 * C.position_size_krw(0.03, 90_000)


def test_position_size_capped_by_available_krw():
    # 주문가능 원화가 부족하면 그 이하로 clamp
    assert C.position_size_krw(0.03, CAP, available_krw=12_000) == 12_000


def test_daily_loss_limit_scales():
    """§5.2 — 자본에 비례한다."""
    assert C.daily_loss_limit_krw(200_000) == 2 * C.daily_loss_limit_krw(100_000)
    assert C.daily_loss_limit_krw(90_000) == 90_000 * C.DAILY_LOSS_LIMIT_RATIO


def test_v4_absolute_circuit_values():
    """
    §5 v4.0 — 서킷은 **절대값**이다 (운영자 결정 2026-08-14).
    v3.0 은 R(=f 배수) 단위였지만, 두 트랙(검증 f 기반 + 실험 고정금액)이 한 계좌를
    공유하면서 R 정의가 성립하지 않게 됐다. 값은 운영자가 직접 골랐다:
    "천천히 잃으면서 배우는 것과 하루에 다 잃는 것은 배움의 양이 다르다."
    """
    assert C.DAILY_LOSS_LIMIT_RATIO == 0.05
    assert C.MAX_DRAWDOWN_RATIO == 0.50
    assert C.MAX_CONSECUTIVE_LOSSES == 12
    # MDD 정지선은 일일한도보다 충분히 커야 한다(하루치로 전면정지가 걸리면 안 된다)
    assert C.MAX_DRAWDOWN_RATIO > C.DAILY_LOSS_LIMIT_RATIO * 2


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
        if spec.trail_atr_mult > 0:
            continue    # v4.0: 트레일링 청산 전략(breakout)은 고정 rr 을 쓰지 않는다 (rr=0.0)
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
    # 게이트 0.6% = 검증값. 0.3%(v2.6 테스트값)는 백테스트에서 **−0.050%(음수)** 인 지점이다
    # (0.30%→−0.050 · 0.50%→+0.142 · 0.60%→+0.199 · 0.70%→+0.206). 근거 없이 낮추지 말 것.
    assert (spec.stop_pct, spec.rr, spec.min_atr_ratio) == (0.025, 1.0, 0.006)
    assert spec.entry_level == 3.0                # 진입선 RSI(2) ≤ 3 (v1.3 검증값)
    assert C.time_stop_bars_for(spec) == 96
    assert spec.timeframe == "minute5"


def test_rsi2_15m_스펙_검증값():
    """15분봉 병행(v1.4) · 진입선·게이트 개정(v2.3).

    v2.3에서 진입선 3→7·게이트 1.0%→0.83% 로 바꿨다가 **v3.0에서 취소**했다.
    v2.3은 209일 선택구간에서 고른 값이었고 walk-forward t=+1.08 로 스스로 미입증이라
    적혀 있었다. 2년 재측정에서 개정 전이 모든 조합에서 우세했다(lab_exit_timing.py).
    """
    spec = C.STRATEGY_SPECS["rsi2_15m"]
    # v3.0 — v2.3 개정(진입7·게이트0.83%)을 취소하고 개정 전 값으로 복원.
    # 2년 재측정: 개정 전 +0.457%(6종목,t+3.58) vs v2.3 +0.130%(t+2.16)·전체 −0.026%.
    assert (spec.stop_pct, spec.rr, spec.min_atr_ratio) == (0.030, 1.0, 0.010)
    assert spec.entry_level == 3.0                # 진입선 RSI(2) ≤ 3 (개정 전 검증값)
    assert C.time_stop_bars_for(spec) == 32       # 8시간 = 15분 × 32봉
    assert spec.timeframe == "minute15"
    assert spec.use_dead_extras is False and spec.always_active is True


def test_두_전략의_진입선은_분리돼_있어야_한다():
    """
    진입선·게이트는 v2.2까지 모듈 상수를 두 전략이 공유했다. 스펙에서 읽도록 분리한 구조가
    유지되는지 본다. v3.0부터 **진입선은 두 전략이 같은 값(3.0)** 이므로 값 비교로는 이
    회귀를 잡을 수 없다 → 동작 수준 검증은 test_rsi2_strategy.py 가 담당하고,
    여기서는 '게이트가 타임프레임별로 다르다'는 사실만 고정한다.
    """
    five = C.STRATEGY_SPECS["rsi2"]
    fifteen = C.STRATEGY_SPECS["rsi2_15m"]
    assert five.min_atr_ratio != fifteen.min_atr_ratio, "두 전략이 변동성 게이트를 공유하고 있다"
    assert fifteen.min_atr_ratio > five.min_atr_ratio, "15분봉 게이트가 5분봉보다 높아야 한다"
    # 스펙 기본값은 5분봉 검증값과 같아야 한다(지정하지 않은 전략의 동작이 바뀌지 않도록)
    assert C.StrategySpec("x", atr_stop_mult=1.0, rr=1.5,
                          regime=C.Regime.RANGE).entry_level == five.entry_level


def test_가동전략_목록():
    # v4.0: breakout 은 EXPERIMENTAL — §11 미통과 상태로 가동하되 주문 상한이 강제된다
    assert C.ACTIVE_STRATEGIES == ("rsi2", "rsi2_15m", "breakout")
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


# ── v3.0 회귀 테스트: 2026-08-06 에 고친 서킷 버그 두 개 ──────────
def test_daily_pnl_auto_resets_on_date_boundary():
    """
    ⚠ 회귀: 이전 버전은 `reset_daily()` 가 **리포 어디에서도 호출되지 않았다.**
    그래서 daily_pnl 이 영구 누적됐고, 누적 손실이 한도를 넘는 순간 '일일' 한도가
    사실상 **영구 정지**로 변했다. can_enter() 가 날짜를 보고 스스로 풀어야 한다.
    """
    from bot.risk_manager import RiskManager, RiskState
    r = RiskManager(RiskState(capital=100_000, available_krw=100_000))
    r.on_close(-C.daily_loss_limit_krw(100_000) * 2)      # 한도의 2배를 잃는다
    r.s.daily_date = "어제"                                # 날짜 경계를 넘긴 상황
    ok, why = r.can_enter()
    assert ok, f"날짜가 바뀌면 일일 한도는 풀려야 한다: {why}"
    assert r.s.daily_pnl == 0.0


def test_consecutive_loss_circuit_cannot_deadlock():
    """
    ⚠ 회귀: 연속손절로 차단되면 신규 진입이 없어 '승리'가 나올 수 없고,
    그러면 카운터가 영영 리셋되지 않아 **영구 잠김**이 된다.
    날짜 경계에서 반드시 풀려야 한다.
    """
    from bot.risk_manager import RiskManager, RiskState
    r = RiskManager(RiskState(capital=100_000, available_krw=100_000))
    for _ in range(C.MAX_CONSECUTIVE_LOSSES):
        r.on_close(-100)
    r._roll_day()                                          # 같은 날에는 차단이 유지된다
    r.s.daily_date = "어제"
    ok, _ = r.can_enter()
    assert ok, "연속손절 차단은 익일 자동 해제되어야 한다(데드락 금지)"
    assert r.s.consecutive_losses == 0


def test_fingerprint_covers_risk_params():
    """
    §9.7 v3.0 — 승인 지문은 **§5 리스크 파라미터까지** 덮어야 한다.
    이전 버전은 전략 스펙만 해싱해서, f·MDD 정지선을 바꿔도 이전 승인이 그대로 통했다.
    (이 게이트가 생긴 계기가 '검증 0건 설정이 이전 승인으로 실계좌에서 돈' 사건이다)
    """
    import importlib
    from config import charter as ch
    before = ch.charter_fingerprint()
    orig = ch.RISK_PER_TRADE_RATIO
    try:
        ch.RISK_PER_TRADE_RATIO = orig * 2
        assert ch.charter_fingerprint() != before, "f 를 바꿨는데 지문이 그대로다"
    finally:
        ch.RISK_PER_TRADE_RATIO = orig
    assert ch.charter_fingerprint() == before


def test_fingerprint_covers_allocation():
    """
    ★ 2026-08-06 회귀 — 배분비율은 실효 리스크를 직접 결정하므로 승인 지문에 들어가야 한다.
    (f 를 올려도 배분상한이 먼저 걸리면 실효 리스크가 안 바뀐다 — 조용히 지나가면 안 된다)
    """
    from config import charter as ch
    before = ch.charter_fingerprint()
    orig = ch.ALLOC_PER_STRATEGY_RATIO
    try:
        ch.ALLOC_PER_STRATEGY_RATIO = orig / 2
        assert ch.charter_fingerprint() != before
    finally:
        ch.ALLOC_PER_STRATEGY_RATIO = orig
    assert ch.charter_fingerprint() == before


# ── 인큐베이션 활동 지표 (2026-08-08) ────────────────────────────
def test_activity_reports_facts_not_stale_expectations(tmp_path):
    """
    ★ 2026-08-08 회귀 — "거래가 없는데 고장인가?"에 **사실**로 답해야 한다.
    이전 리포트는 "하루 약 1회가 정상"이라는 **낡은 백테스트 기대치**를 문구로 박아뒀는데,
    08-04 게이트 복원 뒤 실제 빈도가 하루 0.39건으로 떨어지면서 운영자가 고장으로 오인했다.
    """
    import sqlite3
    from datetime import datetime, timezone, timedelta
    from incubation import progress as P

    db = tmp_path / "t.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, "
                "event TEXT, strategy TEXT, market TEXT, price REAL, volume REAL, "
                "size_krw REAL, pnl_krw REAL, reason TEXT, fill_price REAL)")
    now = datetime.now(timezone.utc) + timedelta(hours=9)
    for days_ago in (2, 20, 200):          # 7일내 0건 · 30일내 2건 · 90일내 2건
        con.execute("INSERT INTO trades (ts,event,strategy,market,price,volume,size_krw,"
                    "pnl_krw,reason) VALUES (?,'entry','rsi2','KRW-XRP',1,1,1000,0,'x')",
                    ((now - timedelta(days=days_ago)).isoformat(timespec="minutes"),))
    con.commit(); con.close()

    a = P.activity(str(db))
    assert a["n7"] == 1 and a["n30"] == 2 and a["n90"] == 2
    assert 1.5 < a["days_since"] < 2.5

    # 30일 0건이면 '조용한 국면'이 아니라 점검 경보를 띄워야 한다
    db2 = tmp_path / "t2.sqlite"
    con = sqlite3.connect(db2)
    con.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, "
                "event TEXT, strategy TEXT, market TEXT, price REAL, volume REAL, "
                "size_krw REAL, pnl_krw REAL, reason TEXT, fill_price REAL)")
    con.execute("INSERT INTO trades (ts,event,strategy,market,price,volume,size_krw,"
                "pnl_krw,reason) VALUES (?,'entry','rsi2','KRW-XRP',1,1,1000,0,'x')",
                ((now - timedelta(days=45)).isoformat(timespec="minutes"),))
    con.commit(); con.close()
    rep = P.report(str(db2))
    assert any(str(P.GAP_ALERT_DAYS) in n or "점검" in n for n in rep["notes"]), rep["notes"]


def test_gap_thresholds_match_measured_distribution():
    """
    ★ 2026-08-13 — 무거래 공백 임계값은 **실측 간격 분포**에서 나와야 한다.
    rsi2 는 몰아서 거래하는 전략이라 2년간 11일+ 공백이 16회, 최대 104일이었다.
    포아송으로 계산하면 11일 공백이 1.4% 로 나와 '고장'으로 오판한다(실제는 상위 6.2%).
    """
    from incubation import progress as P
    assert P.GAP_INFO_DAYS >= 14, "실측 95분위(17일)보다 낮으면 상시 안내가 뜬다"
    assert P.GAP_ALERT_DAYS >= 39, "실측 99분위(38.8일) 이상이어야 오발하지 않는다"
    assert P.GAP_ALERT_DAYS > P.GAP_INFO_DAYS


def test_fingerprint_covers_universe():
    """
    ★ 2026-08-13 v3.1 — 승인 지문은 **거래 대상 유니버스**까지 덮어야 한다.
    종목 추가·스프레드 상한 변경은 '실제로 무엇을 사는지'를 바꾼다.
    v3.0 까지는 빠져 있어 DOGE 편입이 이전 승인으로 통과할 뻔했다.
    """
    from config import charter as ch
    before = ch.charter_fingerprint()
    orig = dict(ch.VALIDATED_MARKETS)
    try:
        ch.VALIDATED_MARKETS["ZZZTEST"] = 0.001
        assert ch.charter_fingerprint() != before, "종목을 추가했는데 지문이 그대로다"
    finally:
        ch.VALIDATED_MARKETS.clear(); ch.VALIDATED_MARKETS.update(orig)
    assert ch.charter_fingerprint() == before


def test_validated_symbol_gets_its_own_spread_cap():
    """
    §3.2-h v3.1 — 검증된 종목은 **그 종목의 검증 상한**을 쓴다.
    (DOGE 가 0.002%p 차이로 영구 배제됐던 문제)
    단 해당 전략의 블랙리스트 종목은 완화하지 않는다.
    """
    from config import charter as ch
    assert ch.strategy_spread_cap("rsi2", "KRW-DOGE") == ch.VALIDATED_MARKETS["DOGE"]
    assert ch.strategy_spread_cap("rsi2", "KRW-XRP") == ch.MAX_SPREAD_RATIO
    for sym in ch.STRATEGY_BLACKLIST.get("rsi2", set()):
        assert ch.strategy_spread_cap("rsi2", f"KRW-{sym}") == ch.MAX_SPREAD_RATIO, sym
    assert ch.strategy_spread_cap("rsi2") == ch.MAX_SPREAD_RATIO      # market 없으면 기본값


# ── v4.0 실험 트랙 (2026-08-14, 스펙 docs/superpowers/specs/2026-08-14-…) ──
def test_v4_experimental_track_constants():
    """v4.0: 실험 트랙 — 검증 없이 가동 가능하되 주문금액 상한이 강제되는 공식 실험 차선."""
    assert C.EXPERIMENTAL_STRATEGIES == frozenset({"breakout"})
    assert C.EXPERIMENT_MAX_ORDER_KRW == 30_000   # v4.2: 1만→3만 (운영자 결정 2026-08-22)
    assert C.MAX_POSITIONS_VALIDATED == 1
    assert C.MAX_POSITIONS_EXPERIMENTAL == 3
    assert C.MAX_CONCURRENT_POSITIONS == 4          # 트랙 합
    assert C.track_of("breakout") == "experimental"
    assert C.track_of("rsi2") == "validated"
    assert C.track_of("unknown") == "validated"      # 모르는 전략은 보수적으로 검증 트랙 취급


def test_v4_experiment_order_cap_enforced():
    """실험 트랙은 사이징 결과와 무관하게 10,000원을 넘을 수 없다 (easy_teaching 사고 재발 방지)."""
    assert C.position_cap_for("breakout", 90_000.0) == 30_000.0   # v4.2
    assert C.position_cap_for("breakout", 7_000.0) == 7_000.0
    assert C.position_cap_for("rsi2", 90_000.0) == 90_000.0   # 검증 트랙은 불변


def test_v4_breakout_spec():
    spec = C.STRATEGY_SPECS["breakout"]
    assert spec.timeframe == "minute5"
    # 캘리브레이션 확정값 (lab_breakout.py 2026-08-14: 하루 4.9건 · net −0.189%/거래)
    assert spec.breakout_bars == 288
    assert spec.vol_mult == 5.0
    # v4.1 1차 튜닝: 실거래 22건 검증으로 trail 2.0 → 3.0 (좁은 손절에 털림)
    assert spec.trail_atr_mult == 3.0
    assert spec.time_stop_bars == 12
    assert spec.time_stop_min_profit == 0.003
    assert spec.min_atr_ratio == 0.0        # 변동성 게이트 없음 — rsi2를 죽인 관문을 여기선 안 둔다
    assert spec.always_active is True       # 레짐 필터 없음
    assert spec.use_dead_extras is False
    assert "breakout" in C.ACTIVE_STRATEGIES
    # 신규 필드의 기본값은 기존 전략의 동작을 바꾸지 않아야 한다
    rsi2 = C.STRATEGY_SPECS["rsi2"]
    assert rsi2.breakout_bars == 0 and rsi2.vol_mult == 0.0
    assert rsi2.trail_atr_mult == 0.0 and rsi2.time_stop_min_profit is None


def test_v4_fingerprint_covers_experiment_params():
    """실험 트랙 상한도 승인 대상 — 상한을 몰래 올리면 지문이 바뀌어 모의로 떨어져야 한다."""
    before = C.charter_fingerprint()
    orig = C.EXPERIMENT_MAX_ORDER_KRW
    try:
        C.EXPERIMENT_MAX_ORDER_KRW = 50_000
        assert C.charter_fingerprint() != before
    finally:
        C.EXPERIMENT_MAX_ORDER_KRW = orig
    assert C.charter_fingerprint() == before

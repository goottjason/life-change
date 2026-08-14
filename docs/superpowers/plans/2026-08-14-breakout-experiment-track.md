# 돌파(breakout) 실험 트랙 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 헌장 v4.0 — 고빈도 돌파 전략을 "실험 트랙"(건당 10,000원 강제 상한)으로 신설하고, 거래마다 진입 컨텍스트를 저장해 주간 코호트 튜닝 루프를 만든다.

**Architecture:** 기존 전략 카트리지 구조를 그대로 쓴다. 새 전략 클래스(`strategies/breakout.py`) + 헌장 스펙 + 트랙별 리스크 상한(RiskManager) + 트레일링 스톱(Position) + 컨텍스트 로깅(TradeLogger)/코호트 리포트. 검증 트랙(rsi2 인큐베이션)은 신호·청산 규칙 불변.

**Tech Stack:** Python 3.11+, pandas, sqlite3, pytest. 리포: `upbit-rbi-bot/` (모든 경로는 이 디렉터리 기준. pytest·python 실행도 여기서).

**Spec:** `docs/superpowers/specs/2026-08-14-breakout-experiment-track-design.md`

## Global Constraints

- 매매 파라미터 SSOT는 `config/charter.py`. 값 변경 = 헌장 개정(`CHARTER_VERSION` 버전업 필수).
- 검증 트랙(rsi2·rsi2_15m)의 **신호·청산 동작은 절대 변경 금지** (인큐베이션 표본 오염 방지). 배분 상한(2/3) 변경만 허용되며 이는 스펙 §1에 문서화됨.
- 실험 트랙 주문 상한 10,000원, 검증 트랙 동시 1 / 실험 트랙 동시 3.
- 서킷(절대값): 일 −5% 정지 · 고점 대비 −50% 전면 정지 · 12연패 차단(일 경계 자동 해제) 유지.
- 테스트 실행: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/ -q` (전체는 매 Task 마지막에).
- 커밋 메시지 끝: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- 기존 테스트를 수정할 때는 "왜 기대값이 바뀌는지"를 테스트 주석에 남긴다.

---

### Task 1: 헌장 v4.0 — 실험 트랙 상수·스펙·지문

**Files:**
- Modify: `config/charter.py`
- Modify: `tests/test_charter.py`
- Modify: `tests/test_atr_sizing.py` (실효 리스크 테스트가 있는 파일 — `grep -rn "test_effective_risk_is_not_silently_capped" tests/` 로 위치 확인)
- Modify: `docs/TRADING_CHARTER_KR.md` (개정 이력 추가)

**Interfaces:**
- Produces: `EXPERIMENTAL_STRATEGIES: frozenset[str]`, `EXPERIMENT_MAX_ORDER_KRW: int = 10_000`, `MAX_POSITIONS_VALIDATED: int = 1`, `MAX_POSITIONS_EXPERIMENTAL: int = 3`, `track_of(strategy: str) -> str` ("validated"|"experimental"), 확장된 `position_cap_for(strategy, krw)`, `StrategySpec` 신규 필드 `breakout_bars/vol_mult/trail_atr_mult/time_stop_min_profit`, `STRATEGY_SPECS["breakout"]`, `ACTIVE_STRATEGIES = ("rsi2", "rsi2_15m", "breakout")`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_charter.py`에 추가:

```python
def test_v4_experimental_track_constants():
    """v4.0: 실험 트랙 — 검증 없이 가동 가능하되 주문금액 상한이 강제되는 공식 실험 차선."""
    assert C.EXPERIMENTAL_STRATEGIES == frozenset({"breakout"})
    assert C.EXPERIMENT_MAX_ORDER_KRW == 10_000
    assert C.MAX_POSITIONS_VALIDATED == 1
    assert C.MAX_POSITIONS_EXPERIMENTAL == 3
    assert C.MAX_CONCURRENT_POSITIONS == 4          # 트랙 합
    assert C.track_of("breakout") == "experimental"
    assert C.track_of("rsi2") == "validated"
    assert C.track_of("unknown") == "validated"      # 모르는 전략은 보수적으로 검증 트랙 취급


def test_v4_experiment_order_cap_enforced():
    """실험 트랙은 사이징 결과와 무관하게 10,000원을 넘을 수 없다 (easy_teaching 사고 재발 방지)."""
    assert C.position_cap_for("breakout", 90_000.0) == 10_000.0
    assert C.position_cap_for("breakout", 7_000.0) == 7_000.0
    assert C.position_cap_for("rsi2", 90_000.0) == 90_000.0   # 검증 트랙은 불변


def test_v4_absolute_circuit_values():
    """서킷은 절대값 (운영자 결정 2026-08-14): 일 -5% · MDD -50%. 두 트랙이 한 계좌를 공유하므로 R 단위를 버린다."""
    assert C.DAILY_LOSS_LIMIT_RATIO == 0.05
    assert C.MAX_DRAWDOWN_RATIO == 0.50
    assert C.MAX_CONSECUTIVE_LOSSES == 12


def test_v4_breakout_spec():
    spec = C.STRATEGY_SPECS["breakout"]
    assert spec.timeframe == "minute5"
    assert spec.breakout_bars == 20
    assert spec.vol_mult == 1.5
    assert spec.trail_atr_mult == 1.5
    assert spec.time_stop_bars == 12
    assert spec.time_stop_min_profit == 0.003
    assert spec.min_atr_ratio == 0.0        # 변동성 게이트 없음 — rsi2를 죽인 관문을 여기선 안 둔다
    assert spec.always_active is True       # 레짐 필터 없음
    assert spec.use_dead_extras is False
    assert "breakout" in C.ACTIVE_STRATEGIES


def test_v4_fingerprint_covers_experiment_params():
    """실험 트랙 상한도 승인 대상 — 상한을 몰래 올리면 지문이 바뀌어 모의로 떨어져야 한다."""
    before = C.charter_fingerprint()
    orig = C.EXPERIMENT_MAX_ORDER_KRW
    try:
        C.EXPERIMENT_MAX_ORDER_KRW = 50_000
        assert C.charter_fingerprint() != before
    finally:
        C.EXPERIMENT_MAX_ORDER_KRW = orig
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/test_charter.py -q -k v4`. Expected: FAIL (AttributeError 등)

- [ ] **Step 3: charter.py 구현** — 아래 변경을 적용:

(a) 버전: `CHARTER_VERSION = "v4.0"`

(b) 배분 (기존 `ALLOC_PER_STRATEGY_RATIO = 1.0` 교체):

```python
ALLOC_PER_STRATEGY_RATIO = 2 / 3    # 검증 트랙 배분 상한 (v4.0). 실험 예산(3×10,000원) 침범 방지.
                                    # ⚠ 실효 리스크 = min(f, 배분×손절거리) = min(2.4%, 0.667×2.5%) ≈ 1.67%.
                                    #   f 2.4%가 온전히 실리지 않음을 알고 감수한다 — 두 트랙이 한 계좌를
                                    #   공유하는 대가다 (스펙 2026-08-14 §1, 운영자 승인).
```

(c) 서킷 (기존 `DAILY_LOSS_LIMIT_R`/`MAX_DRAWDOWN_R_MULT`/파생값 블록 교체 — R 상수는 삭제):

```python
# ── 서킷 (v4.0, 운영자 결정 2026-08-14): R 단위 → **절대 %** ─────────
# 두 트랙(검증 f 기반 + 실험 고정금액)이 한 계좌를 공유하므로 R(=f 배수) 정의가 성립하지 않는다.
# 값은 운영자가 직접 골랐다: "천천히 잃으면서 배우는 것과 하루에 다 잃는 것은 배움의 양이 다르다."
DAILY_LOSS_LIMIT_RATIO = 0.05       # 일일 손실 한도 −5% (§5.2). 약 −4,500원에서 당일 정지
MAX_DRAWDOWN_RATIO = 0.50           # 고점 대비 −50% 전면 정지 (§5.4). 재가동은 수동
MAX_CONSECUTIVE_LOSSES = 12         # 연속 손절 차단 (§5.3, v3.0 값 유지. 일 경계 자동 해제)
```

`MAX_CONCURRENT_POSITIONS = 1` 및 그 주석 블록은 (d)의 트랙 상수로 교체. `TOTAL_HEAT_RATIO`는 `RISK_PER_TRADE_RATIO * MAX_POSITIONS_VALIDATED`로 정의를 바꾸고 "실험 트랙 히트는 주문 상한(3×10,000원×손절거리)으로 별도 바운드된다" 주석을 단다. `RISK_PER_TRADE_RATIO = 0.024`는 유지(검증 트랙 사이징 전용).

(d) 트랙 정의 (INCUBATING_STRATEGIES 근처에 추가):

```python
# ── 실험 트랙 (v4.0, 스펙 docs/superpowers/specs/2026-08-14-…) ─────────
# EXPERIMENTAL — §11 검증 **없이** 가동할 수 있는 공식 실험 차선. 대신 주문금액 상한을
# 코드로 강제한다. easy_teaching 사고(검증 0건 전략이 자본 1/3로 실계좌 진입)의 재발
# 방지 장치를 유지하면서 "작게는 실험해도 된다"를 규칙으로 만든 것이다.
# 이 트랙의 1차 산출물은 수익이 아니라 **튜닝 데이터**다(진입 컨텍스트 전수 기록).
EXPERIMENTAL_STRATEGIES: frozenset[str] = frozenset({"breakout"})
EXPERIMENT_MAX_ORDER_KRW = 10_000   # 실험 트랙 건당 주문 상한 (지문 포함 — 승인 대상)
MAX_POSITIONS_VALIDATED = 1         # 검증 트랙 동시 포지션 (v3.0의 MAX_CONCURRENT_POSITIONS=1 승계)
MAX_POSITIONS_EXPERIMENTAL = 3      # 실험 트랙 동시 포지션 (예산 = 3 × 10,000원)
MAX_CONCURRENT_POSITIONS = MAX_POSITIONS_VALIDATED + MAX_POSITIONS_EXPERIMENTAL


def track_of(strategy: str) -> str:
    """전략 → 트랙. 모르는 전략(오펀 복구 등)은 보수적으로 검증 트랙 취급(더 좁은 한도)."""
    return "experimental" if strategy in EXPERIMENTAL_STRATEGIES else "validated"
```

(e) `position_cap_for` 교체:

```python
def position_cap_for(strategy: str, krw: float) -> float:
    """인큐베이션은 최소주문금액(§11-3), 실험 트랙은 EXPERIMENT_MAX_ORDER_KRW(v4.0)로 묶는다."""
    if strategy in INCUBATING_STRATEGIES:
        return min(krw, float(MIN_ORDER_KRW))
    if strategy in EXPERIMENTAL_STRATEGIES:
        return min(krw, float(EXPERIMENT_MAX_ORDER_KRW))
    return krw
```

(f) `StrategySpec`에 필드 추가 (기본값이 기존 전략 동작을 바꾸지 않게 0/None):

```python
    # ── v4.0: 돌파(실험 트랙) 파라미터 ──
    breakout_bars: int = 0            # 직전 N봉 최고가 돌파 진입. 0 = 미사용
    vol_mult: float = 0.0             # 거래량 확인: 현재봉 ≥ 직전 N봉 평균 × 이 값. 0 = 끔
    trail_atr_mult: float = 0.0       # 트레일링 스톱: 고점 − 이 값×진입ATR. 0 = 미사용
    time_stop_min_profit: float | None = None  # 시간손절 시 이 수익률 이상이면 청산 유예
```

(g) `STRATEGY_SPECS`에 추가:

```python
    # breakout (v4.0 실험 트랙) — §11 검증 없이 가동한다(운영자 결정, 스펙 §0 비목적 참조).
    # 과거 연구(14차 H4)에서 돌파 계열 롱은 음수였다. 이 전략의 1차 산출물은 수익이 아니라
    # "어떤 조건의 돌파가 손실인가"의 실거래 데이터다. 시작값은 lab_breakout.py 캘리브레이션으로
    # 확정(하루 3~10건 범위에서 가장 덜 나쁜 조합). rr=0.0: 고정 익절 없음 — 트레일링이 익절을 대체.
    "breakout": StrategySpec("breakout", atr_stop_mult=1.0, rr=0.0, regime=Regime.TREND,
                             min_atr_ratio=0.0, time_stop_bars=12,
                             use_dead_extras=False, always_active=True,
                             timeframe="minute5",
                             breakout_bars=20, vol_mult=1.5, trail_atr_mult=1.5,
                             time_stop_min_profit=0.003),
```

(h) `ACTIVE_STRATEGIES = ("rsi2", "rsi2_15m", "breakout")` + 주석에 "breakout은 EXPERIMENTAL — §11 미통과 상태로 가동, 주문 상한 강제" 명시.

(i) `charter_fingerprint()`의 risk 파트에 추가 (기존 `alloc=` 줄 뒤):

```python
        f"exp_cap={EXPERIMENT_MAX_ORDER_KRW!r}",
        f"maxpos_v={MAX_POSITIONS_VALIDATED!r}",
        f"maxpos_e={MAX_POSITIONS_EXPERIMENTAL!r}",
```

- [ ] **Step 4: 기존 테스트 갱신** — `.venv/bin/python -m pytest tests/ -q` 실행 후 실패하는 기존 테스트를 고친다. 예상 실패와 조치:
  - `test_charter.py`의 `ACTIVE_STRATEGIES == ("rsi2", "rsi2_15m")` 단언 → `("rsi2", "rsi2_15m", "breakout")`으로 갱신.
  - "ACTIVE ⊆ VALIDATED ∪ INCUBATING" 계열 단언 → `∪ EXPERIMENTAL`로 확장하고, **EXPERIMENTAL이면 position_cap_for가 상한을 강제하는지**를 같은 테스트에서 확인.
  - `test_effective_risk_is_not_silently_capped` → 의도가 바뀌었다: v4.0은 배분 2/3로 **의도적으로** 캡한다. 테스트를 "실효 리스크 = min(f, 배분×손절거리) ≈ 0.0167이 **문서화된 값과 일치**하는지"로 재작성하고, 주석에 "v4.0: 실험 예산 확보를 위한 의도적 캡(스펙 §1)"을 남긴다.
  - 서킷 절대값 전환으로 `DAILY_LOSS_LIMIT_R`/`MAX_DRAWDOWN_R_MULT`를 참조하는 테스트 → 절대값 검증으로 교체.

- [ ] **Step 5: 통과 확인** — Run: `.venv/bin/python -m pytest tests/ -q`. Expected: PASS (전체)

- [ ] **Step 6: 헌장 문서 개정** — `docs/TRADING_CHARTER_KR.md` 개정 이력(문서 상단 또는 §14 부근)에 추가:

```markdown
### v4.0 (2026-08-14) — 실험 트랙 신설
- §3.5(신설) 실험 트랙: EXPERIMENTAL_STRATEGIES는 §11 검증 없이 가동 가능. 대신 주문금액
  상한 10,000원을 코드로 강제(position_cap_for). 첫 전략: breakout(5분봉 20봉 고가 돌파,
  거래량 1.5×, 트레일링 1.5×ATR, 시간손절 12봉/+0.3% 유예). 목적은 수익이 아니라 튜닝 데이터.
- §5 서킷을 절대값으로 전환(운영자 결정): 일 −5% · MDD −50% · 12연패 유지. 두 트랙이 한
  계좌를 공유하므로 R 단위 정의가 성립하지 않는다.
- §5.5 동시 포지션: 검증 트랙 1 + 실험 트랙 3. §7.1 검증 트랙 배분 상한 2/3
  (실효 리스크 2.4% → 약 1.67%).
- 근거·상세: docs/superpowers/specs/2026-08-14-breakout-experiment-track-design.md
```

- [ ] **Step 7: Commit**

```bash
git add upbit-rbi-bot/config/charter.py upbit-rbi-bot/tests/ docs/TRADING_CHARTER_KR.md
git commit -m "feat(charter): v4.0 실험 트랙 — breakout 스펙·주문상한·트랙별 동시포지션·절대 서킷"
```

---

### Task 2: Position 트레일링 스톱 + 시간손절 수익 유예

**Files:**
- Modify: `bot/position.py:131-157` (`check_price_exit`)
- Modify: `bot/dead_position.py:26-28` (시간손절 분기)
- Test: `tests/test_position.py` (추가), `tests/test_structural_exit.py` (기존 회귀 확인만)

**Interfaces:**
- Consumes: Task 1의 `StrategySpec.trail_atr_mult`, `time_stop_min_profit`, `STRATEGY_SPECS["breakout"]`
- Produces: `Position.check_price_exit(price)` — `spec.trail_atr_mult > 0`이면 트레일링 경로. `dead_position.is_dead(pos, df)` — 시간손절 시 수익 유예.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_position.py`에 추가:

```python
import pandas as pd
from bot.position import Position, ExitReason
from bot import dead_position


def _breakout_pos(entry=100.0, atr=0.3):
    return Position(strategy="breakout", market="KRW-TEST", entry_price=entry,
                    size_krw=10_000, volume=100.0,
                    entry_time=pd.Timestamp("2026-08-14 09:00"), entry_atr=atr)


def test_breakout_trailing_stop_exits_below_high_minus_atr():
    """고점 − 1.5×진입ATR 하회 시 청산. 수익 중이면 take_profit, 손실이면 stop_loss."""
    pos = _breakout_pos(entry=100.0, atr=0.3)      # 트레일 거리 = 0.45
    pos.update_high(102.0)
    assert pos.check_price_exit(101.7) == ExitReason.NONE       # 102−0.45=101.55 위
    assert pos.check_price_exit(101.5) == ExitReason.TAKE_PROFIT  # 진입가 위에서 트레일 이탈
    pos2 = _breakout_pos(entry=100.0, atr=0.3)
    pos2.update_high(100.0)                         # 고점 갱신 없이 하락
    assert pos2.check_price_exit(99.5) == ExitReason.STOP_LOSS   # 100−0.45=99.55 아래, 손실


def test_breakout_no_fixed_take_profit():
    """rr=0.0이어도 즉시 익절되면 안 된다 — 트레일링이 익절을 대체한다."""
    pos = _breakout_pos(entry=100.0, atr=0.3)
    pos.update_high(100.4)
    assert pos.check_price_exit(100.4) == ExitReason.NONE


def test_breakout_hard_stop_backstop():
    """갭 하락 등으로 트레일보다 고정 손절(sl_ratio, MIN_STOP_RATIO 하한 1%)이 위에 있으면 그쪽이 잡는다."""
    pos = _breakout_pos(entry=100.0, atr=2.0)      # 트레일 거리 3.0 > 고정손절 2.0 (=2×ATR/가격 2%... )
    # atr=2.0 → sl_ratio = max(1.0×2/100, 1%) = 2% → 고정손절 98.0 · 트레일 100−3=97.0 → 98이 위
    assert pos.check_price_exit(97.9) == ExitReason.STOP_LOSS


def test_rsi2_ratio_exit_unchanged():
    """검증 트랙 회귀: rsi2는 고정 ±2.5% 비율 청산 그대로 (§11 재현성)."""
    pos = Position(strategy="rsi2", market="KRW-TEST", entry_price=100.0,
                   size_krw=50_000, volume=500.0,
                   entry_time=pd.Timestamp("2026-08-14 09:00"), entry_atr=0.5)
    assert pos.check_price_exit(97.4) == ExitReason.STOP_LOSS
    assert pos.check_price_exit(102.6) == ExitReason.TAKE_PROFIT
    assert pos.check_price_exit(101.0) == ExitReason.NONE


def _df_after_entry(entry_ts: str, bars: int, close: float) -> pd.DataFrame:
    idx = pd.date_range(pd.Timestamp(entry_ts), periods=bars + 1, freq="5min")
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": 1.0}, index=idx)


def test_breakout_time_stop_exits_when_flat():
    """12봉 경과 & 수익 < +0.3% → 시간손절."""
    pos = _breakout_pos(entry=100.0)
    df = _df_after_entry("2026-08-14 09:00", 12, close=100.1)   # +0.1% < +0.3%
    dead, why = dead_position.is_dead(pos, df)
    assert dead and "time_stop" in why


def test_breakout_time_stop_deferred_when_in_profit():
    """12봉 경과라도 수익 ≥ +0.3%면 유예 — 트레일링이 마무리한다."""
    pos = _breakout_pos(entry=100.0)
    df = _df_after_entry("2026-08-14 09:00", 12, close=100.5)   # +0.5%
    dead, _ = dead_position.is_dead(pos, df)
    assert not dead
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/test_position.py -q`. Expected: 신규 테스트 FAIL

- [ ] **Step 3: 구현**

`bot/position.py` — `check_price_exit` 맨 앞(구조 레벨 분기보다 먼저)에 추가:

```python
        # ── 트레일링 스톱 (v4.0, 실험 트랙) — 고정 익절 없이 고점을 따라간다 ──
        # 청산선 = max(고점 − trail×진입ATR, 고정손절). 진입 직후엔 고점=진입가라서
        # 트레일이 곧 초기 손절이 된다(공격적 설계 — 스펙 §2). 수익 중 이탈이면 take_profit,
        # 손실 이탈이면 stop_loss 로 구분해 기록한다(코호트 분석이 이 구분을 쓴다).
        if self.spec.trail_atr_mult > 0 and self.entry_atr > 0:
            trail = self.highest_price - self.spec.trail_atr_mult * self.entry_atr
            hard = self.entry_price * (1 - self.sl_ratio)
            if price <= max(trail, hard):
                return (ExitReason.STOP_LOSS if price < self.entry_price
                        else ExitReason.TAKE_PROFIT)
            return ExitReason.NONE
```

`bot/dead_position.py` — ① 시간손절 분기를 교체:

```python
    # ① 시간 손절: N봉 경과 & TP·SL 미도달 (메인 기준)
    if bars_held >= time_stop:
        # v4.0: 수익 유예 — 시간이 다 됐어도 min_profit 이상 수익 중이면 청산하지 않는다
        # (트레일링 스톱이 마무리한다). 수익이 그 밑으로 내려오면 다음 판정에서 시간손절.
        minp = pos.spec.time_stop_min_profit
        if minp is not None and len(df):
            price = float(df["close"].iloc[-1])
            if (price - pos.entry_price) / pos.entry_price >= minp:
                return False, ""
        return True, f"time_stop {bars_held}>={time_stop} bars"
```

- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/python -m pytest tests/test_position.py tests/test_structural_exit.py tests/test_exit_resilience.py -q`. Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add upbit-rbi-bot/bot/position.py upbit-rbi-bot/bot/dead_position.py upbit-rbi-bot/tests/test_position.py
git commit -m "feat(position): 트레일링 스톱 + 시간손절 수익 유예 (v4.0 실험 트랙)"
```

---

### Task 3: BreakoutStrategy

**Files:**
- Create: `strategies/breakout.py`
- Test: `tests/test_breakout_strategy.py` (신규)

**Interfaces:**
- Consumes: `StrategySpec.breakout_bars/vol_mult` (Task 1), `strategies/base.py`의 `BaseStrategy/Signal/Action`
- Produces: `BreakoutStrategy(BaseStrategy)` — `signal(df, ctx)`. ENTER_LONG 시 `meta`에 코호트 축 전부: `{"breakout_pct", "vol_ratio", "atr_pct", "trend_up", "timeframe", "n", "vol_mult", "trail"}`. EXIT 신호는 내지 않는다(청산은 Position 전담).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_breakout_strategy.py`:

```python
import numpy as np
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
        "open": closes, "high": highs, "low": closes * 0.999, "close": closes,
        "volume": pd.Series(volumes if volumes is not None else [1.0] * n, dtype=float),
    }, index=idx)


@pytest.fixture
def strat():
    return BreakoutStrategy(STRATEGY_SPECS["breakout"])


def _base(n=40, price=100.0):
    """직전 구간이 평평한(고가 100) 캔들. 마지막 봉만 테스트가 바꾼다."""
    closes = [price] * n
    highs = [price] * n
    vols = [1.0] * n
    return closes, highs, vols


def test_enters_on_breakout_with_volume(strat):
    closes, highs, vols = _base()
    closes[-1] = 101.0; highs[-1] = 101.0; vols[-1] = 2.0   # 돌파 + 거래량 2배
    sig = strat.signal(_df(closes, highs, vols))
    assert sig.action == Action.ENTER_LONG
    # 코호트 축이 meta에 전부 있어야 한다 (Task 6이 이 dict를 JSON으로 저장한다)
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
    closes[-1] = 101.0; highs[-1] = 101.0; vols[-1] = 2.0
    sig = strat.signal(_df(closes, highs, vols), ctx={"trend_up": False})
    assert sig.action == Action.ENTER_LONG
    assert sig.meta["trend_up"] is False


def test_never_emits_exit(strat):
    """청산은 Position(트레일링·시간손절) 전담 — 어떤 입력에도 EXIT를 내지 않는다."""
    closes, highs, vols = _base()
    closes[-1] = 90.0                                       # 급락
    assert strat.signal(_df(closes, highs, vols)).action == Action.HOLD


def test_insufficient_bars(strat):
    closes, highs, vols = _base(n=10)
    assert strat.signal(_df(closes, highs, vols)).action == Action.HOLD
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/test_breakout_strategy.py -q`. Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `strategies/breakout.py`:

```python
"""
돌파(breakout) 전략 — v4.0 실험 트랙 (헌장 §3.5, 스펙 docs/superpowers/specs/2026-08-14-…).

## 무엇을 노리는가
"조용히 있다가 움직이기 시작하는 순간 올라타서, 움직임이 끝나면 바로 내린다."
직전 N봉(기본 20봉 = 100분) 최고가를 종가가 넘고 거래량이 늘었을 때만 산다.
청산은 전부 가격 기반(Position): 트레일링 스톱 · 고정 손절 백스톱 · 시간손절(수익 유예).

## ⚠ 이 전략은 §11 검증을 통과하지 않았다 (운영자가 알고 가동)
과거 연구(14차 H4)에서 돌파 계열 롱은 유의하게 음수였다. 1차 산출물은 수익이 아니라
"어떤 조건의 돌파가 손실인가"의 실거래 데이터다. 그래서 진입 순간의 모든 상황을 meta에
담는다 — 트레이더가 이를 JSON으로 저장하고 주간 코호트 리포트가 축별로 쪼갠다.
추세·변동성 게이트는 **판단에 쓰지 않고 기록만 한다**(1주 뒤 데이터로 재판단).
"""
from __future__ import annotations

import pandas as pd

from indicators import ta
from strategies.base import BaseStrategy, Signal, Action


class BreakoutStrategy(BaseStrategy):
    def signal(self, df: pd.DataFrame, ctx: dict | None = None) -> Signal:
        n = self.spec.breakout_bars
        if n <= 0 or len(df) < max(n + 1, 20):
            return Signal(Action.HOLD, self.name, "insufficient bars")

        close = float(df["close"].iloc[-1])
        prior = df.iloc[-(n + 1):-1]              # 현재(진행 중) 봉 제외 직전 n봉
        prior_high = float(prior["high"].max())
        vol = float(df["volume"].iloc[-1])
        vol_avg = float(prior["volume"].mean())
        vol_ratio = (vol / vol_avg) if vol_avg > 0 else 0.0

        atr = ta.atr(df).iloc[-1] if len(df) >= 15 else float("nan")
        atr_pct = (float(atr) / close * 100) if pd.notna(atr) and close > 0 else None

        # 코호트 축 (판단에 쓰는 건 breakout·vol_ratio 둘뿐, 나머지는 기록용)
        meta = {
            "breakout_pct": round((close - prior_high) / prior_high * 100, 3) if prior_high > 0 else None,
            "vol_ratio": round(vol_ratio, 2),
            "atr_pct": None if atr_pct is None else round(atr_pct, 3),
            "trend_up": (ctx or {}).get("trend_up"),
            "timeframe": self.spec.timeframe,
            "n": n, "vol_mult": self.spec.vol_mult, "trail": self.spec.trail_atr_mult,
        }

        if prior_high <= 0 or close <= prior_high:
            return Signal(Action.HOLD, self.name,
                          f"돌파 대기 (직전 {n}봉 고가 {prior_high:.4g})", meta)
        if self.spec.vol_mult > 0 and vol_ratio < self.spec.vol_mult:
            return Signal(Action.HOLD, self.name,
                          f"거래량 부족 ({vol_ratio:.1f}배 < {self.spec.vol_mult}배)", meta)
        return Signal(Action.ENTER_LONG, self.name,
                      f"{n}봉 고가 돌파 +{meta['breakout_pct']}% · 거래량 {vol_ratio:.1f}배", meta)
```

- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/python -m pytest tests/test_breakout_strategy.py -q`. Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add upbit-rbi-bot/strategies/breakout.py upbit-rbi-bot/tests/test_breakout_strategy.py
git commit -m "feat(strategy): BreakoutStrategy — 20봉 고가 돌파 + 거래량 확인 (v4.0 실험 트랙)"
```

---

### Task 4: RiskManager — 트랙별 동시 포지션 상한

**Files:**
- Modify: `bot/risk_manager.py`
- Test: `tests/test_multi_position.py` (추가)

**Interfaces:**
- Consumes: Task 1의 `track_of`, `MAX_POSITIONS_VALIDATED/EXPERIMENTAL`
- Produces: `RiskManager.can_enter(strategy: str | None = None)` (인자 없으면 기존과 동일한 전역 판정 — 대시보드 호환), `on_open(strategy: str = "")`, `on_close(pnl_krw, strategy: str = "")`, `RiskState.open_validated/open_experimental: int`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_multi_position.py`에 추가:

```python
from bot.risk_manager import RiskManager, RiskState


def _rm():
    rm = RiskManager(RiskState())
    rm.update_capital(90_000, 90_000)
    return rm


def test_experimental_track_allows_three_positions():
    rm = _rm()
    for _ in range(3):
        ok, _w = rm.can_enter("breakout")
        assert ok
        rm.on_open("breakout")
    ok, why = rm.can_enter("breakout")
    assert not ok and "실험" in why


def test_validated_track_capped_at_one_independently():
    """실험 트랙이 3개 차 있어도 검증 트랙 1자리는 열려 있고, 그 역도 성립한다."""
    rm = _rm()
    for _ in range(3):
        rm.on_open("breakout")
    ok, _w = rm.can_enter("rsi2")
    assert ok
    rm.on_open("rsi2")
    assert not rm.can_enter("rsi2")[0]        # 검증 트랙 만석
    assert not rm.can_enter("breakout")[0]    # 실험 트랙 만석
    rm.on_close(-100.0, "breakout")           # 실험 자리 하나 반환
    assert rm.can_enter("breakout")[0]
    assert not rm.can_enter("rsi2")[0]        # 검증 트랙은 여전히 만석


def test_can_enter_without_strategy_is_global_only():
    """대시보드 호환: 인자 없으면 트랙 판정 없이 전역(서킷·잔고·총한도)만 본다."""
    rm = _rm()
    rm.on_open("rsi2")
    assert rm.can_enter()[0]
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/test_multi_position.py -q`. Expected: 신규 FAIL (TypeError)

- [ ] **Step 3: 구현** — `bot/risk_manager.py`:

`RiskState`에 필드 추가:

```python
    open_validated: int = 0                            # 검증 트랙 보유 수 (v4.0 §5.5)
    open_experimental: int = 0                         # 실험 트랙 보유 수 (v4.0 §5.5)
```

`can_enter` 교체 (시그니처 + 트랙 분기 추가, 기존 전역 검사는 유지):

```python
    def can_enter(self, strategy: str | None = None) -> tuple[bool, str]:
        """strategy 를 주면 그 전략의 트랙 상한까지 본다. 없으면 전역 판정만(대시보드용)."""
        self._roll_day()
        if self.s.halted:
            return False, f"halted: {self.s.halt_reason}"
        if self.s.daily_pnl <= -C.daily_loss_limit_krw(self.s.capital):
            return False, "daily loss limit hit (§5.2)"
        if self.s.consecutive_losses >= C.MAX_CONSECUTIVE_LOSSES:
            return False, "consecutive loss circuit (§5.3) — 익일 자동 해제"
        if self.s.open_positions >= C.MAX_CONCURRENT_POSITIONS:
            return False, "max concurrent positions (§5.5)"
        if self.s.available_krw < C.MIN_ORDER_KRW:
            return False, "insufficient KRW balance (잔고 부족)"
        if strategy is not None:
            if C.track_of(strategy) == "experimental":
                if self.s.open_experimental >= C.MAX_POSITIONS_EXPERIMENTAL:
                    return False, "실험 트랙 동시 포지션 한도 (§5.5, v4.0)"
            elif self.s.open_validated >= C.MAX_POSITIONS_VALIDATED:
                return False, "검증 트랙 동시 포지션 한도 (§5.5)"
        return True, "ok"
```

`on_open`/`on_close` 교체 (open_positions 는 두 카운터의 합으로 유지):

```python
    def on_open(self, strategy: str = "") -> None:
        if C.track_of(strategy) == "experimental":
            self.s.open_experimental += 1
        else:
            self.s.open_validated += 1
        self.s.open_positions = self.s.open_validated + self.s.open_experimental

    def on_close(self, pnl_krw: float, strategy: str = "") -> None:
        if C.track_of(strategy) == "experimental":
            self.s.open_experimental = max(0, self.s.open_experimental - 1)
        else:
            self.s.open_validated = max(0, self.s.open_validated - 1)
        self.s.open_positions = self.s.open_validated + self.s.open_experimental
        self.s.daily_pnl += pnl_krw
        if pnl_krw < 0:
            self.s.consecutive_losses += 1
        else:
            self.s.consecutive_losses = 0
```

- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/python -m pytest tests/test_multi_position.py tests/ -q`. Expected: PASS. `on_open()`/`on_close(pnl)`을 인자 없이 호출하는 기존 테스트·코드는 기본값(`""` → validated)으로 그대로 동작한다.

- [ ] **Step 5: Commit**

```bash
git add upbit-rbi-bot/bot/risk_manager.py upbit-rbi-bot/tests/test_multi_position.py
git commit -m "feat(risk): 트랙별 동시 포지션 상한 — 검증 1 · 실험 3 (v4.0 §5.5)"
```

---

### Task 5: TradeLogger — 진입 컨텍스트(context) 컬럼

**Files:**
- Modify: `incubation/logger.py`
- Test: `tests/test_incubation_progress.py` (추가)

**Interfaces:**
- Produces: `TradeLogger.log(..., context: str = "")` — trades 테이블에 `context TEXT` 컬럼(기존 DB는 ALTER로 자동 마이그레이션, `fill_price` 패턴과 동일)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_incubation_progress.py`에 추가:

```python
def test_logger_context_column_roundtrip(tmp_path):
    """진입 컨텍스트 JSON이 저장·조회되고, 컬럼이 없던 기존 DB도 자동 마이그레이션된다."""
    import json, sqlite3
    from incubation.logger import TradeLogger
    db = str(tmp_path / "trades.sqlite")
    # 구버전 스키마(컨텍스트 없음)를 먼저 만들어 마이그레이션을 검증한다
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " ts TEXT, event TEXT, strategy TEXT, market TEXT, price REAL,"
                    " volume REAL, size_krw REAL, pnl_krw REAL, reason TEXT)")
    lg = TradeLogger(db)
    ctx = {"breakout_pct": 0.42, "vol_ratio": 2.1, "trend_up": False}
    lg.log("entry", strategy="breakout", market="KRW-BTC", price=100.0,
           context=json.dumps(ctx, ensure_ascii=False))
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT context FROM trades WHERE event='entry'").fetchone()
    assert json.loads(row[0])["vol_ratio"] == 2.1
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/test_incubation_progress.py -q -k context`. Expected: FAIL

- [ ] **Step 3: 구현** — `incubation/logger.py`:

`_init_db`의 컬럼 확인 블록에 추가 (fill_price 아래):

```python
            # context (v4.0): 진입 순간의 상황 전부(JSON) — 실험 트랙 코호트 튜닝의 원료.
            # "어떤 조건의 거래가 손실인가"를 실거래로 답하려면 진입 시점 상태가 남아 있어야 한다.
            if "context" not in cols:
                con.execute("ALTER TABLE trades ADD COLUMN context TEXT")
```

`log` 시그니처에 `context: str = ""` 추가, INSERT 문 컬럼·값에 `context` 추가 (`context or None`).

- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/python -m pytest tests/test_incubation_progress.py -q`. Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add upbit-rbi-bot/incubation/logger.py upbit-rbi-bot/tests/test_incubation_progress.py
git commit -m "feat(logger): 진입 컨텍스트 JSON 컬럼 (v4.0 코호트 튜닝 원료)"
```

---

### Task 6: Trader 연결 — 전략 등록·트랙 판정·컨텍스트 저장

**Files:**
- Modify: `bot/trader.py` (42-49 ALL_STRATEGIES, 311 can_enter, 326-387 _open, 169·383 on_open, 472 on_close)
- Test: `tests/test_multi_position.py` 또는 `tests/test_screening_integration.py`의 기존 Trader 통합 테스트 패턴에 추가 (기존 fixture/mocking 패턴을 먼저 읽고 같은 방식 사용)

**Interfaces:**
- Consumes: Task 3 `BreakoutStrategy`, Task 4 `can_enter(name)/on_open(name)/on_close(pnl, name)`, Task 5 `log(..., context=)`
- Produces: 라이브 봇이 breakout을 가동하고, entry 로그 context에 `sig.meta + {"fingerprint", "spread_pct"}` JSON 저장

- [ ] **Step 1: 실패하는 테스트 작성** — 기존 Trader 통합 테스트 파일의 mocking 패턴을 확인한 뒤(예: `tests/test_screening_integration.py`), 다음 두 가지를 검증하는 테스트를 추가:

```python
def test_breakout_registered_in_all_strategies():
    from bot.trader import ALL_STRATEGIES, build_strategies
    from strategies.breakout import BreakoutStrategy
    assert ALL_STRATEGIES["breakout"] is BreakoutStrategy
    assert "breakout" in build_strategies()          # ACTIVE_STRATEGIES 반영


def test_entry_log_contains_context_json(...):      # 기존 통합 테스트 패턴대로 Trader를 모킹 구성
    """진입이 성립하면 trades.context 에 fingerprint·spread_pct 포함 JSON이 남는다."""
    # 기존 파일의 진입 성공 시나리오 fixture를 재사용해 breakout ENTER_LONG을 만들고,
    # sqlite에서 context 컬럼을 읽어 json.loads 후 다음을 단언:
    #   set(ctx) ⊇ {"fingerprint", "spread_pct", "vol_ratio", "breakout_pct"}
    #   ctx["fingerprint"] == charter.charter_fingerprint()
```

(두 번째 테스트의 구체 형태는 기존 통합 테스트의 클라이언트 목 구조를 따른다 — 새 목 프레임워크를 만들지 말 것.)

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/ -q -k "breakout_registered or context_json"`. Expected: FAIL

- [ ] **Step 3: 구현** — `bot/trader.py`:

(a) import + 등록:

```python
from strategies.breakout import BreakoutStrategy
...
ALL_STRATEGIES = {
    ...,
    "breakout": BreakoutStrategy,      # v4.0 실험 트랙 (§3.5)
}
```

(b) `_process_market` 진입 루프의 `ok, why = self.risk.can_enter()` → `self.risk.can_enter(name)`.

(c) `_open`의 성공 경로: `self.risk.on_open()` → `self.risk.on_open(name)`. `_recover_positions`의 `self.risk.on_open()` → `self.risk.on_open(rec_pos.strategy)`. `_close`의 `self.risk.on_close(pnl)` → `self.risk.on_close(pnl, pos.strategy)`.

(d) `_open`의 entry 로그를 컨텍스트 포함으로 교체 (파일 상단에 `import json` 추가):

```python
        # v4.0: 진입 순간의 상황 전부를 남긴다 — 주간 코호트 리포트의 원료 (§10.1).
        # 지문을 함께 저장하므로 파라미터를 튜닝해도 "몇 주차 설정의 성적"으로 비교된다.
        entry_ctx = dict(sig.meta) if sig else {}
        entry_ctx["fingerprint"] = C.charter_fingerprint()
        entry_ctx["spread_pct"] = getattr(self.screener, "spreads", {}).get(market)
        self.logger.log("entry", strategy=name, market=market, price=price,
                        volume=res.filled_volume, size_krw=krw, reason=note,
                        fill_price=res.avg_price or price,
                        context=json.dumps(entry_ctx, ensure_ascii=False, default=str))
```

- [ ] **Step 4: 인큐베이션 오염 방지 회귀 테스트** — `tests/test_incubation_progress.py`에 추가:

```python
def test_breakout_trades_excluded_from_incubation(tmp_path):
    """실험 트랙 거래는 인큐베이션 표본에 절대 섞이지 않는다 (스펙 §1)."""
    import sqlite3
    from incubation.logger import TradeLogger
    from incubation.progress import load_roundtrips, activity
    db = str(tmp_path / "trades.sqlite")
    lg = TradeLogger(db)
    lg.log("entry", strategy="breakout", market="KRW-BTC", price=100.0, size_krw=10_000)
    lg.log("exit", strategy="breakout", market="KRW-BTC", price=101.0, pnl_krw=90.0)
    lg.log("entry", strategy="rsi2", market="KRW-ETH", price=100.0, size_krw=60_000)
    lg.log("exit", strategy="rsi2", market="KRW-ETH", price=99.0, pnl_krw=-660.0)
    trips = load_roundtrips(db, since=None)
    assert {t.strategy for t in trips} == {"rsi2"}      # 기본 필터가 rsi2 계열만 본다
    act = activity(db)
    assert act["n7"] == 1                               # breakout 진입은 활동 집계에도 안 들어간다
```

- [ ] **Step 5: 통과 확인** — Run: `.venv/bin/python -m pytest tests/ -q`. Expected: 전체 PASS

- [ ] **Step 6: Commit**

```bash
git add upbit-rbi-bot/bot/trader.py upbit-rbi-bot/tests/
git commit -m "feat(trader): breakout 가동 연결 — 트랙 판정·진입 컨텍스트 JSON 저장 (v4.0)"
```

---

### Task 7: 코호트 리포트 + /api/experiment + 주간 리포트 확장

**Files:**
- Create: `incubation/cohort.py`
- Modify: `dashboard/app.py` (기존 `/api/incubation` 라우트 패턴을 보고 동일하게 추가)
- Modify: `deploy/weekly_report.py`
- Test: `tests/test_cohort.py` (신규)

**Interfaces:**
- Consumes: trades 테이블 (entry.context JSON, exit.pnl_krw — Task 5·6 산출)
- Produces: `cohort.report(db_path=None) -> dict` (`{"n", "overall", "axes": {축이름: [{"bucket", "n", "winrate", "exp_pct", "pnl_krw"}...]}, "text"}`), `cohort.format_text(rep) -> str`, HTTP `GET /api/experiment`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_cohort.py`:

```python
import json
from incubation.logger import TradeLogger
from incubation import cohort


def _seed(db):
    """breakout 왕복 4건: 거래량확인 O/X × 추세 위/아래를 한 건씩."""
    lg = TradeLogger(db)
    cases = [
        ({"vol_ratio": 2.0, "trend_up": True,  "breakout_pct": 0.3, "fingerprint": "v4.0-aaaa"}, +80.0),
        ({"vol_ratio": 2.5, "trend_up": False, "breakout_pct": 0.1, "fingerprint": "v4.0-aaaa"}, -60.0),
        ({"vol_ratio": 1.1, "trend_up": True,  "breakout_pct": 0.5, "fingerprint": "v4.0-aaaa"}, -30.0),
        ({"vol_ratio": 3.0, "trend_up": None,  "breakout_pct": 0.2, "fingerprint": "v4.0-aaaa"}, +40.0),
    ]
    for i, (ctx, pnl) in enumerate(cases):
        m = f"KRW-C{i}"
        lg.log("entry", strategy="breakout", market=m, price=100.0, size_krw=10_000,
               context=json.dumps(ctx))
        lg.log("exit", strategy="breakout", market=m, price=100.0, pnl_krw=pnl)


def test_cohort_report_axes(tmp_path):
    db = str(tmp_path / "t.sqlite")
    _seed(db)
    rep = cohort.report(db)
    assert rep["n"] == 4
    axes = rep["axes"]
    trend = {b["bucket"]: b for b in axes["추세(1시간봉)"]}
    assert trend["위"]["n"] == 2 and trend["아래"]["n"] == 1 and trend["판정불가"]["n"] == 1
    fp = {b["bucket"]: b for b in axes["설정(지문)"]}
    assert fp["v4.0-aaaa"]["n"] == 4
    # 30건 미만이면 판정 문구 대신 '표본 부족' 안내 (기존 원칙 유지)
    assert "표본" in rep["text"]


def test_cohort_excludes_validated_track(tmp_path):
    db = str(tmp_path / "t.sqlite")
    _seed(db)
    lg = TradeLogger(db)
    lg.log("entry", strategy="rsi2", market="KRW-ETH", price=100.0, size_krw=60_000)
    lg.log("exit", strategy="rsi2", market="KRW-ETH", price=101.0, pnl_krw=500.0)
    assert cohort.report(db)["n"] == 4
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/test_cohort.py -q`. Expected: FAIL

- [ ] **Step 3: 구현** — `incubation/cohort.py`:

```python
"""
실험 트랙(breakout) 주간 코호트 리포트 (v4.0, 스펙 §5).

"어떤 조건의 돌파가 손실인가"를 실거래로 답한다. 진입 컨텍스트(JSON)를 축별로 쪼개
승률·거래당 손익을 집계한다. 30건 미만 코호트는 판정하지 않는다(적은 표본의 승률은 운이다).
"""
from __future__ import annotations

import json
import sqlite3
from config.settings import settings

MIN_JUDGE = 30          # 이 미만이면 '표본 부족'만 말한다 (incubation.progress 와 같은 원칙)


def _load(db_path: str | None = None) -> list[dict]:
    """breakout 왕복 거래 + 진입 컨텍스트. [{pnl_krw, size_krw, pct, ctx, entry_ts, market}...]"""
    path = db_path or settings.db_path
    try:
        con = sqlite3.connect(path)
        cols = {r[1] for r in con.execute("PRAGMA table_info(trades)")}
        cctx = "context" if "context" in cols else "NULL"
        exits = con.execute(
            "SELECT id, market, pnl_krw FROM trades"
            " WHERE event='exit' AND strategy='breakout' ORDER BY id").fetchall()
        entries = con.execute(
            f"SELECT id, market, size_krw, ts, {cctx} FROM trades"
            " WHERE event='entry' AND strategy='breakout' ORDER BY id").fetchall()
    except sqlite3.Error:
        return []
    finally:
        try:
            con.close()
        except Exception:
            pass
    out = []
    for xid, market, pnl in exits:
        prior = [e for e in entries if e[1] == market and e[0] < xid]
        if not prior:
            continue
        _, _, size, ts, raw = prior[-1]
        try:
            ctx = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            ctx = {}
        out.append({"pnl_krw": pnl or 0.0, "size_krw": size or 0.0,
                    "pct": (pnl or 0.0) / size * 100 if size else 0.0,
                    "ctx": ctx, "entry_ts": ts or "", "market": market})
    return out


def _bucket_axes(t: dict) -> dict[str, str]:
    """한 거래 → 축별 버킷 이름. 새 축을 추가하려면 여기만 고치면 된다."""
    ctx = t["ctx"]
    tr = ctx.get("trend_up")
    vol = ctx.get("vol_ratio")
    hour = int(t["entry_ts"][11:13]) if len(t["entry_ts"]) >= 13 else None
    return {
        "추세(1시간봉)": "위" if tr is True else ("아래" if tr is False else "판정불가"),
        "거래량비": ("2배+" if vol >= 2.0 else "1.5~2배" if vol >= 1.5 else "1.5배 미만")
                   if isinstance(vol, (int, float)) else "기록없음",
        "시간대(KST)": f"{hour // 4 * 4:02d}~{hour // 4 * 4 + 4:02d}시" if hour is not None else "기록없음",
        "종목": t["market"],
        "설정(지문)": ctx.get("fingerprint") or "기록없음",
    }


def _stat(trades: list[dict]) -> dict:
    n = len(trades)
    wins = sum(1 for t in trades if t["pct"] > 0)
    return {"n": n, "winrate": round(wins / n * 100, 1) if n else 0.0,
            "exp_pct": round(sum(t["pct"] for t in trades) / n, 3) if n else 0.0,
            "pnl_krw": round(sum(t["pnl_krw"] for t in trades))}


def report(db_path: str | None = None) -> dict:
    trades = _load(db_path)
    axes: dict[str, list[dict]] = {}
    for t in trades:
        for axis, bucket in _bucket_axes(t).items():
            axes.setdefault(axis, {}).setdefault(bucket, []).append(t)   # type: ignore[union-attr]
    axes_out = {axis: sorted(({"bucket": b, **_stat(ts)} for b, ts in buckets.items()),
                             key=lambda r: -r["n"])
                for axis, buckets in axes.items()}
    rep = {"n": len(trades), "overall": _stat(trades), "axes": axes_out}
    rep["text"] = format_text(rep)
    return rep


def format_text(rep: dict) -> str:
    o = rep["overall"]
    lines = [f"🧪 breakout 실험 트랙 — 왕복 {rep['n']}건, "
             f"누적 {o['pnl_krw']:+,}원 (거래당 {o['exp_pct']:+.3f}%)"]
    if rep["n"] < MIN_JUDGE:
        lines.append(f"표본 {rep['n']}건 — {MIN_JUDGE}건 미만이라 코호트 판정은 하지 않습니다. "
                     f"(적은 표본의 승률은 운입니다)")
    for axis, rows in rep["axes"].items():
        lines.append(f"\n[{axis}]")
        for r in rows:
            mark = "" if r["n"] >= MIN_JUDGE else " (표본 부족)"
            lines.append(f"  {r['bucket']:<12} {r['n']:>3}건  승률 {r['winrate']:>5.1f}%  "
                         f"거래당 {r['exp_pct']:+.3f}%{mark}")
    return "\n".join(lines)
```

- [ ] **Step 4: 대시보드·주간 리포트 연결** — `dashboard/app.py`의 `/api/incubation` 라우트 패턴을 그대로 따라 `/api/experiment` → `cohort.report()` 반환 라우트 추가. `deploy/weekly_report.py`의 `main()`에서:

```python
    from incubation import cohort
    ...
    exp = cohort.report()
    if exp["n"]:
        text += "\n\n" + exp["text"]
```

- [ ] **Step 5: 통과 확인** — Run: `.venv/bin/python -m pytest tests/ -q`. Expected: 전체 PASS

- [ ] **Step 6: Commit**

```bash
git add upbit-rbi-bot/incubation/cohort.py upbit-rbi-bot/dashboard/app.py upbit-rbi-bot/deploy/weekly_report.py upbit-rbi-bot/tests/test_cohort.py
git commit -m "feat(cohort): 실험 트랙 주간 코호트 리포트 + /api/experiment (v4.0 튜닝 루프)"
```

---

### Task 8: 캘리브레이션 백테스트 (lab_breakout)

**Files:**
- Create: `backtesting/research/lab_breakout.py`
- Output: `backtesting/research/results/breakout_calibration_2026-08-14.txt`

**Interfaces:**
- Consumes: `backtesting/research/data_cache.load(market, "minute5")` (2년 캐시), `lab_universe.load_spreads()` 패턴(spreads.json + spreads_mid.json)
- Produces: 그리드(`breakout_bars {12,20,36} × vol_mult {0,1.5,2.0} × trail {1.0,1.5,2.0}`)별 하루 거래수·승률·거래당 net%·보유봉 중앙값 표. **판정용이 아니라 시작값 선택용** — 결과가 전부 음수여도 가동한다(운영자 결정). 선택 기준: 하루 3~10건 범위에서 거래당 net%가 가장 덜 나쁜 조합.

- [ ] **Step 1: 구현** — `backtesting/research/lab_breakout.py` (연구 스크립트 — 테스트 대신 `verify` 함수로 자기검증):

```python
"""
breakout 실험 트랙 캘리브레이션 (v4.0, 스펙 §7).

⚠ 이것은 §11 통과/탈락 판정이 아니다. 운영자는 결과와 무관하게 가동을 결정했다.
목적: ① 시작 파라미터가 실제로 하루 3~10건을 만드는지 ② 가장 덜 나쁜 조합 선택
     ③ 예상 수업료(월 예상 손실)를 가동 전에 보고.
체결 모델: 진입 = 신호봉 종가(라이브의 '다음 tick 시장가' 근사, 리포 관례와 동일).
청산 = Position.check_price_exit 와 같은 규칙을 종가 기준으로 재현(트레일·백스톱·시간손절 유예).
비용 = 수수료 0.1% + 종목별 실측 스프레드 전액.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from indicators import ta                                  # noqa: E402
from backtesting.research import data_cache                # noqa: E402

FEE = 0.001
MIN_STOP = 0.01            # charter.MIN_STOP_RATIO 와 동일 (백스톱 하한)
MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
           "KRW-LINK", "KRW-BCH", "KRW-ETC", "KRW-DOT", "KRW-AVAX"]


def load_spreads() -> dict[str, float]:
    out = {}
    for name in ("spreads.json", "spreads_mid.json"):
        p = Path(__file__).parent / "results" / name
        if p.exists():
            out.update(json.loads(p.read_text()))
    return out


def simulate(df: pd.DataFrame, spread: float, n: int, vol_mult: float,
             trail: float, stop_mult: float = 1.0,
             time_stop: int = 12, min_profit: float = 0.003) -> list[dict]:
    high_n = df["high"].shift(1).rolling(n).max()
    vol_avg = df["volume"].shift(1).rolling(n).mean()
    atr = ta.atr(df)
    close = df["close"].to_numpy()
    entry_ok = (df["close"] > high_n)
    if vol_mult > 0:
        entry_ok &= df["volume"] >= vol_avg * vol_mult
    entry_idx = np.flatnonzero(entry_ok.to_numpy())
    trades, i_free = [], 0
    for i in entry_idx:
        if i < n + 15 or i < i_free or i >= len(df) - 1:
            continue
        e_price, e_atr = close[i], float(atr.iloc[i])
        if not (e_atr > 0):
            continue
        sl = max(stop_mult * e_atr / e_price, MIN_STOP)
        hard = e_price * (1 - sl)
        high = e_price
        exit_px, exit_why, j = None, "", i
        for j in range(i + 1, len(df)):
            px = close[j]
            high = max(high, px)
            level = max(high - trail * e_atr, hard)
            if px <= level:
                exit_px, exit_why = px, ("stop" if px < e_price else "trail_tp")
                break
            if j - i >= time_stop and (px - e_price) / e_price < min_profit:
                exit_px, exit_why = px, "time"
                break
        if exit_px is None:
            exit_px, exit_why = close[-1], "eod"
        net = (exit_px - e_price) / e_price - FEE - spread
        trades.append({"net": net, "hold": j - i, "why": exit_why})
        i_free = j + 1               # 같은 종목 중복 보유 금지 (라이브 §3.3과 동일)
    return trades


def main() -> None:
    spreads = load_spreads()
    panels = {m: data_cache.load(m, "minute5") for m in MARKETS}
    panels = {m: df for m, df in panels.items() if df is not None and len(df) > 50_000}
    days = max(len(df) for df in panels.values()) * 5 / 60 / 24
    rows = []
    for n in (12, 20, 36):
        for vm in (0.0, 1.5, 2.0):
            for tr in (1.0, 1.5, 2.0):
                allt = []
                for m, df in panels.items():
                    sp = spreads.get(m.split("-")[1], spreads.get(m, 0.001))
                    allt += simulate(df, sp, n, vm, tr)
                if not allt:
                    continue
                nets = [t["net"] for t in allt]
                rows.append({
                    "n": n, "vol_mult": vm, "trail": tr, "trades": len(allt),
                    "per_day": round(len(allt) / days, 1),
                    "winrate": round(sum(1 for x in nets if x > 0) / len(nets) * 100, 1),
                    "net_pct": round(float(np.mean(nets)) * 100, 4),
                    "hold_med": int(np.median([t["hold"] for t in allt])),
                })
    out = pd.DataFrame(rows).sort_values("net_pct", ascending=False)
    print(out.to_string(index=False))
    dest = Path(__file__).parent / "results" / "breakout_calibration_2026-08-14.txt"
    dest.write_text(out.to_string(index=False))
    # 하루 3~10건 대역에서 최선 조합과 월 예상 수업료 요약
    band = out[(out.per_day >= 3) & (out.per_day <= 10)]
    if len(band):
        b = band.iloc[0]
        krw_month = b.net_pct / 100 * 10_000 * b.per_day * 30
        print(f"\n★ 시작값 후보: n={b.n} vol_mult={b.vol_mult} trail={b.trail} — "
              f"하루 {b.per_day}건 · 거래당 {b.net_pct:+.4f}% · "
              f"월 예상 손익 {krw_month:+,.0f}원 (건당 10,000원 기준)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실행** — Run: `.venv/bin/python backtesting/research/lab_breakout.py` (2년 캐시가 없으면 먼저 `.venv/bin/python backtesting/research/data_cache.py`로 확인). Expected: 27행 표 + 시작값 후보 출력, results 파일 생성.

- [ ] **Step 3: 시작값 반영** — 후보 조합이 현행 스펙(20/1.5/1.5)과 다르면 `config/charter.py`의 `STRATEGY_SPECS["breakout"]`과 Task 1 테스트 기대값을 후보 값으로 갱신하고 주석에 캘리브레이션 결과(하루 건수·거래당 net%·월 예상 수업료)를 기록한다. **하루 3~10건 대역에 조합이 하나도 없으면 멈추고 운영자에게 보고**(빈도 목표와 그리드가 안 맞는다는 뜻 — 그리드 확장 상의).

- [ ] **Step 4: Commit**

```bash
git add upbit-rbi-bot/backtesting/research/lab_breakout.py upbit-rbi-bot/backtesting/research/results/breakout_calibration_2026-08-14.txt upbit-rbi-bot/config/charter.py upbit-rbi-bot/tests/test_charter.py
git commit -m "research(lab): breakout 캘리브레이션 — 시작 파라미터 확정 (하루 3~10건 대역)"
```

---

### Task 9: 전체 검증 · 스펙 튜닝 로그 · 배포

**Files:**
- Modify: `docs/superpowers/specs/2026-08-14-breakout-experiment-track-design.md` (튜닝 로그에 캘리브레이션 결과 기록)
- 서버 `.env` (지문 갱신 — 리포 파일 아님)

- [ ] **Step 1: 전체 테스트** — Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/ -q`. Expected: 전체 PASS
- [ ] **Step 2: 새 지문 확인** — Run: `.venv/bin/python -c "from config.charter import charter_fingerprint as f; print(f())"` → `v4.0-XXXXXXXX` 기록
- [ ] **Step 3: 스펙 튜닝 로그 갱신** — 캘리브레이션으로 확정된 시작값·예상 수업료를 스펙 하단 표에 추가
- [ ] **Step 4: 운영자 최종 보고** — 시작값·예상 빈도·월 예상 수업료·새 지문을 보고하고 배포 승인을 받는다 (**실계좌 반영이므로 반드시 확인 후 진행**)
- [ ] **Step 5: 배포** — push → `gh run watch`로 완료 확인 → 리포 루트에서 `ssh -i ssh-key-2026-06-25.key ubuntu@168.107.31.154 'cd ~/projects/life-change && sed -i "s/^LIVE_CHARTER_ACK=.*/LIVE_CHARTER_ACK=<새지문>/" .env && docker compose up -d --force-recreate'`
- [ ] **Step 6: 검증** — `curl -sk https://168.107.31.154/life-change/api/status` 에서 `dry_run=false` · `forced_paper=""` · strategies에 breakout 확인. `/api/experiment` 200 확인.
- [ ] **Step 7: Commit + 완료 보고** — 첫 주 운영 계획(매일 거래 발생 확인, 7일 후 첫 코호트 리포트) 안내

---

## Self-Review 결과

- 스펙 §1(트랙 구조·배분 2/3) → Task 1(c,d)·기존 테스트 갱신. §2(전략 규칙·컨텍스트) → Task 2·3·6. §3(헌장 통합) → Task 1. §4(실행 경로) → Task 2·4·5·6. §5(튜닝 루프) → Task 7. §6(테스트) → 각 Task Step 1 + Task 6 Step 4. §7(캘리브레이션) → Task 8. §8(배포) → Task 9. 커버리지 공백 없음.
- 타입 일관성: `can_enter(strategy: str|None)`·`on_open(strategy: str)`·`track_of(str)->str`·`position_cap_for(str, float)->float` — Task 1·4·6에서 동일 시그니처 사용 확인.
- 주의: incubation/progress.py는 기본 인자가 이미 `("rsi2","rsi2_15m")` 화이트리스트라 코드 변경 불필요 — Task 6 Step 4의 회귀 테스트로 고정만 한다.

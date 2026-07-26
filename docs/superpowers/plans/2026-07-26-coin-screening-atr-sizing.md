# 동적 종목 스크리닝 + ATR 완전정규화 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 거래대금 상위 동적 유니버스로 거래 대상을 넓히고, SL/TP·포지션 사이징을 ATR 기반으로 정규화해 어떤 코인이든 1거래 리스크 = 자본 1%로 통일한다.

**Architecture:** 순수 선정 로직 + 캐시형 `Screener`(업비트 공개 REST)로 적격 유니버스를 산출하고, `StrategySpec`을 ATR 배수(k, 손익비 rr) 체계로 바꿔 진입 시점에 SL/TP 비율과 포지션 크기를 ATR로 계산한다. `Trader`가 매 tick 스크리너 유니버스를 순회한다.

**Tech Stack:** Python 3(=서버), pandas, requests(기존 의존성), pytest. 로컬 실행은 `upbit-rbi-bot/.venv/bin/python`.

## Global Constraints

- 작업 디렉터리: 모든 경로는 `upbit-rbi-bot/` 기준. 테스트는 `cd upbit-rbi-bot && .venv/bin/python -m pytest`로 실행.
- 헌장 SSOT는 `config/charter.py`. 매매 파라미터는 여기서만 import. 이 작업으로 `CHARTER_VERSION="v1.2"`.
- 모든 수치(ATR 배수, top-N, 거래대금 하한)는 **백테스트로 조정할 출발 가설**.
- 신규 의존성 금지(requests는 이미 `requirements.txt`에 존재).
- 기존 테스트 23개 회귀 없음 유지.
- 커밋만 하고 **push 금지**(push는 라이브 자동배포 트리거 — 사용자가 별도 결정).
- 라이브 실계좌 운영 중 → 오펀/미체결 방지 로직(이미 반영된 desync 수정) 훼손 금지.

---

## File Structure

- `config/charter.py` (수정): `StrategySpec` 필드 교체, `STRATEGY_SPECS` 값, `position_size_krw` 시그니처, 스크리너/폴백 상수, 버전.
- `bot/position.py` (수정): `sl_ratio`/`tp_ratio` 필드(진입 시 ATR로 계산), `check_price_exit` 로직.
- `bot/risk_manager.py` (수정): `size_for(stop_ratio)`.
- `bot/trader.py` (수정): `_open` ATR 사이징, `tick` 스크리너 순회, `_recover_positions` ATR, `snapshot` sl/tp·universe.
- `dashboard/service.py` (수정): `bot.universe`를 동적 유니버스로.
- `data/screener.py` (신규): `select_universe` 순수 함수 + `Screener` 캐시.
- 테스트: `tests/test_atr_sizing.py`, `tests/test_position.py`, `tests/test_screener.py`, `tests/test_screening_integration.py` (신규); `tests/test_charter.py` (회귀 확인).

---

## Task 1: ATR 완전정규화 리스크 모델 (차터 + 포지션 + 사이징)

**Files:**
- Modify: `config/charter.py`
- Modify: `bot/position.py`
- Modify: `bot/risk_manager.py`
- Modify: `bot/trader.py` (snapshot의 sl/tp 표기만)
- Test: `tests/test_atr_sizing.py` (신규), `tests/test_position.py` (신규)

**Interfaces:**
- Produces:
  - `charter.StrategySpec(name: str, atr_stop_mult: float, rr: float, regime: Regime)`, 프로퍼티 `risk_reward -> float` (=`rr`).
  - `charter.position_size_krw(stop_ratio: float, capital: float, available_krw: float | None = None) -> float`
  - `charter.FALLBACK_STOP_RATIO: float`
  - `Position.sl_ratio: float`, `Position.tp_ratio: float` (진입 시 계산됨)
  - `RiskManager.size_for(stop_ratio: float) -> float`

- [ ] **Step 1: 사이징 실패 테스트 작성** — `tests/test_atr_sizing.py`

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/test_atr_sizing.py -q`
Expected: FAIL (`test_zero_or_negative_stop_ratio_returns_zero` — 현재는 0 나눗셈 ZeroDivisionError; 나머지는 시그니처 인자명 무관하게 통과할 수 있음)

- [ ] **Step 3: `charter.py`의 `StrategySpec`·`STRATEGY_SPECS`·`position_size_krw`·상수·버전 수정**

`StrategySpec` 정의 교체:

```python
@dataclass(frozen=True)
class StrategySpec:
    """전략별 진입/청산 파라미터 (헌장 §2, §4). SL/TP는 ATR 배수로 정규화(v1.2)."""
    name: str
    atr_stop_mult: float  # k: 손절거리 = k × ATR
    rr: float             # 손익비: 익절거리 = rr × (k × ATR)
    regime: Regime        # 이 전략이 유리한 레짐

    @property
    def risk_reward(self) -> float:
        return self.rr
```

`STRATEGY_SPECS` 교체(출발 기본값):

```python
STRATEGY_SPECS: dict[str, StrategySpec] = {
    "macd": StrategySpec("macd", atr_stop_mult=1.5, rr=2.0, regime=Regime.TREND),
    "rsi":  StrategySpec("rsi",  atr_stop_mult=1.2, rr=1.6, regime=Regime.RANGE),
    "cvd":  StrategySpec("cvd",  atr_stop_mult=1.3, rr=1.7, regime=Regime.REVERSAL),
}
```

`position_size_krw` 교체(인자 `stop_loss_pct` → `stop_ratio`, 0/음수 가드 추가):

```python
def position_size_krw(stop_ratio: float, capital: float,
                      available_krw: float | None = None) -> float:
    """
    리스크 상한 기반 포지션 크기 (§7.2).
        포지션 크기 = 1거래최대손실 ÷ 손절거리비율(stop_ratio)
    stop_ratio = k × ATR / 진입가 (ATR 정규화, v1.2). 코인 변동성이 크면 stop_ratio↑ → 크기↓
    → 모든 코인이 동일 KRW 리스크(자본 1%). 전략당 배분·주문가능원화로 clamp.
    """
    if stop_ratio <= 0:
        return 0.0
    raw = max_loss_per_trade_krw(capital) / stop_ratio
    alloc_cap = capital * ALLOC_PER_STRATEGY_RATIO
    size = min(raw, alloc_cap)
    if available_krw is not None:
        size = min(size, available_krw)
    return max(0.0, size)
```

상수·버전 수정: 파일 상단 `CHARTER_VERSION = "v1.0"` → `"v1.2"`, 그리고 파일 하단(파생 헬퍼 근처)에 추가:

```python
# ── ATR 사이징 폴백 (헌장 §7, v1.2) ──────────────────────────
FALLBACK_STOP_RATIO = 0.03   # entry_atr 없을 때(봉 부족/복원) SL 거리 기본값
```

- [ ] **Step 4: `risk_manager.py`의 `size_for` 시그니처 변경**

```python
    def size_for(self, stop_ratio: float) -> float:
        """1거래 리스크 = 자본 1%를 만족하는 포지션 크기 (§7.2, ATR 정규화 v1.2)."""
        return C.position_size_krw(stop_ratio, self.s.capital, self.s.available_krw)
```

(docstring의 `stop_loss_pct` 언급만 위처럼 갱신)

- [ ] **Step 5: 사이징 테스트 통과 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/test_atr_sizing.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: 포지션 ATR 청산 실패 테스트 작성** — `tests/test_position.py`

```python
"""ATR 기반 SL/TP 청산 판정 (헌장 v1.2 §4)."""
import pandas as pd
from bot.position import Position, ExitReason


def _p(entry=100.0, atr=2.0, strategy="macd"):
    return Position(strategy=strategy, market="KRW-BTC", entry_price=entry,
                    size_krw=30000, volume=1.0,
                    entry_time=pd.Timestamp("2026-07-26T00:00:00"), entry_atr=atr)


def test_atr_sl_tp_ratios_computed():
    p = _p(entry=100, atr=2)   # macd k=1.5 → sl=1.5*2/100=0.03, rr=2 → tp=0.06
    assert round(p.sl_ratio, 4) == 0.03
    assert round(p.tp_ratio, 4) == 0.06


def test_stop_loss_triggers_at_atr_distance():
    p = _p(entry=100, atr=2)   # sl 3%
    assert p.check_price_exit(96.9) == ExitReason.STOP_LOSS   # -3.1%
    assert p.check_price_exit(98.0) == ExitReason.NONE        # -2%


def test_take_profit_triggers_at_atr_distance():
    p = _p(entry=100, atr=2)   # tp 6%
    assert p.check_price_exit(106.1) == ExitReason.TAKE_PROFIT
    assert p.check_price_exit(105.0) == ExitReason.NONE


def test_fallback_ratio_when_no_atr():
    p = _p(entry=100, atr=0)   # ATR 없음 → FALLBACK_STOP_RATIO(0.03)
    assert round(p.sl_ratio, 4) == 0.03
    assert round(p.tp_ratio, 4) == 0.06   # macd rr=2 × 0.03
```

- [ ] **Step 7: 실패 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/test_position.py -q`
Expected: FAIL (`Position`에 `sl_ratio`/`tp_ratio` 없음 → AttributeError)

- [ ] **Step 8: `position.py`에 ATR 기반 SL/TP 구현**

`Position` 데이터클래스에 필드 추가(`highest_price` 아래):

```python
    highest_price: float = field(init=False)
    sl_ratio: float = field(init=False)   # 진입 시 ATR로 계산한 손절거리비율
    tp_ratio: float = field(init=False)   # 익절거리비율
```

`__post_init__` 교체:

```python
    def __post_init__(self):
        from config.charter import FALLBACK_STOP_RATIO
        self.highest_price = self.entry_price
        if self.entry_price > 0 and self.entry_atr > 0:
            stop = self.spec.atr_stop_mult * self.entry_atr / self.entry_price
        else:
            stop = FALLBACK_STOP_RATIO
        self.sl_ratio = stop
        self.tp_ratio = self.spec.rr * stop
```

`check_price_exit` 교체(고정 spec % → 저장된 비율):

```python
    def check_price_exit(self, price: float) -> ExitReason:
        """가격 기반 청산 판정 (§4.1, §4.2). 진입 시 ATR로 정한 sl/tp 비율(gross) 기준."""
        gross = (price - self.entry_price) / self.entry_price
        if gross >= self.tp_ratio:
            return ExitReason.TAKE_PROFIT
        if gross <= -self.sl_ratio:
            return ExitReason.STOP_LOSS
        return ExitReason.NONE
```

- [ ] **Step 9: `trader.py` snapshot의 sl/tp 표기 수정**

`snapshot()`의 positions 딕셔너리에서:

```python
                "sl": -pos.sl_ratio * 100,
                "tp": pos.tp_ratio * 100,
```

(기존 `-pos.spec.stop_loss * 100` / `pos.spec.take_profit * 100` 대체)

- [ ] **Step 10: 포지션·차터 회귀 테스트 통과 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/test_position.py tests/test_charter.py -q`
Expected: PASS (test_charter는 무수정 통과 — `risk_reward`는 `rr` 반환, `position_size_krw`는 위치인자 동일)

- [ ] **Step 11: 전체 스위트 회귀 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/ -q`
Expected: PASS (기존 23 + 신규 9 = 32 passed)

- [ ] **Step 12: 커밋**

```bash
git add config/charter.py bot/position.py bot/risk_manager.py bot/trader.py tests/test_atr_sizing.py tests/test_position.py
git commit -m "feat: ATR 완전정규화 리스크 모델 (SL/TP·사이징) — 헌장 v1.2"
```

---

## Task 2: 유니버스 스크리너 (`data/screener.py`)

**Files:**
- Create: `data/screener.py`
- Test: `tests/test_screener.py` (신규)

**Interfaces:**
- Consumes: `charter.UNIVERSE_TOP_N`, `charter.MIN_TURNOVER_24H_KRW`, `charter.UNIVERSE_REFRESH_SEC`, `charter.STABLECOINS`, `charter.UNIVERSE_BLACKLIST` (Step 3에서 charter에 추가), `settings.universe`(폴백).
- Produces:
  - `select_universe(tickers: list[dict], top_n: int, min_turnover: float, exclude: set[str]) -> list[str]`
  - `Screener(fallback=None, top_n=None, min_turnover=None, refresh_sec=None, exclude=None)` with `eligible() -> list[str]`, `_fetch_tickers() -> list[dict]`.

- [ ] **Step 1: charter에 스크리너 상수 추가**

`config/charter.py`의 레짐 상수 근처에 추가:

```python
# ── 종목 스크리닝 (헌장 §3, v1.2) ────────────────────────────
UNIVERSE_TOP_N = 6                       # 거래대금 상위 N개만 거래 후보
MIN_TURNOVER_24H_KRW = 10_000_000_000    # 24h 거래대금 하한(100억) 미달 제외
UNIVERSE_REFRESH_SEC = 600               # 적격 유니버스 재조회 주기(10분)
STABLECOINS = {"USDT", "USDC", "DAI", "TUSD", "BUSD"}
UNIVERSE_BLACKLIST: set[str] = set()     # 수동 제외 심볼(예: {"XYZ"})
```

- [ ] **Step 2: 스크리너 실패 테스트 작성** — `tests/test_screener.py`

```python
"""거래대금 상위 동적 유니버스 스크리너 (헌장 v1.2 §3)."""
from data.screener import select_universe, Screener


def _t(market, turnover):
    return {"market": market, "acc_trade_price_24h": turnover}


def test_ranks_by_turnover_and_caps_top_n():
    tickers = [_t("KRW-BTC", 100), _t("KRW-ETH", 80),
               _t("KRW-SOL", 60), _t("KRW-XRP", 40)]
    assert select_universe(tickers, top_n=2, min_turnover=0, exclude=set()) \
        == ["KRW-BTC", "KRW-ETH"]


def test_applies_turnover_floor():
    tickers = [_t("KRW-BTC", 100), _t("KRW-DOGE", 5)]
    assert select_universe(tickers, top_n=10, min_turnover=10, exclude=set()) \
        == ["KRW-BTC"]


def test_excludes_stablecoins_and_non_krw():
    tickers = [_t("KRW-BTC", 100), _t("KRW-USDT", 999), _t("BTC-ETH", 999)]
    assert select_universe(tickers, top_n=10, min_turnover=0, exclude={"USDT"}) \
        == ["KRW-BTC"]


def test_eligible_falls_back_when_fetch_fails():
    class S(Screener):
        def _fetch_tickers(self):
            raise RuntimeError("network down")
    s = S(fallback=("KRW-BTC", "KRW-ETH"))
    assert s.eligible() == ["KRW-BTC", "KRW-ETH"]


def test_eligible_fetches_then_caches():
    class S(Screener):
        calls = 0
        def _fetch_tickers(self):
            type(self).calls += 1
            return [_t("KRW-BTC", 100), _t("KRW-ETH", 50), _t("KRW-USDT", 999)]
    s = S(top_n=2, min_turnover=0, refresh_sec=999, exclude={"USDT"})
    assert s.eligible() == ["KRW-BTC", "KRW-ETH"]
    s.eligible()
    assert S.calls == 1   # refresh_sec 이내 → 캐시 사용
```

- [ ] **Step 3: 실패 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/test_screener.py -q`
Expected: FAIL (`No module named 'data.screener'`)

- [ ] **Step 4: `data/screener.py` 구현**

```python
"""
종목 스크리너 (헌장 §3, v1.2).
업비트 KRW 마켓 중 24h 거래대금 상위 N개(하한·스테이블·블랙리스트 제외)를 반환한다.
매 tick 호출해도 안전하도록 UNIVERSE_REFRESH_SEC 동안 캐시한다.
조회 실패 시 직전 캐시 → 없으면 폴백(settings.universe).
"""
from __future__ import annotations

import time

try:
    import requests
except ImportError:
    requests = None

from config import charter as C
from config.settings import settings

UPBIT_MARKET_ALL = "https://api.upbit.com/v1/market/all"
UPBIT_TICKER = "https://api.upbit.com/v1/ticker"


def select_universe(tickers: list[dict], top_n: int,
                    min_turnover: float, exclude: set[str]) -> list[str]:
    """티커 목록에서 KRW·거래대금 하한·제외 조건을 적용해 거래대금 상위 top_n 마켓 반환."""
    rows: list[tuple[str, float]] = []
    for t in tickers:
        market = t.get("market", "")
        if not market.startswith("KRW-"):
            continue
        symbol = market.split("-", 1)[1]
        if symbol in exclude:
            continue
        turnover = float(t.get("acc_trade_price_24h", 0) or 0)
        if turnover < min_turnover:
            continue
        rows.append((market, turnover))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [m for m, _ in rows[:top_n]]


class Screener:
    def __init__(self, fallback=None, top_n=None, min_turnover=None,
                 refresh_sec=None, exclude=None):
        self.fallback = list(fallback or settings.universe)
        self.top_n = top_n if top_n is not None else C.UNIVERSE_TOP_N
        self.min_turnover = min_turnover if min_turnover is not None else C.MIN_TURNOVER_24H_KRW
        self.refresh_sec = refresh_sec if refresh_sec is not None else C.UNIVERSE_REFRESH_SEC
        self.exclude = exclude if exclude is not None else (C.STABLECOINS | C.UNIVERSE_BLACKLIST)
        self._cache: list[str] = []
        self._last = 0.0

    def eligible(self) -> list[str]:
        now = time.monotonic()
        if self._cache and (now - self._last) < self.refresh_sec:
            return self._cache
        try:
            picked = select_universe(self._fetch_tickers(), self.top_n,
                                     self.min_turnover, self.exclude)
        except Exception:
            picked = []
        if picked:
            self._cache = picked
            self._last = now
            return picked
        return self._cache or self.fallback

    def _fetch_tickers(self) -> list[dict]:
        if requests is None:
            raise RuntimeError("requests 미설치")
        markets = requests.get(UPBIT_MARKET_ALL,
                               params={"isDetails": "false"}, timeout=5).json()
        krw = [m["market"] for m in markets
               if str(m.get("market", "")).startswith("KRW-")]
        resp = requests.get(UPBIT_TICKER,
                            params={"markets": ",".join(krw)}, timeout=5).json()
        return resp
```

- [ ] **Step 5: 스크리너 테스트 통과 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/test_screener.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: 커밋**

```bash
git add config/charter.py data/screener.py tests/test_screener.py
git commit -m "feat: 거래대금 상위 동적 유니버스 스크리너 (헌장 v1.2 §3)"
```

---

## Task 3: 트레이더 통합 (스크리너 순회 + ATR 사이징 + 오펀 ATR + 대시보드)

**Files:**
- Modify: `bot/trader.py`
- Modify: `dashboard/service.py`
- Test: `tests/test_screening_integration.py` (신규)

**Interfaces:**
- Consumes: `Screener` (Task 2), `RiskManager.size_for(stop_ratio)`·`StrategySpec.atr_stop_mult`·`Position.sl_ratio/tp_ratio` (Task 1), `indicators.ta.atr`.
- Produces: `Trader.screener: Screener`, `Trader.snapshot()["universe"]: list[str]`.

- [ ] **Step 1: 통합 실패 테스트 작성** — `tests/test_screening_integration.py`

```python
"""스크리너 유니버스 순회 + ATR 사이징 통합 (헌장 v1.2)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bot.trader import Trader, build_strategies
from bot.risk_manager import RiskManager
from bot.position import Position
from config import charter as C
from data.upbit_client import OrderResult
from indicators import ta


class _Null:
    def send(self, msg): pass


class _Rec:
    def __init__(self): self.events = []
    def log(self, event, **kw): self.events.append((event, kw))


def _df():
    idx = pd.date_range("2026-07-26", periods=30, freq="5min")
    close = pd.Series(np.linspace(100, 110, 30))
    return pd.DataFrame({"open": close, "high": close + 1.0,
                         "low": close - 1.0, "close": close, "volume": 10.0}, index=idx)


def test_open_uses_atr_sizing_and_sets_ratios():
    t = Trader.__new__(Trader)
    t.risk = RiskManager()
    t.positions = {}
    t.logger = _Rec()
    t.notifier = _Null()
    captured = {}

    class Orders:
        def enter_long(self, market, price, krw):
            captured["krw"] = krw
            return OrderResult(ok=True, filled_volume=krw / price, avg_price=price)
    t.orders = Orders()

    df = _df()
    price = float(df["close"].iloc[-1])
    strat = build_strategies()["macd"]
    t._open("macd", strat, "KRW-BTC", price, df)

    entry_atr = float(ta.atr(df).iloc[-1])
    stop_ratio = strat.spec.atr_stop_mult * entry_atr / price
    expected = C.position_size_krw(stop_ratio, t.risk.s.capital, t.risk.s.available_krw)
    assert captured["krw"] == pytest.approx(expected)
    assert "macd" in t.positions
    p = t.positions["macd"]
    assert p.sl_ratio == pytest.approx(stop_ratio)
    assert p.tp_ratio == pytest.approx(stop_ratio * 2.0)


def test_open_skips_when_no_atr():
    t = Trader.__new__(Trader)
    t.risk = RiskManager()
    t.positions = {}
    t.logger = _Rec()
    t.notifier = _Null()

    class Orders:
        def enter_long(self, *a, **k):
            raise AssertionError("ATR 없으면 진입하면 안 됨")
    t.orders = Orders()

    short_df = _df().iloc[:5]   # 14봉 미만 → ATR 불가
    strat = build_strategies()["macd"]
    t._open("macd", strat, "KRW-BTC", 100.0, short_df)
    assert "macd" not in t.positions
    assert "entry_fail" in [e for e, _ in t.logger.events]


def test_tick_scans_screener_markets():
    t = Trader.__new__(Trader)
    t.risk = RiskManager()
    t.positions = {}
    t.last_prices = {}
    t.regimes = {}
    t.notifier = _Null()
    scanned = []

    class Client:
        def get_account_equity(self, lookup): return (90_000.0, 90_000.0)
        def get_candles(self, market):
            scanned.append(market)
            raise RuntimeError("skip processing")
    t.client = Client()

    from safety.failsafe import Failsafe
    t.failsafe = Failsafe(t.client, order_manager=None, notifier=_Null())

    class Scr:
        def eligible(self): return ["KRW-AAA", "KRW-BBB", "KRW-CCC"]
    t.screener = Scr()

    t.tick()
    assert scanned == ["KRW-AAA", "KRW-BBB", "KRW-CCC"]
```

- [ ] **Step 2: 실패 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/test_screening_integration.py -q`
Expected: FAIL (`_open`이 ATR 사이징 미적용/`tick`이 `settings.universe` 순회 → `screener` 속성 없음)

- [ ] **Step 3: `trader.py` import·`__init__`에 스크리너 추가**

import 블록에 추가:

```python
from data.screener import Screener
```

`__init__`에 추가(`self.orders = OrderManager(self.client)` 다음 줄 부근):

```python
        self.screener = Screener()
```

- [ ] **Step 4: `tick()`이 스크리너 유니버스를 순회하도록 수정**

`tick()` 내부의 `for market in settings.universe:` 를 다음으로 교체:

```python
        for market in self.screener.eligible():
```

- [ ] **Step 5: `_open`을 ATR 사이징으로 교체**

`_open` 전체를 교체:

```python
    def _open(self, name: str, strat: BaseStrategy, market: str,
              price: float, df: pd.DataFrame) -> None:
        entry_atr = float(ta.atr(df).iloc[-1]) if len(df) >= 14 else 0.0
        if entry_atr <= 0:
            self.logger.log("entry_fail", strategy=name, market=market, reason="no atr")
            return
        stop_ratio = strat.spec.atr_stop_mult * entry_atr / price   # §7.2 (ATR 정규화)
        krw = self.risk.size_for(stop_ratio)
        # 잔고 부족 등으로 최소주문 미만이면 조용히 스킵(로그 스팸 방지)
        if krw < C.MIN_ORDER_KRW:
            return
        res = self.orders.enter_long(market, price, krw)            # §6
        if not res.ok or res.filled_volume <= 0:
            self.logger.log("entry_fail", strategy=name, market=market,
                            reason=res.error or "no fill")
            return
        self.positions[name] = Position(
            strategy=name, market=market, entry_price=res.avg_price or price,
            size_krw=krw, volume=res.filled_volume, entry_time=df.index[-1],
            entry_atr=entry_atr,
        )
        self.risk.on_open()
        self.logger.log("entry", strategy=name, market=market, price=price,
                        volume=res.filled_volume, size_krw=krw, reason=strat.signal(df).reason)
```

- [ ] **Step 6: `_recover_positions`의 오펀 포지션에 entry_atr 부여**

`_recover_positions` 내부, `Position(...)` 생성 직전에 해당 마켓 캔들로 ATR을 구한다. 기존 `entry_time` 계산 블록 아래, `self.positions[slot] = Position(...)` 를 다음으로 교체:

```python
            try:
                rec_df = self.client.get_candles(o["market"])
                entry_atr = float(ta.atr(rec_df).iloc[-1]) if len(rec_df) >= 14 else 0.0
            except Exception:
                entry_atr = 0.0
            self.positions[slot] = Position(
                strategy=slot, market=o["market"], entry_price=o["avg_price"],
                size_krw=o["avg_price"] * o["volume"], volume=o["volume"],
                entry_time=entry_time, entry_atr=entry_atr,
            )
```

(entry_atr=0이면 Position이 FALLBACK_STOP_RATIO로 SL/TP를 잡아 관리 지속)

- [ ] **Step 7: `snapshot()`에 동적 유니버스 추가**

`snapshot()` 반환 딕셔너리 끝에 키 추가:

```python
            "universe": self.screener.eligible(),
```

- [ ] **Step 8: `dashboard/service.py`의 `bot.universe`를 동적으로**

`status()`의 `"universe": list(settings.universe),` 를 다음으로 교체:

```python
            "universe": self.trader.screener.eligible(),
```

- [ ] **Step 9: 통합 테스트 통과 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/test_screening_integration.py -q`
Expected: PASS (3 passed)

- [ ] **Step 10: 전체 스위트 회귀 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/ -q`
Expected: PASS (35 passed)

기존 `tests/test_desync.py`의 오펀 복원 테스트(`test_boot_recovers_orphan_as_managed_position`)는 `FakeClient`에 `get_candles`가 없어 실패할 수 있다. 실패하면 그 테스트의 `FakeClient`에 아래를 추가한다:

```python
    def get_candles(self, market):
        raise RuntimeError("no candles in fake")
```

(→ `_recover_positions`의 try/except가 entry_atr=0으로 폴백 → 복원은 계속 동작)

- [ ] **Step 11: 커밋**

```bash
git add bot/trader.py dashboard/service.py tests/test_screening_integration.py tests/test_desync.py
git commit -m "feat: 트레이더에 동적 스크리너 유니버스 순회 + ATR 사이징 통합"
```

---

## Task 4: 헌장 문서 동기화 + 최종 검증

**Files:**
- Modify: `docs/TRADING_CHARTER_KR.md`
- (검증) 전체 테스트 + 임포트 스모크

**Interfaces:** 없음(문서·검증).

- [ ] **Step 1: 헌장 문서 읽기**

Run: `Read docs/TRADING_CHARTER_KR.md` — §1(유니버스/봉), §3(진입/중복), §4(청산 SL/TP), §7(사이징), §13(파라미터표), 버전 표기 위치 확인.

- [ ] **Step 2: 문서에 v1.2 변경 반영**

다음 내용을 해당 절에 반영(코드 SSOT와 일치):
- **버전**: 문서 상단/이력에 `v1.2 (2026-07-26): 동적 종목 스크리닝 + ATR 완전정규화` 추가.
- **§3 유니버스**: 고정 BTC/ETH → **업비트 KRW 거래대금 상위 top-6 동적 선정**(24h 거래대금 하한 100억, 스테이블코인·블랙리스트 제외, 10분 갱신, 조회 실패 시 직전/폴백). 최대 3포지션·전략당 1개·같은코인 중복금지는 유지.
- **§4 청산(SL/TP)**: 고정 % → **ATR 배수**. 손절거리 = k×ATR, 익절거리 = rr×손절거리. 표: macd k=1.5/rr=2.0, rsi k=1.2/rr=1.6, cvd k=1.3/rr=1.7. ATR 없으면 폴백 0.03.
- **§7 사이징**: 포지션 = 자본1% ÷ (k×ATR/진입가). 변동성 큰 코인일수록 자동 축소 → 동일 KRW 리스크. 전략당 33%·주문가능원화 clamp 유지.
- **§13 파라미터표**: `UNIVERSE_TOP_N=6`, `MIN_TURNOVER_24H_KRW=100억`, `UNIVERSE_REFRESH_SEC=600`, ATR 배수 표, `FALLBACK_STOP_RATIO=0.03` 추가/갱신. 기존 고정 SL/TP 행은 ATR 배수로 대체.

- [ ] **Step 3: 전체 테스트 최종 확인**

Run: `cd upbit-rbi-bot && .venv/bin/python -m pytest tests/ -q`
Expected: PASS (35 passed)

- [ ] **Step 4: 임포트·스크리너 스모크 (네트워크 실제 호출 확인은 선택)**

Run:
```bash
cd upbit-rbi-bot && DRY_RUN=true .venv/bin/python -c "import bot.trader, data.screener, dashboard.app; from data.screener import Screener; print('import OK'); print('eligible:', Screener(refresh_sec=0).eligible()[:6])"
```
Expected: `import OK` + 실제 거래대금 상위 목록(네트워크 가능 시) 또는 폴백 `['KRW-BTC','KRW-ETH']`(오프라인 시). 둘 다 정상.

- [ ] **Step 5: 커밋**

```bash
git add docs/TRADING_CHARTER_KR.md
git commit -m "docs: 헌장 v1.2 — 동적 스크리닝 + ATR 정규화 반영"
```

- [ ] **Step 6: 배포 안내(푸시 금지)**

사용자에게 보고: 로컬 커밋 완료, 전체 테스트 통과. `main` push 시 라이브 자동배포되며 다음 tick부터 동적 유니버스·ATR 사이징이 적용됨을 안내하고 **push 여부를 확인**받는다. (미배포 스펙/문서 커밋들도 함께 나감)

---

## Self-Review

**스펙 커버리지:**
- §4.1 스크리너 → Task 2 ✅
- §4.2 ATR 리스크 모델(StrategySpec/사이징/Position 청산) → Task 1 ✅
- §4.3 트레이더 통합(tick 순회/_open/_recover/snapshot) → Task 3 ✅
- §4.4 설정/상수 → Task 1(사이징·버전·FALLBACK) + Task 2(스크리너 상수) ✅
- §5 데이터 흐름 → Task 3 통합으로 실현 ✅
- §6 에러 처리(스크리너 폴백/ATR NaN/최소주문) → Task 2 Step4, Task 3 Step5 ✅
- §7 테스트 계획 → 각 Task 테스트 ✅
- §8 헌장 문서 동기화 → Task 4 ✅

**플레이스홀더 스캔:** 없음(모든 코드 블록 실제 내용).

**타입 일관성:** `position_size_krw(stop_ratio,...)`·`size_for(stop_ratio)`·`select_universe(tickers, top_n, min_turnover, exclude)`·`Screener.eligible()`·`Position.sl_ratio/tp_ratio`·`StrategySpec.atr_stop_mult/rr` — Task 간 시그니처 일치 확인 완료.

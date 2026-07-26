# 설계: 동적 종목 스크리닝 + ATR 완전정규화 리스크 모델

- **날짜**: 2026-07-26
- **대상 리포**: `upbit-rbi-bot/`
- **관련 Q&A**: `docs/LIFE_CHANGE_QA.md` Q9·Q10
- **헌장 버전**: v1.1 → **v1.2** (이 설계로 개정)

## 1. 배경 & 목표

현재 봇은 **정적 2종목(BTC/ETH)** 만 거래하고, SL/TP·포지션 크기가 **코인 무관 고정 %** 다. 변동성이 몰렸다 이동하는 암호화폐 시장에서 이 구조는 ① 로테이션 기회를 놓치고 ② 변동성이 다른 코인을 넣으면 고정 %가 whipsaw(정상 출렁임에도 손절)를 유발한다. 종목 선정(스크리닝)이라는 알파 층 자체가 없다.

**목표**: 90k 자본·인큐베이션 단계에 맞는 **점진적** 개선으로,
1. 거래 대상을 **거래대금 상위 동적 유니버스**로 확장(유동성 기반 로테이션).
2. SL/TP·사이징을 **ATR 기반으로 완전정규화** → 어떤 코인이든 1거래 리스크 = 자본 1%로 동일.

## 2. 비목표 (YAGNI — 다음 단계)

- 모멘텀/상대강도 **랭킹 알파**(횡단면 선택). 이번엔 *유동성 적격 필터*까지만.
- 상장 경과일 필터, 섹터/상관 상한 강제 차단.
- 동시 포지션 수 확대 (90k에서는 최대 3 유지).
- 트레일링 스톱(ATR은 진입 시점 기준으로 고정).

## 3. 현재 상태 (변경 대상)

- `config/settings.py` → `universe = ("KRW-BTC","KRW-ETH")` 하드코딩.
- `config/charter.py` → `StrategySpec(stop_loss, take_profit)` 고정 %; `position_size_krw(stop_loss_pct, ...)`.
- `bot/position.py` → `check_price_exit`가 `spec.take_profit/stop_loss`(고정 gross %)로 판정. `entry_atr` 필드는 이미 존재(죽은포지션용).
- `bot/trader.py` → `tick()`이 `settings.universe`를 순회; `_open`이 `risk.size_for(strat.spec.stop_loss)`로 사이징.
- `bot/risk_manager.py` → `size_for(stop_loss_pct)` → `C.position_size_krw`.
- `indicators/ta.py` → `atr(df, period=14)` 존재(사이징엔 미사용).
- `data/upbit_client.py` → 시세/주문 래퍼. 티커(거래대금) 조회 없음.

## 4. 컴포넌트 설계

### 4.1 유니버스 스크리너 — `data/screener.py` (신규)

**책임**: 업비트 KRW 마켓 중 "지금 거래 가능한 고유동성 종목"을 반환.

- **데이터 소스**: 업비트 공개 REST (인증 불필요), `requests` 사용(기존 의존성).
  - `GET https://api.upbit.com/v1/market/all` → KRW 마켓 목록.
  - `GET https://api.upbit.com/v1/ticker?markets=<KRW 전부>` → 각 종목 `acc_trade_price_24h`(24h 누적 거래대금).
- **순수 선정 로직**(테스트 대상, 부수효과 없음):
  `select_universe(tickers: list[dict], top_n, min_turnover, exclude: set[str]) -> list[str]`
  1. `KRW-*` 만, 심볼이 `exclude`(스테이블코인+블랙리스트)에 없는 것.
  2. `acc_trade_price_24h >= min_turnover` (하한 미달 제외).
  3. 거래대금 내림차순 정렬 → 상위 `top_n` 반환.
- **캐시/갱신**: `Screener.eligible() -> list[str]`. 마지막 조회가 `UNIVERSE_REFRESH_SEC`(600s) 이내면 캐시 반환, 아니면 재조회. `time.monotonic()` 기준.
- **실패 처리**: REST 실패/빈 결과 → **직전 캐시** 반환; 캐시도 없으면 **폴백 `settings.universe`**(BTC/ETH). 실패는 1회 로깅(스팸 방지).
- **의존성**: `requests`, `config.charter`(상수), `config.settings`(폴백).

인터페이스 요약:
```
class Screener:
    def __init__(self, fallback: tuple[str,...] = settings.universe): ...
    def eligible(self) -> list[str]:          # 캐시된 적격 유니버스
    def _fetch_tickers(self) -> list[dict]:   # REST (실패 시 예외/빈값)
# 순수 함수 (모듈 레벨)
def select_universe(tickers, top_n, min_turnover, exclude) -> list[str]
```

### 4.2 ATR 완전정규화 리스크 모델 — `config/charter.py` + `bot/position.py` + `bot/risk_manager.py`

**StrategySpec 변경** (SSOT):
```
@dataclass(frozen=True)
class StrategySpec:
    name: str
    atr_stop_mult: float   # k: SL 거리 = k × ATR
    rr: float              # 손익비: TP 거리 = rr × (k × ATR)
    regime: Regime
```
출발 기본값(백테스트로 조정):
| 전략 | atr_stop_mult (k) | rr | 성격 |
|------|------|----|------|
| macd | 1.5 | 2.0 | 추세 — 넓게 태움 |
| rsi  | 1.2 | 1.6 | 되돌림 — 짧고 빠르게 |
| cvd  | 1.3 | 1.7 | 반전 — 중간 |

> `risk_reward` 프로퍼티는 `rr` 을 그대로 반환(테스트 `test_strategy_risk_reward_ge_1_5` 유지).

**진입 시 SL/TP 확정** (진입가·entry_atr 기준, 트레일링 아님):
- `stop_ratio = atr_stop_mult × entry_atr / entry_price`
- `sl_ratio = stop_ratio`, `tp_ratio = rr × stop_ratio` (gross 등락률 기준)
- `Position`에 `sl_ratio`, `tp_ratio` 저장(진입 시 계산). `entry_atr <= 0`(봉 부족)이면 진입 스킵.

**`Position.check_price_exit`**: 고정 `spec.stop_loss/take_profit` → 저장된 `self.sl_ratio/self.tp_ratio` 사용.
```
gross = (price - entry_price)/entry_price
if gross >= tp_ratio: TAKE_PROFIT
if gross <= -sl_ratio: STOP_LOSS
```

**사이징** — `charter.position_size_krw`:
```
def position_size_krw(stop_ratio, capital, available_krw=None):
    raw = max_loss_per_trade_krw(capital) / stop_ratio   # 자본1% ÷ 손절거리비율
    size = min(raw, capital * ALLOC_PER_STRATEGY_RATIO)
    if available_krw is not None: size = min(size, available_krw)
    return max(0.0, size)
```
- 인자를 `stop_loss_pct`(고정) → `stop_ratio`(ATR 기반)로 일반화. 계산식은 동일(리스크÷손절거리). 변동성 큰 코인 = 큰 stop_ratio = 작은 포지션 → **동일 KRW 리스크**.
- `RiskManager.size_for` 시그니처: `size_for(stop_ratio)`.

### 4.3 트레이더 통합 — `bot/trader.py`

- `__init__`: `self.screener = Screener()`.
- `tick()`: `markets = self.screener.eligible()` 로 순회(폴백 포함). 자본 갱신·피드 감시는 그대로.
- `_process_market`: 진입 판정 시 `entry_atr` 계산 → `stop_ratio` 산출 → `risk.size_for(stop_ratio)`.
- `_open`: `Position` 생성 시 `sl_ratio/tp_ratio` 세팅. `entry_atr<=0` 또는 사이즈 < 최소주문 → 스킵.
- **유지**: 전략당 1포지션 / 같은코인 중복금지(`_is_duplicate`) / 최대 3포지션. 전략은 거래대금 상위 순서로 순회하며 첫 적격 코인 진입.
- 복원(`_recover_positions`)된 오펀도 ATR 기반 SL/TP를 부여(진입가=avg_buy_price, entry_atr=최신 캔들 ATR로 산정) → 관리 일관성.
- `snapshot()`: `positions[].sl/tp`를 저장된 `sl_ratio/tp_ratio`(%)로 표기. `bot.universe`(대시보드)는 스크리너 적격 유니버스를 반영.

### 4.4 설정/상수 — `config/charter.py`, `config/settings.py`

- `charter.py` 신규 상수: `UNIVERSE_TOP_N=6`, `MIN_TURNOVER_24H_KRW=10_000_000_000`(100억), `UNIVERSE_REFRESH_SEC=600`, `STABLECOINS={"USDT","USDC","DAI","TUSD","BUSD"}`, `UNIVERSE_BLACKLIST: set[str]=set()`. `CHARTER_VERSION="v1.2"`.
- `settings.universe`는 폴백 기본값으로 의미 유지(주석 갱신).

## 5. 데이터 흐름

```
[10분 주기] Screener.eligible()
   └ Upbit REST(market/all + ticker) → select_universe(top_n, floor, exclude) → 캐시
          │ (실패 시 캐시/폴백)
          ▼
[매 tick] Trader.tick() → markets = eligible()
   for market in markets:
       get_candles → detect_regime
       (청산) check_price_exit(sl_ratio/tp_ratio) → dead → reverse
       (진입) 레짐활성 & 신호 & 중복통과
              → entry_atr → stop_ratio = k·ATR/price
              → size = risk.size_for(stop_ratio)  (자본1%÷stop_ratio)
              → enter_long → Position(sl_ratio, tp_ratio)
```

## 6. 에러 처리

| 상황 | 처리 |
|------|------|
| 스크리너 REST 실패/빈값 | 직전 캐시 → 없으면 폴백(BTC/ETH), 1회 알림 |
| ATR NaN/0 (봉 부족) | 해당 코인 진입 스킵(사이징 불가) |
| 사이즈 < 최소주문(5,000) | 진입 스킵(기존 로직) |
| get_candles 실패(개별 코인) | 해당 코인 skip(기존 on_api_error) |

## 7. 테스트 계획

- `tests/test_screener.py`: `select_universe` — 거래대금 내림차순 top-N, 하한 미달 제외, 스테이블/블랙리스트 제외, KRW 외 제외; `Screener.eligible` 캐시/폴백(가짜 `_fetch_tickers`).
- `tests/test_atr_sizing.py`: `position_size_krw(stop_ratio)` — 리스크 = 자본1% 검산; **변동성 다른 두 코인이 동일 KRW 리스크**; alloc/available clamp; 최소주문 미만 반환.
- `tests/test_position.py`(신규 또는 확장): `check_price_exit`가 `sl_ratio/tp_ratio`로 TP/SL 판정; ATR→sl/tp_ratio 산출.
- `tests/test_charter.py`: 새 `StrategySpec`(atr_stop_mult/rr) 반영, `risk_reward>=1.5` 유지.
- 통합(가짜 Screener 주입): `Trader.tick`이 적격 유니버스를 순회하고 ATR 사이징으로 진입.
- 기존 23개 테스트 회귀 없음 확인.

## 8. 헌장 문서 동기화

- 코드 SSOT(`charter.py`)를 v1.2로 개정. `docs/TRADING_CHARTER_KR.md`의 §3(유니버스)·§4(SL/TP)·§7(사이징)·§13(파라미터표)에 스크리너·ATR 모델 반영(별도 문서 편집, 코드와 일치 유지).

## 9. 미해결/후속 (범위 밖 기록)

- 모멘텀 랭킹 알파(자본 증가 후).
- 거래대금 하한/Top-N/ATR 배수는 **백테스트로 확정할 출발 가설**(승률>55%, 손익비>1.5, 100거래+, 수수료·슬리피지 반영).
- 상관/방향 편중 강제 차단, 상장경과일 필터.

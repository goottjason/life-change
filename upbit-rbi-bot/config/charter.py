"""
헌장(Trading Charter) 규칙을 코드 상수로 고정한 단일 기준(SSOT).

이 파일의 값은 docs/TRADING_CHARTER_KR.md 의 §13 파라미터 표와 1:1로 대응한다.
코드 어디서든 매매 파라미터가 필요하면 반드시 여기서 import 한다.
값을 바꾸려면 먼저 헌장을 개정(버전업)한 뒤 이 파일에 반영한다. (헌장 §14)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

CHARTER_VERSION = "v1.7"

# ── 자본·수수료 (헌장 §1, §13) ────────────────────────────────
# 자본은 더 이상 고정값이 아니라 '실계좌 잔고(총 자산)'를 런타임에 읽어서 쓴다 (헌장 v1.1 §7.1).
# 아래 값은 DRY_RUN(모의) 및 계좌 조회 실패 시의 폴백 기본값일 뿐이다.
DEFAULT_CAPITAL_KRW = 90_000        # 폴백 기본 자본 (실전에선 계좌 잔고로 대체됨)
ALLOC_PER_STRATEGY_RATIO = 1 / 3    # 전략당 배분 ≈33% = 30,000원 (§7.1)
FEE_ROUNDTRIP = 0.001               # 왕복 수수료 0.1% (0.05% × 2) (§6, 업비트 KRW)
MIN_ORDER_KRW = 5_000               # 업비트 최소주문금액 (§6.5)

# ── 리스크·서킷 브레이커 (헌장 §5) ───────────────────────────
RISK_PER_TRADE_RATIO = 0.01         # 1거래 최대손실 = 자본 1% (§5.1)
DAILY_LOSS_LIMIT_RATIO = 0.03       # 일일 손실 한도 = 자본 -3% (§5.2)
MAX_CONSECUTIVE_LOSSES = 5          # 연속 손절 차단 (§5.3)
MAX_DRAWDOWN_RATIO = 0.15           # MDD 전면정지 = 고점 대비 -15% (§5.4)
MAX_CONCURRENT_POSITIONS = 3        # 동시 최대 포지션 (§3.4, §5.5)

# ── 봉·시간 손절 (헌장 §1, §4-A) ─────────────────────────────
BASE_TIMEFRAME = "minute5"          # 기준 봉 (pyupbit interval)
TIME_STOP_BARS = 24                 # 시간 손절: 24봉(2시간) (§4-A)
FLAT_BAND_RATIO = 0.005             # 횡보 판정 밴드 ±0.5% (§4-A)
FLAT_BARS = 12                      # 횡보 지속 봉 수 (§4-A)
ATR_SHRINK_RATIO = 0.5              # 변동성 축소 판정: 진입시 ATR의 50% 이하 (§4-A, 백테스트 후 채택)

# ── 주문 집행 (헌장 §6) ──────────────────────────────────────
LIMIT_UNFILLED_TIMEOUT_SEC = 30     # 지정가 미체결 취소 시간 (§6.3)
LIMIT_REORDER_MAX = 1               # 미체결 재주문 허용 횟수 (§6.3)

# ── 백테스트 통과 기준 (헌장 §11) ────────────────────────────
BACKTEST_MIN_WINRATE = 0.55
BACKTEST_MIN_PROFIT_FACTOR = 1.5
BACKTEST_MAX_DRAWDOWN = 0.20
BACKTEST_MIN_TRADES = 100

# ── 레짐 필터 (헌장 §8) ──────────────────────────────────────
ADX_TREND_THRESHOLD = 25            # ADX 이 값 이상이면 추세장 (초기값, 백테스트로 확정)

# ── 종목 스크리닝 (헌장 §3, v1.2 / 스프레드 필터 v1.3 / 풀 확대 v1.6) ───────
# v1.6에서 추적 종목 풀을 6 → 15로 늘렸다. 실측(2026-07-26): 스프레드 ≤0.1%를 통과하는
# KRW 종목이 18개인데, 거래대금 100억 하한이 그중 5개만 남기고 후보 스캔 범위(상위 24개)가
# 12개를 아예 검사하지 않아 실제 풀이 3~5개에 불과했다.
# 거래대금 하한을 30억으로 낮춘 근거: 주문금액 30,000원은 하루 거래대금 30억의 0.001%로
# 시장충격이 없고, 실제 체결 품질은 스프레드(≤0.1%)와 최우선호가 잔량(≥30,000원)이 보장한다.
UNIVERSE_TOP_N = 15                      # 동시 추적 종목 수 (동시 보유는 §5.5로 3개 제한)
MIN_TURNOVER_24H_KRW = 3_000_000_000     # 24h 거래대금 하한(30억) 미달 제외
UNIVERSE_REFRESH_SEC = 600               # 적격 유니버스 재조회 주기(10분)
STABLECOINS = {"USDT", "USDC", "DAI", "TUSD", "BUSD"}
UNIVERSE_BLACKLIST: set[str] = set()     # 수동 제외 심볼(예: {"XYZ"})

# 호가 스프레드 상한 (v1.3) — 거래대금만 보면 안 되는 이유:
# 업비트 KRW는 가격대별 호가 단위(tick)가 고정이라 **가격이 낮은 코인은 한 틱이 이미 0.2~0.9%**다.
# 2026-07-26 실측: DOGE 0.930%, ADA 0.412%, TRX 0.206% vs BTC 0.067%, BCH 0.033%.
# 왕복 스프레드가 거래당 기댓값(≈0.18%)을 넘으면 어떤 신호로도 구조적 손실이므로
# 거래대금 상위여도 스프레드가 넓은 종목은 유니버스에서 제외한다.
MAX_SPREAD_RATIO = 0.001                 # (매도호가−매수호가)/중간가 상한 0.1%
# 후보 스캔 배수 (v1.6에서 4 → 8): 스프레드를 통과하는 종목은 거래대금 순위와 무관하게
# 흩어져 있다(실측: SOL 27위, AVAX 29위, SUI 36위, ATOM 120위). 스캔 범위가 좁으면
# 조건을 만족하는 종목을 놓친다. top_n(15)×8 = 120종목까지 오더북을 확인한다
# (오더북 API는 30종목/요청이라 4회 호출, 10분 주기라 부담 없음).
SPREAD_CANDIDATE_MULT = 8
# 진입 직전 스프레드 재확인 (v1.6) — 유니버스는 10분 주기로 갱신되므로 스크리닝 시점의
# 스프레드가 최신이 아니다. 실제 주문 전에 한 번 더 확인해 넓어졌으면 진입을 취소한다.
# (풀을 넓히면 스프레드 변동이 큰 종목이 섞이므로 이 확인이 필수가 된다)
VERIFY_SPREAD_ON_ENTRY = True

# 스프레드 '안정성' 요건 (v1.7) — 순간 스냅샷으로는 판단할 수 없다.
# 실측(2026-07-26): LPT 스프레드가 스냅샷 0.044% vs 8회 중앙값 **0.245%** 로 크게 흔들렸다.
# 진입 직전 확인(§6.7)은 진입 쪽만 보장한다 — **청산 시점 스프레드는 진입할 때 알 수 없다**.
# 따라서 최근 관측치의 중앙값과 최댓값을 함께 보고, 변동이 큰 종목은 유니버스에서 뺀다.
SPREAD_HISTORY_LEN = 6                   # 유니버스 갱신 주기(10분)×6 = 최근 1시간 관측
SPREAD_HISTORY_MIN_SAMPLES = 3           # 이 개수 이상 모이면 안정성 판정 적용
SPREAD_MAX_MULT = 2.0                    # 관측 최댓값이 상한의 이 배수를 넘으면 제외

# 최우선 호가 잔량 하한 (v1.3) — 스프레드가 좁아도 잔량이 주문금액보다 작으면 호가를 타고
# 올라가며 체결돼(= walking the book) 측정한 스프레드보다 큰 비용을 낸다. 전략당 배분
# (자본의 1/3 ≈ 30,000원)을 최우선 호가에서 소화할 수 있는 종목만 거래한다.
MIN_TOP_DEPTH_KRW = 30_000

# 최소 상장 경과일 (v1.5) — 신규 상장 코인 배제.
# 거래대금 상위만 보면 **상장 당일 코인이 1위로 올라온다**(2026-07-26 실측: EUL 상장 0일, 587억 1위).
# 신규 상장 코인은 변동성이 극단적이라 ATR 게이트(§2)를 아주 쉽게 통과한다 → 전략이 오히려
# 미검증 종목을 선호하는 선택 편향이 생긴다. 검증은 2년 히스토리 종목에서만 했으므로,
# 최소 1년 이상 데이터가 있는 종목만 거래한다.
MIN_LISTING_DAYS = 365

# ── 상위 타임프레임 추세 캐시 (헌장 §2, v1.3) ─────────────────
# rsi2 는 1시간봉 EMA200 위에서만 진입한다. 판정 기준이 '직전에 완성된 1시간봉'이므로
# 15분마다 갱신하면 충분하다(매 tick 조회하면 API 낭비).
TREND_REFRESH_SEC = 900


class Regime(str, Enum):
    TREND = "trend"      # 추세장 → MACD
    RANGE = "range"      # 횡보장 → RSI 평균회귀
    REVERSAL = "reversal"  # 반전 → CVD 보조


@dataclass(frozen=True)
class StrategySpec:
    """전략별 진입/청산 파라미터 (헌장 §2, §4). SL/TP는 ATR 배수로 정규화(v1.2)."""
    name: str
    atr_stop_mult: float  # k: 손절거리 = k × ATR
    rr: float             # 손익비: 익절거리 = rr × (k × ATR)
    regime: Regime        # 이 전략이 유리한 레짐
    # ── v1.3 추가: 백테스트로 검증된 설정을 라이브에 그대로 재현하기 위한 필드 ──
    stop_pct: float | None = None   # 고정 손절거리비율. 지정하면 ATR 대신 이 값을 쓴다
    min_atr_ratio: float = 0.0      # 진입 변동성 게이트: ATR/가격이 이 값 미만이면 진입 금지
    time_stop_bars: int | None = None   # 전략별 시간손절(봉). None이면 전역 TIME_STOP_BARS
    use_dead_extras: bool = True    # §4-A의 부가 규칙(횡보·신호중립·ATR축소) 사용 여부
    always_active: bool = False     # True면 레짐 필터(§8)를 통과시킨다
    timeframe: str = BASE_TIMEFRAME  # 이 전략이 판단에 쓰는 봉 (pyupbit interval)

    @property
    def risk_reward(self) -> float:
        return self.rr


# 전략 스펙 — 헌장 §4 표와 1:1 대응
STRATEGY_SPECS: dict[str, StrategySpec] = {
    "macd": StrategySpec("macd", atr_stop_mult=1.5, rr=2.0, regime=Regime.TREND),
    "rsi":  StrategySpec("rsi",  atr_stop_mult=1.2, rr=1.6, regime=Regime.RANGE),
    "cvd":  StrategySpec("cvd",  atr_stop_mult=1.3, rr=1.7, regime=Regime.REVERSAL),
    # rsi2 (v1.3) — 2년 5분봉·홀드아웃 17개월에서 §11을 통과한 유일한 설정.
    # 검증 성적: 553거래 승률 69.1% PF 1.49 거래당 +0.180%(수수료+실측스프레드 차감) t+3.48
    #            계좌 +32.7% MDD 5.9% (backtesting/research/README.md)
    # 손절/익절은 ATR이 아니라 고정 2.5%다(검증된 값). 레짐 필터는 적용하지 않는다
    # (ADX 필터는 개선 근거가 확인되지 않았고, 검증 시에도 쓰지 않았다).
    "rsi2": StrategySpec("rsi2", atr_stop_mult=0.0, rr=1.0, regime=Regime.RANGE,
                         stop_pct=0.025, min_atr_ratio=0.006, time_stop_bars=96,
                         use_dead_extras=False, always_active=True,
                         timeframe="minute5"),
    # rsi2_15m (v1.4) — 같은 신호를 15분봉에 적용. 15분봉은 '한 봉 평균 움직임 0.323% vs
    # 왕복비용 0.15%'로 5분봉(0.196% vs 0.15%)보다 비용 부담이 절반이라 거래당 엣지가 크다.
    # 검증(홀드아웃 17개월): 188거래 승률 73.9% PF 1.96 거래당 +0.489% t+3.75
    # 5분봉과 병행 시(동시 3포지션·동일코인 중복 금지): 741신호 PF 1.64 t+5.06 계좌 +51.4% MDD 6.2%
    "rsi2_15m": StrategySpec("rsi2_15m", atr_stop_mult=0.0, rr=1.0, regime=Regime.RANGE,
                             stop_pct=0.030, min_atr_ratio=0.010, time_stop_bars=32,
                             use_dead_extras=False, always_active=True,
                             timeframe="minute15"),
}

# 가동 전략 (v1.4) — macd/rsi/cvd 는 장기·walk-forward·국면분해에서 모두 음의 기댓값으로
# 확인되어 비활성화한다(backtesting/research/README.md). 되살리려면 §11 기준을 먼저 통과해야 한다.
ACTIVE_STRATEGIES: tuple[str, ...] = ("rsi2", "rsi2_15m")

# ── 파생 계산 헬퍼 (모두 '현재 자본(capital)'을 인자로 받는다) ──────────────
# capital = 실계좌 총 자산(원화 + 보유코인 평가액). 입금하면 자동으로 커지고,
# 출금/손실이면 작아진다 → 모든 리스크 한도·포지션 크기가 잔고에 비례해 스케일된다.

def max_loss_per_trade_krw(capital: float) -> float:
    """1거래 최대 손실 금액 (§5.1). capital × 1%. 예: 90,000 → 900원."""
    return capital * RISK_PER_TRADE_RATIO


def daily_loss_limit_krw(capital: float) -> float:
    """일일 손실 한도 금액 (§5.2). capital × 3%. 예: 90,000 → 2,700원."""
    return capital * DAILY_LOSS_LIMIT_RATIO


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


# ── ATR 사이징 폴백 (헌장 §7, v1.2) ──────────────────────────
FALLBACK_STOP_RATIO = 0.03   # entry_atr 없을 때(봉 부족/복원) SL 거리 기본값
MIN_STOP_RATIO = 0.01        # 손절거리 하한 1% (§7). 5분봉 저변동 시 ATR 스톱이 수수료(0.1%) 밑으로
                             # 내려가 수수료로 잔고를 갉아먹는 것을 방지(초단타 스캘핑 금지 원칙).


def stop_ratio_from_atr(atr_stop_mult: float, entry_atr: float, entry_price: float) -> float:
    """
    손절거리비율 = k×ATR/진입가 (§7). 단 MIN_STOP_RATIO(수수료 방어 하한)보다 작아지지 않게 바닥.
    ATR/가격이 유효하지 않으면 FALLBACK_STOP_RATIO.
    """
    if entry_price > 0 and entry_atr > 0:
        return max(atr_stop_mult * entry_atr / entry_price, MIN_STOP_RATIO)
    return FALLBACK_STOP_RATIO


def stop_ratio_for(spec: StrategySpec, entry_atr: float, entry_price: float) -> float:
    """전략 스펙에 맞는 손절거리비율 (v1.3).

    spec.stop_pct 가 있으면 그 고정값(백테스트에서 검증된 값)을 쓰고, 없으면 ATR 정규화(§7).
    """
    if spec.stop_pct is not None:
        return spec.stop_pct
    return stop_ratio_from_atr(spec.atr_stop_mult, entry_atr, entry_price)


def time_stop_bars_for(spec: StrategySpec) -> int:
    """전략별 시간손절 봉 수 (§4-A). 지정이 없으면 전역 기본값."""
    return spec.time_stop_bars if spec.time_stop_bars is not None else TIME_STOP_BARS

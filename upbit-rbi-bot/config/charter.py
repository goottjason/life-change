"""
헌장(Trading Charter) 규칙을 코드 상수로 고정한 단일 기준(SSOT).

이 파일의 값은 docs/TRADING_CHARTER_KR.md 의 §13 파라미터 표와 1:1로 대응한다.
코드 어디서든 매매 파라미터가 필요하면 반드시 여기서 import 한다.
값을 바꾸려면 먼저 헌장을 개정(버전업)한 뒤 이 파일에 반영한다. (헌장 §14)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

CHARTER_VERSION = "v1.0"

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


class Regime(str, Enum):
    TREND = "trend"      # 추세장 → MACD
    RANGE = "range"      # 횡보장 → RSI 평균회귀
    REVERSAL = "reversal"  # 반전 → CVD 보조


@dataclass(frozen=True)
class StrategySpec:
    """전략별 진입/청산 파라미터 (헌장 §2, §4)."""
    name: str
    stop_loss: float      # 손절 % (양수로 표기, 진입가 대비 하락폭)
    take_profit: float    # 익절 %
    regime: Regime        # 이 전략이 유리한 레짐 (레짐 필터에서 사용)

    @property
    def risk_reward(self) -> float:
        return self.take_profit / self.stop_loss


# 전략 스펙 — 헌장 §4 표와 1:1 대응 (출발 기본값, 백테스트로 조정)
STRATEGY_SPECS: dict[str, StrategySpec] = {
    "macd": StrategySpec("macd", stop_loss=0.03, take_profit=0.06, regime=Regime.TREND),
    "rsi":  StrategySpec("rsi",  stop_loss=0.025, take_profit=0.04, regime=Regime.RANGE),
    "cvd":  StrategySpec("cvd",  stop_loss=0.03, take_profit=0.05, regime=Regime.REVERSAL),
}

# ── 파생 계산 헬퍼 (모두 '현재 자본(capital)'을 인자로 받는다) ──────────────
# capital = 실계좌 총 자산(원화 + 보유코인 평가액). 입금하면 자동으로 커지고,
# 출금/손실이면 작아진다 → 모든 리스크 한도·포지션 크기가 잔고에 비례해 스케일된다.

def max_loss_per_trade_krw(capital: float) -> float:
    """1거래 최대 손실 금액 (§5.1). capital × 1%. 예: 90,000 → 900원."""
    return capital * RISK_PER_TRADE_RATIO


def daily_loss_limit_krw(capital: float) -> float:
    """일일 손실 한도 금액 (§5.2). capital × 3%. 예: 90,000 → 2,700원."""
    return capital * DAILY_LOSS_LIMIT_RATIO


def position_size_krw(stop_loss_pct: float, capital: float,
                      available_krw: float | None = None) -> float:
    """
    리스크 상한 기반 포지션 크기 (§7.2).
        포지션 크기 = 1거래최대손실 ÷ 손절폭
    예(capital=90,000): 900 / 0.03 = 30,000원. 손절 -6%면 15,000원.
    - 전략당 배분 상한(capital × 1/3)을 넘지 않게 clamp.
    - 주문가능 원화(available_krw)가 주어지면 그 이하로 clamp (잔고 초과 주문 방지).
    반환값이 MIN_ORDER_KRW 미만이면 호출측이 '진입 불가'로 처리한다(강제로 올리지 않음).
    """
    raw = max_loss_per_trade_krw(capital) / stop_loss_pct
    alloc_cap = capital * ALLOC_PER_STRATEGY_RATIO
    size = min(raw, alloc_cap)
    if available_krw is not None:
        size = min(size, available_krw)
    return max(0.0, size)

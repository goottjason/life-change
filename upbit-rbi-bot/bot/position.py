"""
포지션 모델 + 가격 기반 청산 판정 (헌장 §4).
청산 우선순위: 익절 → 손절 → 역방향 신호 → 죽은 포지션.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from config.charter import STRATEGY_SPECS, FEE_ROUNDTRIP


class ExitReason(str, Enum):
    TAKE_PROFIT = "take_profit"     # §4.1
    STOP_LOSS = "stop_loss"         # §4.2
    REVERSE_SIGNAL = "reverse"      # §4.3
    DEAD_POSITION = "dead"          # §4.4 / §4-A
    KILL_SWITCH = "kill_switch"     # §9.1
    NONE = "none"


@dataclass
class Position:
    strategy: str
    market: str                 # 예: "KRW-BTC"
    entry_price: float
    size_krw: float             # 진입 원금(원)
    volume: float               # 코인 수량
    entry_time: object          # 진입 캔들 타임스탬프 (시간 손절 계산용 §4-A) — pd.Timestamp
    entry_atr: float = 0.0      # 진입 시 ATR (변동성 축소 판정용 §4-A)
    highest_price: float = field(init=False)
    sl_ratio: float = field(init=False)   # 진입 시 ATR로 계산한 손절거리비율
    tp_ratio: float = field(init=False)   # 익절거리비율

    def __post_init__(self):
        from config.charter import FALLBACK_STOP_RATIO
        self.highest_price = self.entry_price
        if self.entry_price > 0 and self.entry_atr > 0:
            stop = self.spec.atr_stop_mult * self.entry_atr / self.entry_price
        else:
            stop = FALLBACK_STOP_RATIO
        self.sl_ratio = stop
        self.tp_ratio = self.spec.rr * stop

    @property
    def spec(self):
        return STRATEGY_SPECS[self.strategy]

    def pnl_ratio(self, price: float) -> float:
        """수수료 반영 손익률 (§4, §0.4)."""
        gross = (price - self.entry_price) / self.entry_price
        return gross - FEE_ROUNDTRIP

    def pnl_krw(self, price: float) -> float:
        return self.size_krw * self.pnl_ratio(price)

    def update_high(self, price: float) -> None:
        self.highest_price = max(self.highest_price, price)

    def check_price_exit(self, price: float) -> ExitReason:
        """가격 기반 청산 판정 (§4.1, §4.2). 진입 시 ATR로 정한 sl/tp 비율(gross) 기준."""
        gross = (price - self.entry_price) / self.entry_price
        if gross >= self.tp_ratio:
            return ExitReason.TAKE_PROFIT
        if gross <= -self.sl_ratio:
            return ExitReason.STOP_LOSS
        return ExitReason.NONE

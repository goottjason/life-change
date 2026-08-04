"""
포지션 모델 + 가격 기반 청산 판정 (헌장 §4).
청산 우선순위: 손절 → 익절(부분/전량) → 역방향 신호 → 죽은 포지션.

v2.7 — 구조 기반 청산(반익반본)을 추가했다. 전략이 Signal 로 손절/목표 '가격'을 직접
넘기면 그 값을 쓰고, 넘기지 않으면 기존 ATR 비율 청산 그대로다(rsi2 계열은 불변).
근거: easy_teaching 실매매 15건 중 익절 도달 0건 — 자리와 무관한 고정 배수 익절선이
15분봉 실측 MFE(평균 +0.44%)로는 닿을 수 없는 곳에 있었다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from config.charter import STRATEGY_SPECS, FEE_ROUNDTRIP, MIN_ORDER_KRW


class ExitReason(str, Enum):
    TAKE_PROFIT = "take_profit"     # §4.1
    PARTIAL_TAKE_PROFIT = "partial_take_profit"   # §4.1-A 반익절 (v2.7)
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
    # ── v2.7: 구조 레벨. 전략이 넘기지 않으면 None → 기존 비율 청산 ──
    stop_price: float | None = None    # 손절 '가격' (예: 오더블록 생성 캔들의 저점)
    target_price: float | None = None  # 1차 목표 '가격' (예: 직전 스윙 고점)
    half_taken: bool = False           # 반익절 완료 여부
    # ── v2.7: 청산 실패 백오프 (§6.7). 팔 수 없는 주문을 매 tick 재시도하지 않기 위한 상태 ──
    exit_failures: int = 0             # 연속 청산 실패 횟수
    retry_after: float = 0.0           # 이 시각(time.monotonic) 전에는 재시도하지 않는다
    alerted: bool = False              # '수동 확인 필요' 경보를 이미 보냈는가
    highest_price: float = field(init=False)
    sl_ratio: float = field(init=False)   # 진입 시 ATR로 계산한 손절거리비율
    tp_ratio: float = field(init=False)   # 익절거리비율

    def __post_init__(self):
        from config.charter import stop_ratio_for
        self.highest_price = self.entry_price
        # 전략 스펙에 고정 손절(stop_pct)이 있으면 그 값, 없으면 ATR 정규화 (§7, v1.3)
        stop = stop_ratio_for(self.spec, self.entry_atr, self.entry_price)
        self.sl_ratio = stop
        self.tp_ratio = self.spec.rr * stop

    # ── 구조 레벨 (v2.7) ─────────────────────────────────────
    @property
    def effective_stop_price(self) -> float | None:
        """
        지금 유효한 손절 '가격'. 구조 레벨이 없으면 None(→ 비율 청산이 담당).
        반익절 후에는 진입가로 올라온다 = 원문의 '본절 스탑'.
        """
        if self.stop_price is None:
            return None
        return self.entry_price if self.half_taken else self.stop_price

    @property
    def sizing_stop_ratio(self) -> float:
        """
        사이징에 쓸 손절거리비율. 청산 트리거는 구조 그대로 쓰되(가까우면 가까운 대로),
        사이징은 MIN_STOP_RATIO 하한을 적용한다 — 손절거리가 아주 가까울 때 포지션이
        과대해지는 것을 막기 위해서다(리스크는 목표치보다 작아질 뿐 커지지 않는다).
        """
        from config.charter import MIN_STOP_RATIO
        if self.stop_price is None or self.entry_price <= 0:
            return self.sl_ratio
        return max((self.entry_price - self.stop_price) / self.entry_price, MIN_STOP_RATIO)

    def partial_exit_volume(self) -> float:
        """반익절로 팔 수량. 미사용 전략이거나 이미 반익했으면 0."""
        ratio = self.spec.partial_tp_ratio
        if ratio <= 0 or self.half_taken:
            return 0.0
        return self.volume * ratio

    def _partial_is_tradable(self, price: float) -> bool:
        """
        반익절이 실제로 가능한가. 파는 쪽도 남는 쪽도 업비트 최소주문금액(5,000원)을
        넘어야 한다. 넘지 못하면 쪼개지 말고 전량 익절해야 한다 — 실매매에서 0.5 AVAX
        먼지가 최소금액 미만이라 영구 청산 불가로 남은 사고가 실제로 있었다.
        """
        sell = self.partial_exit_volume()
        if sell <= 0:
            return False
        return min(sell, self.volume - sell) * price >= MIN_ORDER_KRW

    def take_partial(self, fill_price: float) -> None:
        """반익절 반영: 잔량·원금을 줄이고 손절을 본절로 올린다. 2차 목표는 두지 않는다."""
        sell = self.partial_exit_volume()
        self.volume -= sell
        self.size_krw *= (1 - self.spec.partial_tp_ratio)
        self.half_taken = True
        self.target_price = None      # 나머지는 추세를 태운다 (시간손절·역신호가 정리)

    def partial_pnl_krw(self, fill_price: float) -> float:
        """반익절로 실현되는 손익(원). 판 수량 몫만 잡는다."""
        return self.size_krw * self.spec.partial_tp_ratio * self.pnl_ratio(fill_price)

    @property
    def spec(self):
        return STRATEGY_SPECS[self.strategy]

    @property
    def key(self) -> str:
        """포지션 식별자 (v1.4). 한 전략이 서로 다른 코인에 동시 진입할 수 있어
        '전략명'만으로는 구분되지 않는다 → '전략:코인'."""
        return f"{self.strategy}:{self.market}"

    def pnl_ratio(self, price: float) -> float:
        """수수료 반영 손익률 (§4, §0.4)."""
        gross = (price - self.entry_price) / self.entry_price
        return gross - FEE_ROUNDTRIP

    def pnl_krw(self, price: float) -> float:
        return self.size_krw * self.pnl_ratio(price)

    def update_high(self, price: float) -> None:
        self.highest_price = max(self.highest_price, price)

    def check_price_exit(self, price: float) -> ExitReason:
        """
        가격 기반 청산 판정 (§4.1, §4.2).

        구조 레벨(stop_price/target_price)이 있으면 그것이 우선한다 — 손절은 근거가
        깨지는 지점에서, 익절은 직전 고점에서 절반. 없으면 기존 ATR 비율 청산 그대로다.
        손절을 먼저 본다: 같은 tick 에 둘 다 걸리면 살아남는 쪽이 우선이다.
        """
        stop = self.effective_stop_price
        if stop is not None:
            # 강의(원저자, 2025.09): "손절은 복마감(봉마감)을 보고 한다."
            # 라이브에서 price 는 해당 전략 타임프레임의 마지막 종가이므로 이 비교가 곧
            # 종가 기준 판정이다. 백테스트도 같은 기준을 쓴다(spec.stop_on_close).
            if price <= stop:
                return ExitReason.STOP_LOSS
            if self.target_price is not None and price >= self.target_price:
                # 반익절 — 쪼갤 수 없는 크기면 전량 익절한다
                return (ExitReason.PARTIAL_TAKE_PROFIT if self._partial_is_tradable(price)
                        else ExitReason.TAKE_PROFIT)
            return ExitReason.NONE

        gross = (price - self.entry_price) / self.entry_price
        if gross <= -self.sl_ratio:
            return ExitReason.STOP_LOSS
        if gross >= self.tp_ratio:
            return ExitReason.TAKE_PROFIT
        return ExitReason.NONE

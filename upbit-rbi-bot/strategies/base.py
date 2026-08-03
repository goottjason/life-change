"""
전략 추상 클래스 + 신호 모델.
모든 전략은 캔들 DataFrame을 받아 Signal 을 반환한다 (백테스트/실시간 공통).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from config.charter import StrategySpec, Regime


class Action(str, Enum):
    ENTER_LONG = "enter_long"
    EXIT = "exit"          # 역방향 신호 청산 (헌장 §4.3)
    HOLD = "hold"


@dataclass
class Signal:
    action: Action
    strategy: str
    reason: str = ""       # 로깅용 (헌장 §10.1)
    meta: dict = field(default_factory=dict)
    """관찰용 수치 (v1.5). 대시보드에서 '왜 진입하지 않는가'를 보여주기 위한 것으로,
    매매 판단에는 쓰지 않는다. rsi2 는 {"rsi2","atr_pct","trend_up","gate"} 를 채운다."""
    # ── v2.7: 구조 기반 청산 레벨 ──
    # meta 와 달리 이 둘은 **매매 판단에 쓰인다**. 전략이 '근거가 깨지는 지점'과 '1차 목표'를
    # 자리마다 다르게 정할 수 있어야 원문의 손익비가 성립하기 때문이다(easy_teaching).
    # None 이면 트레이더가 기존 ATR 비율 청산을 쓴다 → rsi2 계열은 동작이 바뀌지 않는다.
    stop_price: float | None = None      # 손절 가격 (예: 오더블록 생성 캔들의 저점)
    target_price: float | None = None    # 1차 목표 가격 (예: 직전 스윙 고점)


class BaseStrategy(ABC):
    """
    업비트 현물 롱 전용 (헌장 §1: 숏 폐기).
    spec 에서 SL/TP/레짐을 가져오므로 전략 클래스는 '신호 판정'만 책임진다.
    """
    def __init__(self, spec: StrategySpec):
        self.spec = spec

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def regime(self) -> Regime:
        return self.spec.regime

    @abstractmethod
    def signal(self, df: pd.DataFrame, ctx: dict | None = None) -> Signal:
        """
        최신 봉 기준 진입/청산/보류 신호. df는 시간순 정렬된 OHLCV(기준봉 = 5분).

        ctx: 기준봉만으로 계산할 수 없는 재료를 넘기는 통로 (v1.3).
          - "trend_up": bool — 상위 타임프레임 추세(1시간봉 EMA200 위). rsi2 전략이 사용.
          라이브는 매 tick 200봉만 받으므로 1시간봉 추세는 별도 조회해 여기로 전달한다.
          ctx가 없으면 전략이 df만으로 계산 가능한 경우에만 신호를 낸다.
        """
        raise NotImplementedError

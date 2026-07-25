"""
전략 추상 클래스 + 신호 모델.
모든 전략은 캔들 DataFrame을 받아 Signal 을 반환한다 (백테스트/실시간 공통).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
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
    def signal(self, df: pd.DataFrame) -> Signal:
        """최신 봉 기준 진입/청산/보류 신호. df는 시간순 정렬된 OHLCV."""
        raise NotImplementedError

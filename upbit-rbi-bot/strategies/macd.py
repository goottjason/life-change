"""MACD 히스토그램 전략 (헌장 §2, 추세추종)."""
from __future__ import annotations

import pandas as pd

from indicators import ta
from strategies.base import BaseStrategy, Signal, Action


class MacdStrategy(BaseStrategy):
    FAST, SLOW, SIGNAL = 3, 15, 3

    def signal(self, df: pd.DataFrame, ctx: dict | None = None) -> Signal:
        if len(df) < self.SLOW + 2:
            return Signal(Action.HOLD, self.name, "insufficient bars")
        m = ta.macd(df["close"], self.FAST, self.SLOW, self.SIGNAL)

        # 진입: MACD선이 시그널선 상향 돌파 (§2)
        if ta.crossed_up(m["macd"], m["signal"]):
            return Signal(Action.ENTER_LONG, self.name, "macd cross up")

        # 역방향 신호 청산 (§4.3, 선택)
        if ta.crossed_down(m["macd"], m["signal"]):
            return Signal(Action.EXIT, self.name, "macd cross down")

        return Signal(Action.HOLD, self.name)

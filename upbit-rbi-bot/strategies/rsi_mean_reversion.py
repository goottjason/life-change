"""RSI 평균회귀 전략 (헌장 §2, 되돌림)."""
from __future__ import annotations

import pandas as pd

from indicators import ta
from strategies.base import BaseStrategy, Signal, Action


class RsiMeanReversionStrategy(BaseStrategy):
    PERIOD = 14
    OVERSOLD = 30
    EXIT_LEVEL = 50

    def signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < self.PERIOD + 2:
            return Signal(Action.HOLD, self.name, "insufficient bars")
        r = ta.rsi(df["close"], self.PERIOD)
        last = r.iloc[-1]

        # 진입: RSI < 30 과매도 (§2)
        if last < self.OVERSOLD:
            return Signal(Action.ENTER_LONG, self.name, f"rsi {last:.1f} < {self.OVERSOLD}")

        # 청산 조건: RSI > 50 또는 가격이 VWAP 도달 (§2)
        vwap = ta.vwap(df).iloc[-1]
        if last > self.EXIT_LEVEL or df["close"].iloc[-1] >= vwap:
            return Signal(Action.EXIT, self.name, f"rsi {last:.1f} recovered / vwap reached")

        return Signal(Action.HOLD, self.name)

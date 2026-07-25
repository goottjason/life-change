"""CVD(누적 거래량 델타) 전략 (헌장 §2, 반전 포착)."""
from __future__ import annotations

import pandas as pd

from indicators import ta
from strategies.base import BaseStrategy, Signal, Action


class CvdStrategy(BaseStrategy):
    LOOKBACK = 5  # 다이버전스 확인 구간(봉)

    def signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < self.LOOKBACK + 2:
            return Signal(Action.HOLD, self.name, "insufficient bars")
        c = ta.cvd(df)
        price = df["close"]

        price_change = price.iloc[-1] - price.iloc[-1 - self.LOOKBACK]
        cvd_change = c.iloc[-1] - c.iloc[-1 - self.LOOKBACK]

        # 강세 다이버전스: 가격 하락 + CVD 상승 → 숨은 매수세 → 롱 (§2)
        if price_change < 0 and cvd_change > 0:
            return Signal(Action.ENTER_LONG, self.name, "bullish cvd divergence")

        # 반대(약세 다이버전스): 가격 상승 + CVD 하락 → 청산 (§4.3)
        if price_change > 0 and cvd_change < 0:
            return Signal(Action.EXIT, self.name, "bearish cvd divergence")

        return Signal(Action.HOLD, self.name)

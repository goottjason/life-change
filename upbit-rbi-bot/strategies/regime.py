"""
레짐 필터 (헌장 §8): 현재 장세를 판별해 유리한 전략만 활성화한다.
"항상 3전략 on" 금지.
"""
from __future__ import annotations

import pandas as pd

from config.charter import Regime, ADX_TREND_THRESHOLD
from indicators import ta


def detect_regime(df: pd.DataFrame) -> Regime:
    """
    출발 로직(단순): ADX로 추세/횡보 판별. (§8.3: 임계값은 백테스트로 확정)
    - ADX >= 25 → 추세장(TREND)
    - ADX <  25 → 횡보장(RANGE)
    반전(REVERSAL)은 별도 신호성이라 CVD가 보조로 항상 후보. 여기선 추세강도만 반환.
    """
    if len(df) < 30:
        return Regime.RANGE
    adx_val = ta.adx(df).iloc[-1]
    if pd.isna(adx_val):
        return Regime.RANGE
    return Regime.TREND if adx_val >= ADX_TREND_THRESHOLD else Regime.RANGE


def is_strategy_active(strategy_regime: Regime, current: Regime) -> bool:
    """
    전략 활성화 판정 (§8.2).
    - MACD(TREND) → 추세장에서만
    - RSI(RANGE)  → 횡보장에서만
    - CVD(REVERSAL) → 보조로 항상 후보 (반전은 레짐과 독립적으로 발생)
    """
    if strategy_regime == Regime.REVERSAL:
        return True
    return strategy_regime == current

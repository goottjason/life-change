"""
백테스트 엔진 (헌장 §11). 수수료(왕복 0.1%) 반영 필수 (§0.4, §12).
전략 신호 + 헌장의 ATR 기반 SL/TP(§7, v1.2)·시간손절을 그대로 적용해 실전과 동일 규칙으로 검증한다.
"""
from __future__ import annotations

import pandas as pd

from config.charter import FEE_ROUNDTRIP, stop_ratio_for, time_stop_bars_for
from strategies.base import BaseStrategy, Action
from backtesting.metrics import compute, BacktestResult
from indicators import ta


def run(strategy: BaseStrategy, df: pd.DataFrame, warmup: int = 30) -> BacktestResult:
    """
    단순 이벤트 루프 백테스트.
    - 진입: 전략 ENTER_LONG 신호. 진입 시점 ATR로 SL/TP 비율을 고정한다 (실전 Position과 동일, §7 v1.2).
    - 청산: TP / SL / 시간손절 / 역방향 신호 (실전 §4와 동일 우선순위)
    - 손익: 수수료 왕복 0.1% 차감
    """
    spec = strategy.spec
    pnls: list[float] = []
    in_pos = False
    entry_price = 0.0
    entry_i = 0
    sl_ratio = 0.0
    tp_ratio = 0.0

    for i in range(warmup, len(df)):
        window = df.iloc[: i + 1]
        price = window["close"].iloc[-1]

        if not in_pos:
            sig = strategy.signal(window)
            if sig.action == Action.ENTER_LONG:
                entry_atr = float(ta.atr(window).iloc[-1]) if len(window) >= 14 else 0.0
                sl_ratio = stop_ratio_for(spec, entry_atr, price)
                tp_ratio = spec.rr * sl_ratio
                in_pos, entry_price, entry_i = True, price, i
            continue

        # 보유 중 — 청산 판정 (§4 우선순위, 진입 시 고정된 ATR 비율 기준)
        gross = (price - entry_price) / entry_price
        exit_now = False
        if gross >= tp_ratio:                  # 익절
            exit_now = True
        elif gross <= -sl_ratio:               # 손절
            exit_now = True
        elif (i - entry_i) >= time_stop_bars_for(spec):  # 시간 손절 (전략별, v1.3)
            exit_now = True
        else:
            sig = strategy.signal(window)
            if sig.action == Action.EXIT:      # 역방향 신호
                exit_now = True

        if exit_now:
            pnls.append(gross - FEE_ROUNDTRIP)  # 수수료 반영
            in_pos = False

    return compute(pnls)

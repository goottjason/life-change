"""
죽은 포지션(Dead Position) 판정 (헌장 §4-A).
아래 기준 중 하나라도(OR) 충족 시 청산 → 본전 ±α에서 자본 회전.
'당일 청산' 대원칙을 대체하는 시간 손절 로직 (§11에서 폐기된 규칙의 대체).
"""
from __future__ import annotations

import pandas as pd

from config.charter import (
    TIME_STOP_BARS, FLAT_BARS, FLAT_BAND_RATIO, ATR_SHRINK_RATIO,
)
from indicators import ta
from bot.position import Position


def is_dead(pos: Position, df: pd.DataFrame) -> tuple[bool, str]:
    """
    반환: (죽음 여부, 사유). df 는 진입 이후를 포함한 최신 캔들.
    bars_held 는 '틱 수'가 아니라 진입 캔들 이후 '경과한 캔들 수'로 계산한다
    (실거래에서 시간 손절이 봉 기준으로 정확히 동작하도록).
    """
    bars_held = int((df.index > pos.entry_time).sum())

    # ① 시간 손절: N봉 경과 & TP·SL 미도달 (메인 기준)
    if bars_held >= TIME_STOP_BARS:
        return True, f"time_stop {bars_held}>={TIME_STOP_BARS} bars"

    # ② 횡보/무변동: 최근 FLAT_BARS 동안 진입가 ±FLAT_BAND 이탈 실패
    if bars_held >= FLAT_BARS and len(df) >= FLAT_BARS:
        window = df["close"].iloc[-FLAT_BARS:]
        band = pos.entry_price * FLAT_BAND_RATIO
        if (window.max() - pos.entry_price) < band and (pos.entry_price - window.min()) < band:
            return True, f"flat within ±{FLAT_BAND_RATIO:.1%} for {FLAT_BARS} bars"

    # ③ 신호 소멸: MACD 히스토그램 0 근접 or RSI 중립(45~55) 복귀
    if _signal_neutralized(pos.strategy, df):
        return True, "signal neutralized"

    # ④ 변동성 축소: ATR 이 진입 시 대비 ATR_SHRINK_RATIO 이하 (백테스트 후 채택)
    if pos.entry_atr > 0 and len(df) >= 14:
        cur_atr = ta.atr(df).iloc[-1]
        if pd.notna(cur_atr) and cur_atr <= pos.entry_atr * ATR_SHRINK_RATIO:
            return True, f"atr shrank to {cur_atr:.4f} <= {ATR_SHRINK_RATIO:.0%} of entry"

    return False, ""


def _signal_neutralized(strategy: str, df: pd.DataFrame) -> bool:
    if strategy == "macd":
        hist = ta.macd(df["close"]).iloc[-1]["hist"]
        # 히스토그램 크기가 최근 변동 대비 매우 작으면 중립
        recent = ta.macd(df["close"])["hist"].abs().tail(20).mean()
        return recent > 0 and abs(hist) < recent * 0.1
    if strategy == "rsi":
        r = ta.rsi(df["close"]).iloc[-1]
        return 45 <= r <= 55
    return False

"""
기술적 지표 계산 (순수 함수, pandas 기반).
전략과 레짐 필터가 공유한다. 외부 상태 없음 → 백테스트/실시간 동일 코드 사용.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def macd(close: pd.Series, fast: int = 3, slow: int = 15, signal: int = 3
         ) -> pd.DataFrame:
    """MACD (헌장 §2, 3/15/3). macd/signal/hist 반환."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI (Wilder). 0~100."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def vwap(df: pd.DataFrame) -> pd.Series:
    """당일 VWAP. df: high/low/close/volume 필요 (RSI 청산 조건 §2)."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum()
    return (typical * df["volume"]).cumsum() / cum_vol


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR — 변동성. 죽은포지션 판정(§4-A)과 사이징 참고에 사용."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def cvd(df: pd.DataFrame) -> pd.Series:
    """
    누적 거래량 델타 (헌장 §2, CVD).
    캔들 단위 근사: 종가>시가면 매수우위(+vol), 종가<시가면 매도우위(-vol) 누적.
    (틱 단위 매수/매도 체결 데이터가 있으면 그것으로 대체하면 더 정확)
    """
    direction = (df["close"] > df["open"]).astype(int) - (df["close"] < df["open"]).astype(int)
    return (direction * df["volume"]).cumsum()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ADX — 추세 강도. 레짐 필터(§8)에서 추세/횡보 판별."""
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up
    minus_dm = ((down > up) & (down > 0)) * down
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.astype(float).ewm(alpha=1 / period, adjust=False).mean()


def crossed_up(fast: pd.Series, slow: pd.Series) -> bool:
    """직전 봉에서 fast가 slow를 상향 돌파했는지 (마지막 2봉 기준)."""
    if len(fast) < 2 or len(slow) < 2:
        return False
    return fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]


def crossed_down(fast: pd.Series, slow: pd.Series) -> bool:
    if len(fast) < 2 or len(slow) < 2:
        return False
    return fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1]

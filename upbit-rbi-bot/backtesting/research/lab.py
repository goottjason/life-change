"""
5분봉 단타 후보 신호 라이브러리 (조사 기반).

기존 3전략(macd/rsi/cvd)이 전부 음의 기댓값이었으므로, 문헌·실무에서 근거가 보고된
단기 효과들을 후보로 구현해 같은 체결 모델(수수료 0.1%, intrabar 손절우선)로 검증한다.

넘어야 하는 문턱: **거래당 총엣지 > 왕복 수수료 0.1%**.
 승률만 높아도 익절폭이 작으면 음수다. 예) 승률 60%·TP=SL=0.3% → 0.6×0.3 − 0.4×0.3 − 0.1 = −0.04%.
 따라서 모든 후보에 **변동성 게이트**(ATR/가격 ≥ g)를 걸 수 있게 했다. 움직임이 수수료보다
 충분히 클 때만 진입하는 것이 5분봉 단타의 필요조건이다.

후보 근거
 - zdip / vwapdev : 단기 반전(short-term reversal) — 알트에서 유의, 유동성이 조절변수
   (Up or down? Short-term reversal…, ScienceDirect S1057521921002349)
 - xrev          : 횡단면 단기 반전 — 유니버스 내 최근 수익률 최하위 매수(같은 문헌)
 - panic         : 급락+거래량 급증+아래꼬리 = 강제청산 후 반등(유동성 프리미엄)
 - btclead       : BTC 리드-랙 — 대형코인 모멘텀이 알트로 전이 (Intraday return predictability…, SSRN 4080253)
 - donch/squeeze : 일중 모멘텀(변동성 확장 국면) — 같은 문헌의 momentum 쪽
 - rsi2          : Connors RSI(2) 극단 + 상위 타임프레임 추세 필터(추세 순응 되돌림)
 - openrange     : 일중 개장 레인지 돌파 + 시간대 효과 (Bitcoin 시간대 계절성, Quantpedia)
 - hour 게이트   : 5분 변동성/수익률의 시간대 규칙성 (동일)

모든 신호는 **인과적**(현재 봉까지의 정보만)이며 진입은 신호 봉 종가로 체결된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from indicators import ta
from backtesting.research import data_cache

EPS = 1e-12


# ── 패널 로드 (모든 종목을 같은 시각축에 정렬) ──────────────────────────
def load_panel(interval: str = "minute5",
               markets: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """종목별 캔들을 공통 시각축으로 정렬해 반환. 횡단면 신호를 위해 위치 정렬이 필요하다."""
    markets = markets or data_cache.MARKETS_LAB
    raw = {}
    for m in markets:
        df = data_cache.load(m, interval)
        if df is not None and len(df) > 5000:
            raw[m] = df
    if not raw:
        return {}
    common = None
    for df in raw.values():
        common = df.index if common is None else common.intersection(df.index)
    return {m: df.reindex(common).ffill() for m, df in raw.items()}


@dataclass
class Ctx:
    """패널 전체에서 한 번만 계산하는 공통 재료 (횡단면 랭크, BTC 수익률, 시간대)."""
    index: pd.DatetimeIndex
    btc_close: pd.Series
    xrank: dict[int, pd.DataFrame] = field(default_factory=dict)   # k봉 수익률의 횡단면 백분위
    hour: np.ndarray = field(default_factory=lambda: np.array([]))

    @classmethod
    def build(cls, panel: dict[str, pd.DataFrame], xrank_ks=(6, 12)) -> "Ctx":
        idx = next(iter(panel.values())).index
        closes = pd.DataFrame({m: df["close"] for m, df in panel.items()})
        xrank = {}
        for k in xrank_ks:
            ret = closes.pct_change(k)
            xrank[k] = ret.rank(axis=1, pct=True)      # 0=최하위(가장 많이 하락)
        return cls(index=idx, btc_close=panel["KRW-BTC"]["close"],
                   xrank=xrank, hour=idx.hour.to_numpy())


# ── 공통 필터 ────────────────────────────────────────────────────────
def atr_pct(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR/가격 — '이 종목이 지금 수수료를 넘길 만큼 움직이는가'의 척도."""
    return ta.atr(df, period) / df["close"]


def vol_spike(df: pd.DataFrame, window: int = 96) -> pd.Series:
    return df["volume"] / (df["volume"].rolling(window).mean() + EPS)


def _arr(s: pd.Series) -> np.ndarray:
    return s.fillna(False).to_numpy(dtype=bool)


# ── 후보 신호들 ──────────────────────────────────────────────────────
# 각 함수: (df, ctx, market, **params) -> (enter, exit_sig) 불리언 배열

def sig_zdip(df, ctx, market, n=48, z_in=2.5, z_out=0.0, gate=0.004):
    """볼린저/z-score 하단 이탈 후 평균회귀. 하락 과열 → 평균 복귀 목표."""
    ma = df["close"].rolling(n).mean()
    sd = df["close"].rolling(n).std()
    z = (df["close"] - ma) / (sd + EPS)
    enter = (z <= -z_in) & (atr_pct(df) >= gate)
    return _arr(enter), _arr(z >= z_out)


def sig_panic(df, ctx, market, n=6, drop=0.02, vm=3.0, wick=0.4, gate=0.0):
    """급락 + 거래량 급증 + 긴 아래꼬리 = 투매 소진 후 반등."""
    ret_n = df["close"] / df["close"].shift(n) - 1
    rng = (df["high"] - df["low"]).abs() + EPS
    lower_wick = (df["close"] - df["low"]) / rng
    enter = (ret_n <= -drop) & (vol_spike(df) >= vm) & (lower_wick >= wick)
    if gate:
        enter = enter & (atr_pct(df) >= gate)
    return _arr(enter), np.zeros(len(df), dtype=bool)


def sig_xrev(df, ctx, market, k=6, q=0.15, gate=0.004):
    """횡단면 단기 반전: 최근 k봉 수익률이 유니버스 최하위 q분위 → 매수."""
    rank = ctx.xrank[k][market]
    enter = (rank <= q) & (atr_pct(df) >= gate)
    return _arr(enter), np.zeros(len(df), dtype=bool)


def sig_btclead(df, ctx, market, k=2, btc_up=0.004, lag=0.5, gate=0.0):
    """BTC 리드-랙: BTC가 먼저 급등했고 이 종목은 아직 덜 올랐을 때 추종 매수."""
    btc_ret = ctx.btc_close / ctx.btc_close.shift(k) - 1
    own_ret = df["close"] / df["close"].shift(k) - 1
    enter = (btc_ret >= btc_up) & (own_ret <= btc_ret * lag)
    if gate:
        enter = enter & (atr_pct(df) >= gate)
    return _arr(enter), np.zeros(len(df), dtype=bool)


def sig_donch(df, ctx, market, n=48, vm=2.0, gate=0.004):
    """돌파 모멘텀: n봉 고점 돌파 + 거래량 확인. 청산신호는 n봉 저점 이탈."""
    hh = df["high"].rolling(n).max().shift(1)
    ll = df["low"].rolling(n).min().shift(1)
    enter = (df["close"] > hh) & (vol_spike(df) >= vm) & (atr_pct(df) >= gate)
    return _arr(enter), _arr(df["close"] < ll)


def sig_squeeze(df, ctx, market, n=24, pct=0.25, look=288):
    """변동성 압축(밴드폭 하위 분위) 후 상방 확장 돌파."""
    ma = df["close"].rolling(n).mean()
    sd = df["close"].rolling(n).std()
    bw = (2 * sd) / (ma + EPS)
    thresh = bw.rolling(look).quantile(pct)
    hh = df["high"].rolling(n).max().shift(1)
    enter = (bw.shift(1) <= thresh.shift(1)) & (df["close"] > hh)
    return _arr(enter), np.zeros(len(df), dtype=bool)


def sig_rsi2(df, ctx, market, th=5.0, trend_bars=2400, exit_level=70.0, gate=0.003):
    """Connors RSI(2) 극단 되돌림 + 상위 추세 필터(5분봉 2400봉 EMA ≈ 1시간봉 EMA200)."""
    r2 = ta.rsi(df["close"], 2)
    trend = df["close"] > ta.ema(df["close"], trend_bars)
    enter = (r2 <= th) & trend & (atr_pct(df) >= gate)
    return _arr(enter), _arr(r2 >= exit_level)


def sig_vwapdev(df, ctx, market, window=288, k=2.0, gate=0.004):
    """일중 VWAP 하단 이탈 → VWAP 회귀. window=288봉 ≈ 24시간."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    num = (typical * df["volume"]).rolling(window).sum()
    den = df["volume"].rolling(window).sum() + EPS
    vwap = num / den
    dev = (df["close"] - vwap) / (vwap + EPS)
    sd = dev.rolling(window).std()
    enter = (dev <= -k * sd) & (atr_pct(df) >= gate)
    return _arr(enter), _arr(df["close"] >= vwap)


def sig_openrange(df, ctx, market, open_bars=12, gate=0.0):
    """일중 개장(KST 00:00) 후 첫 open_bars봉 고가 돌파 — 하루 최초 신호만."""
    day = pd.Series(df.index.date, index=df.index)
    bar_of_day = df.groupby(day).cumcount()
    or_high = df["high"].where(bar_of_day < open_bars).groupby(day).cummax().ffill()
    cond = (bar_of_day >= open_bars) & (df["close"] > or_high)
    first = cond & ~cond.groupby(day).cumsum().shift(1).fillna(0).astype(bool)
    if gate:
        first = first & (atr_pct(df) >= gate)
    return _arr(first), np.zeros(len(df), dtype=bool)


@dataclass
class Candidate:
    name: str
    fn: Callable
    has_exit: bool                 # 신호 기반 청산이 의미 있는지(평균회귀 계열은 True)
    params: list[dict]
    note: str = ""


CANDIDATES: list[Candidate] = [
    Candidate("zdip", sig_zdip, True, [
        dict(n=48, z_in=2.0, gate=0.003), dict(n=48, z_in=2.5, gate=0.004),
        dict(n=96, z_in=2.5, gate=0.004), dict(n=24, z_in=3.0, gate=0.005),
    ], "단기 반전(볼린저 하단)"),
    Candidate("panic", sig_panic, False, [
        dict(n=6, drop=0.015, vm=3.0, wick=0.4), dict(n=6, drop=0.025, vm=4.0, wick=0.5),
        dict(n=12, drop=0.03, vm=3.0, wick=0.3), dict(n=3, drop=0.012, vm=5.0, wick=0.5),
    ], "투매 소진 반등"),
    Candidate("xrev", sig_xrev, False, [
        dict(k=6, q=0.1, gate=0.004), dict(k=6, q=0.2, gate=0.004),
        dict(k=12, q=0.1, gate=0.004), dict(k=12, q=0.1, gate=0.006),
    ], "횡단면 단기 반전"),
    Candidate("btclead", sig_btclead, False, [
        dict(k=2, btc_up=0.003, lag=0.5), dict(k=2, btc_up=0.005, lag=0.4),
        dict(k=4, btc_up=0.006, lag=0.5), dict(k=6, btc_up=0.008, lag=0.6),
    ], "BTC 리드-랙 추종"),
    Candidate("donch", sig_donch, True, [
        dict(n=24, vm=2.0, gate=0.003), dict(n=48, vm=2.0, gate=0.004),
        dict(n=96, vm=2.5, gate=0.004), dict(n=288, vm=3.0, gate=0.004),
    ], "돌파 모멘텀"),
    Candidate("squeeze", sig_squeeze, False, [
        dict(n=24, pct=0.25), dict(n=24, pct=0.1),
        dict(n=48, pct=0.25), dict(n=48, pct=0.1),
    ], "변동성 압축 후 확장"),
    # rsi2는 현실 체결 모델(다음봉 시가)에서 유일하게 살아남은 계열이라 격자를 넓게 본다
    Candidate("rsi2", sig_rsi2, True, [
        dict(th=5.0, gate=0.003), dict(th=2.0, gate=0.003),
        dict(th=10.0, gate=0.004), dict(th=5.0, gate=0.005),
        dict(th=5.0, gate=0.007), dict(th=10.0, gate=0.006),
        dict(th=5.0, gate=0.005, exit_level=60.0),
        dict(th=5.0, gate=0.005, trend_bars=1200),      # ≈30분봉 EMA200
    ], "RSI(2) 극단 + 상위추세"),
    Candidate("vwapdev", sig_vwapdev, True, [
        dict(window=288, k=2.0, gate=0.003), dict(window=288, k=2.5, gate=0.004),
        dict(window=576, k=2.0, gate=0.004), dict(window=144, k=2.5, gate=0.004),
    ], "VWAP 하단 회귀"),
    Candidate("openrange", sig_openrange, False, [
        dict(open_bars=12), dict(open_bars=24), dict(open_bars=36),
        dict(open_bars=12, gate=0.004),
    ], "개장 레인지 돌파"),
]

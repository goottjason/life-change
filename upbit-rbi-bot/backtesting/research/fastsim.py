"""
벡터화 백테스트 코어 — 장기 히스토리·walk-forward 용.

기존 research 스크립트는 매 봉마다 `df.iloc[:i+1]` 전체창으로 지표를 다시 계산해
O(n²)였다(1개월 데이터에서만 겨우 돌아감). 여기서는

1) 지표를 시리즈 전체에 대해 **한 번** 계산해 진입/청산 불리언 배열을 만들고,
2) 그 배열로 O(n) 시뮬레이션을 돈다.

라이브와의 동일성:
- 라이브(`upbit_client.get_candles`)는 매 루프 **200봉**만 받아 신호를 낸다.
  따라서 창 길이 의존 지표(VWAP)는 200봉 rolling 으로 계산해 라이브와 맞춘다.
  (이전 세션 스크립트는 전체 히스토리 VWAP을 썼다 → 라이브와 불일치)
- EMA/RSI/ATR 은 지수가중이라 200봉 창이면 수렴한다(`verify_fastsim.py`가 오차 확인).
- SL/TP/시간손절/역방향청산 우선순위는 `backtesting/engine.py`(헌장 §4)와 동일.

체결 모델:
- exit_mode="intrabar" (기본, 보수적): 봉의 저가가 손절선을 건드리면 손절 체결.
  같은 봉에서 고가가 익절선도 건드리면 **손절을 우선**(어느 쪽이 먼저인지 알 수 없으므로 불리하게).
- exit_mode="close": 종가만으로 판정(기존 engine.py 방식, 낙관적).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd

from config import charter as C
from indicators import ta

LOOKBACK = 200          # 라이브가 신호에 쓰는 창 길이 (get_candles count 기본값)
WARMUP = LOOKBACK       # 시뮬 시작 인덱스 (창이 다 찬 뒤부터)


@dataclass(frozen=True)
class Params:
    """전략 신호 + 청산 규칙 파라미터. 헌장 기본값이 default."""
    strategy: str                       # "macd" | "rsi" | "cvd"
    atr_mult: float                     # k: 손절거리 = k×ATR (헌장 §7)
    rr: float                           # 익절거리 = rr × 손절거리
    use_reverse: bool = True            # 역방향 신호 청산 사용 (헌장 §4.3)
    time_stop_bars: int = C.TIME_STOP_BARS
    trend_ema: int = 0                  # >0 이면 close > EMA(n) 일 때만 진입
    # macd
    macd_fast: int = 3
    macd_slow: int = 15
    macd_signal: int = 3
    # rsi
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_exit: float = 50.0
    # cvd
    cvd_lookback: int = 5

    def label(self) -> str:
        bits = [f"k={self.atr_mult}", f"rr={self.rr}"]
        if not self.use_reverse:
            bits.append("noRev")
        if self.trend_ema:
            bits.append(f"ema{self.trend_ema}")
        if self.strategy == "macd":
            bits.append(f"{self.macd_fast}/{self.macd_slow}/{self.macd_signal}")
        elif self.strategy == "rsi":
            bits.append(f"os{self.rsi_oversold:.0f}/x{self.rsi_exit:.0f}")
        else:
            bits.append(f"lb{self.cvd_lookback}")
        if self.time_stop_bars != C.TIME_STOP_BARS:
            bits.append(f"ts{self.time_stop_bars}")
        return " ".join(bits)


@dataclass
class Trade:
    entry_i: int
    exit_i: int
    pnl: float          # 수수료 차감 후 손익률
    reason: str


@dataclass(frozen=True)
class ExitCfg:
    """청산 규칙. 5분봉 단타 연구용으로 고정%·트레일링까지 지원한다.

    stop_kind="atr": 손절거리 = stop_val × ATR / 가격, 단 min_stop 하한 (헌장 §7 방식)
    stop_kind="pct": 손절거리 = stop_val (예: 0.006 = 0.6%) — 단타에서 ATR 하한 1%는 너무 넓다
    trail=True: 익절선 대신 '최고가 − 손절거리' 추적 청산 (추세 이어질 때 이익 확대)
    """
    stop_kind: str = "atr"
    stop_val: float = 1.5
    rr: float = 2.0
    time_stop_bars: int = C.TIME_STOP_BARS
    use_exit_signal: bool = True
    trail: bool = False
    min_stop: float = C.MIN_STOP_RATIO

    def label(self) -> str:
        stop = (f"atr{self.stop_val}" if self.stop_kind == "atr"
                else f"sl{self.stop_val:.2%}".replace("%", "%"))
        bits = [stop, f"rr{self.rr}", f"ts{self.time_stop_bars}"]
        if self.trail:
            bits.append("trail")
        if not self.use_exit_signal:
            bits.append("noSigExit")
        return "·".join(bits)

    def stop_ratio(self, atr_val: float, price: float) -> float:
        if self.stop_kind == "pct":
            return self.stop_val
        if price > 0 and atr_val > 0:
            return max(self.stop_val * atr_val / price, self.min_stop)
        return C.FALLBACK_STOP_RATIO


def rolling_vwap(df: pd.DataFrame, window: int = LOOKBACK) -> pd.Series:
    """라이브가 보는 창(window봉) 기준 VWAP — `ta.vwap`의 창 한정 버전."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    num = (typical * df["volume"]).rolling(window, min_periods=1).sum()
    den = df["volume"].rolling(window, min_periods=1).sum()
    return num / den


def signals(df: pd.DataFrame, p: Params) -> tuple[np.ndarray, np.ndarray]:
    """
    (enter, exit_) 불리언 배열. 전략 클래스의 signal() 판정 순서를 그대로 재현한다:
    진입 조건이 먼저 검사되므로, 진입 신호가 켜진 봉에서는 청산 신호가 나오지 않는다.
    """
    close = df["close"]
    if p.strategy == "macd":
        macd_line = ta.ema(close, p.macd_fast) - ta.ema(close, p.macd_slow)
        sig_line = ta.ema(macd_line, p.macd_signal)
        prev_m, prev_s = macd_line.shift(1), sig_line.shift(1)
        enter = (prev_m <= prev_s) & (macd_line > sig_line)
        exit_ = (prev_m >= prev_s) & (macd_line < sig_line)
    elif p.strategy == "rsi":
        r = ta.rsi(close, p.rsi_period)
        vw = rolling_vwap(df)
        enter = r < p.rsi_oversold
        exit_ = (r > p.rsi_exit) | (close >= vw)
    elif p.strategy == "cvd":
        direction = (close > df["open"]).astype(int) - (close < df["open"]).astype(int)
        dirvol = direction * df["volume"]
        # c[i] - c[i-lb] (누적합의 차) = 최근 lb봉 dirvol 합 → 창 길이와 무관
        cvd_change = dirvol.rolling(p.cvd_lookback).sum()
        price_change = close - close.shift(p.cvd_lookback)
        enter = (price_change < 0) & (cvd_change > 0)
        exit_ = (price_change > 0) & (cvd_change < 0)
    else:
        raise ValueError(f"unknown strategy {p.strategy}")

    enter = enter.fillna(False).to_numpy(dtype=bool)
    exit_ = exit_.fillna(False).to_numpy(dtype=bool)
    exit_ = exit_ & ~enter                      # 진입 조건 우선 (signal() 순서와 동일)

    if p.trend_ema:
        up = (close > ta.ema(close, p.trend_ema)).fillna(False).to_numpy(dtype=bool)
        enter = enter & up
    return enter, exit_


def signal_key(p: Params) -> tuple:
    """신호 배열에 영향을 주는 파라미터만 (청산 규칙 k/rr/noRev 는 신호와 무관 → 캐시 재사용)."""
    if p.strategy == "macd":
        return ("macd", p.macd_fast, p.macd_slow, p.macd_signal, p.trend_ema)
    if p.strategy == "rsi":
        return ("rsi", p.rsi_period, p.rsi_oversold, p.rsi_exit, p.trend_ema)
    return ("cvd", p.cvd_lookback, p.trend_ema)


@dataclass
class Precomp:
    enter: np.ndarray
    exit_: np.ndarray
    close: np.ndarray
    high: np.ndarray
    low: np.ndarray
    atr: np.ndarray
    open_: np.ndarray | None = None      # entry_delay=1(다음봉 시가 진입)용


_CACHE: dict[tuple, Precomp] = {}


def precompute(df: pd.DataFrame, p: Params, dfkey: str | None = None) -> Precomp:
    """지표/신호 배열 계산. dfkey를 주면 (dfkey, 신호파라미터)로 캐시해 스윕을 가속한다."""
    ck = (dfkey, signal_key(p)) if dfkey else None
    if ck is not None and ck in _CACHE:
        return _CACHE[ck]
    enter, exit_ = signals(df, p)
    pre = Precomp(
        enter=enter, exit_=exit_,
        close=df["close"].to_numpy(dtype=float),
        high=df["high"].to_numpy(dtype=float),
        low=df["low"].to_numpy(dtype=float),
        atr=ta.atr(df).to_numpy(dtype=float),
        open_=df["open"].to_numpy(dtype=float),
    )
    if ck is not None:
        _CACHE[ck] = pre
    return pre


def simulate(df: pd.DataFrame, p: Params, *, start: int = WARMUP, end: int | None = None,
             exit_mode: str = "intrabar", fee: float = C.FEE_ROUNDTRIP,
             dfkey: str | None = None, pre: Precomp | None = None,
             entry_mask: np.ndarray | None = None) -> list[Trade]:
    """
    [start, end) 구간에서 진입을 허용하고 청산까지 추적한다 (`simulate_naive`와 동일 결과,
    진입 없는 봉을 건너뛰어 빠름 — 파라미터 그리드×폴드 스윕에 필요).

    지표는 df 전체(구간 앞 데이터 포함)로 계산 — 인과적 지표만 쓰므로 lookahead 없음.
    구간 끝에 열려 있는 포지션은 마지막 종가로 청산 처리한다(reason="eod").
    """
    n = len(df)
    end = n if end is None else min(end, n)
    if pre is None:
        pre = precompute(df, p, dfkey)
    cfg = ExitCfg(stop_kind="atr", stop_val=p.atr_mult, rr=p.rr,
                  time_stop_bars=p.time_stop_bars, use_exit_signal=p.use_reverse)
    return simulate_arrays(pre, cfg, start=start, end=end, exit_mode=exit_mode, fee=fee,
                           entry_mask=entry_mask)


def simulate_arrays(pre: Precomp, cfg: ExitCfg, *, start: int = WARMUP, end: int | None = None,
                    exit_mode: str = "intrabar", fee: float = C.FEE_ROUNDTRIP,
                    entry_mask: np.ndarray | None = None,
                    slippage: float = 0.0, entry_delay: int = 0) -> list[Trade]:
    """진입/청산 불리언 배열 + 청산규칙(ExitCfg)만으로 도는 시뮬 코어.

    전략 클래스와 무관하므로 새 후보 신호(단타 실험)도 같은 체결 모델로 검증된다.

    slippage: 왕복 추가 비용(호가 스프레드·체결 미끄러짐). 수수료에 더해 차감한다.
        단기 반전 신호는 '하락 종가 = 매도호가 체결가'일 수 있어(bid-ask bounce)
        실제로는 매수호가로 사야 한다 → 이 비용을 넣지 않으면 허구의 엣지가 나온다.
    entry_delay: 1이면 신호 다음 봉의 **시가**로 진입(신호 봉 종가 체결 가정 제거).
        신호 봉 종가에 바로 못 사는 현실(라이브는 10초 주기 폴링·지정가)을 반영한다.
    """
    enter, exit_ = pre.enter, pre.exit_
    close, high, low, atr = pre.close, pre.high, pre.low, pre.atr
    open_ = pre.open_ if pre.open_ is not None else close
    n = len(close)
    end = n if end is None else min(end, n)
    intrabar = exit_mode == "intrabar"
    ts_bars = cfg.time_stop_bars
    use_sig = cfg.use_exit_signal
    cost = fee + slippage

    # entry_mask: 외부 게이트(예: BTC가 EMA200 위일 때만 진입). 인과적 마스크만 넣을 것.
    entries = np.flatnonzero(enter if entry_mask is None else (enter & entry_mask))
    trades: list[Trade] = []
    k = int(np.searchsorted(entries, max(start, WARMUP)))
    while k < len(entries):
        i = int(entries[k])                 # 신호 봉
        fill = i + entry_delay              # 실제 체결 봉
        if i >= end or fill >= end:
            break
        a = atr[i] if np.isfinite(atr[i]) else 0.0
        ep = close[i] if entry_delay == 0 else open_[fill]
        sl = cfg.stop_ratio(a, ep)
        tp = cfg.rr * sl
        sl_price, tp_price = ep * (1 - sl), ep * (1 + tp)
        peak = ep

        j = fill if entry_delay > 0 else i + 1   # 체결 봉 안에서도 손절/익절 판정
        done = False
        while j < end:                       # 시간손절 때문에 최대 ts_bars 회 반복
            g = None
            reason = ""
            if cfg.trail:                    # 트레일링: 최고가 갱신 시 손절선을 끌어올린다
                if high[j] > peak:
                    peak = high[j]
                sl_price = max(sl_price, peak * (1 - sl))
            if intrabar:
                if low[j] <= sl_price:       # 손절 우선 (같은 봉 양쪽 터치 시 불리하게)
                    g, reason = (sl_price - ep) / ep, "sl"
                elif not cfg.trail and high[j] >= tp_price:
                    g, reason = tp, "tp"
            else:
                gross = (close[j] - ep) / ep
                if close[j] <= sl_price:
                    g, reason = gross, "sl"
                elif not cfg.trail and gross >= tp:
                    g, reason = gross, "tp"
            if g is None and (j - fill) >= ts_bars:
                g, reason = (close[j] - ep) / ep, "time"
            if g is None and use_sig and exit_[j]:
                g, reason = (close[j] - ep) / ep, "reverse"
            if g is not None:
                trades.append(Trade(fill, j, g - cost, reason))
                done = True
                break
            j += 1
        if not done:                          # 구간 끝까지 보유 → 마지막 종가 청산
            last = end - 1
            trades.append(Trade(fill, last, (close[last] - ep) / ep - cost, "eod"))
            break
        k = int(np.searchsorted(entries, j + 1))   # 청산한 봉 다음부터 재진입 가능
    return trades


def simulate_naive(df: pd.DataFrame, p: Params, *, start: int = WARMUP, end: int | None = None,
                   exit_mode: str = "intrabar", fee: float = C.FEE_ROUNDTRIP,
                   dfkey: str | None = None, pre: Precomp | None = None) -> list[Trade]:
    """참조 구현 — engine.py 와 같은 봉단위 순차 루프. `simulate` 검증용."""
    n = len(df)
    end = n if end is None else min(end, n)
    if pre is None:
        pre = precompute(df, p, dfkey)
    enter, exit_ = pre.enter, pre.exit_
    close, high, low, atr = pre.close, pre.high, pre.low, pre.atr

    trades: list[Trade] = []
    in_pos = False
    ep = sl = tp = 0.0
    ei = 0
    intrabar = exit_mode == "intrabar"

    for i in range(max(start, WARMUP), end):
        if not in_pos:
            if enter[i]:
                a = atr[i] if np.isfinite(atr[i]) else 0.0
                sl = C.stop_ratio_from_atr(p.atr_mult, a, close[i])
                tp = p.rr * sl
                in_pos, ep, ei = True, close[i], i
            continue

        g = None
        reason = ""
        if intrabar:
            if low[i] <= ep * (1 - sl):          # 손절 우선 (같은 봉 양쪽 터치 시 불리하게)
                g, reason = -sl, "sl"
            elif high[i] >= ep * (1 + tp):
                g, reason = tp, "tp"
        else:
            gross = (close[i] - ep) / ep
            if gross <= -sl:
                g, reason = gross, "sl"
            elif gross >= tp:
                g, reason = gross, "tp"

        if g is None and (i - ei) >= p.time_stop_bars:
            g, reason = (close[i] - ep) / ep, "time"
        if g is None and p.use_reverse and exit_[i]:
            g, reason = (close[i] - ep) / ep, "reverse"

        if g is not None:
            trades.append(Trade(ei, i, g - fee, reason))
            in_pos = False

    if in_pos:
        last = end - 1
        trades.append(Trade(ei, last, (close[last] - ep) / ep - fee, "eod"))
    return trades


@dataclass
class Stats:
    trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    exp: float = 0.0            # 거래당 평균 손익률 (>0 이어야 장기 +)
    total: float = 0.0          # 복리 누적 수익률
    mdd: float = 0.0
    t_stat: float = 0.0         # exp 가 0과 다른지 (|t|>2 ≈ 유의)


def stats(pnls: Iterable[float]) -> Stats:
    arr = np.asarray(list(pnls), dtype=float)
    if arr.size == 0:
        return Stats()
    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())
    equity = np.cumprod(1 + arr)
    peak = np.maximum.accumulate(equity)
    mdd = float((1 - equity / peak).max())
    sd = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
    return Stats(
        trades=int(arr.size),
        win_rate=float(wins.size / arr.size),
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        exp=float(arr.mean()),
        total=float(equity[-1] - 1),
        mdd=mdd,
        t_stat=(float(arr.mean()) / (sd / np.sqrt(arr.size))) if sd > 0 else 0.0,
    )


def stats_chrono(items: Iterable[tuple]) -> Stats:
    """(진입시각, 손익률) 목록 → 시간순으로 정렬 후 집계.

    누적수익/MDD는 거래 순서에 의존하므로 여러 종목의 거래를 합칠 때 반드시 시간순으로
    이어붙여야 한다(종목별로 이어붙이면 MDD가 왜곡됨).
    ※ 누적수익/MDD는 '전액을 한 거래씩 순차 복리'라는 단순 가정 — 실제 포트폴리오는
      동시 3포지션·리스크 기반 사이징(§7)이라 값이 다르다. 방향 판단용 지표로만 본다.
    """
    return stats([p for _, p in sorted(items, key=lambda x: x[0])])


def charter_pass(s: Stats) -> bool:
    """헌장 §11 실전 투입 기준."""
    return (s.trades >= C.BACKTEST_MIN_TRADES
            and s.win_rate >= C.BACKTEST_MIN_WINRATE
            and s.profit_factor >= C.BACKTEST_MIN_PROFIT_FACTOR
            and s.mdd <= C.BACKTEST_MAX_DRAWDOWN)

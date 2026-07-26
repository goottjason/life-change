"""
라이브에 실제로 넣을 형태가 백테스트와 같은 성적인지 확인한다 (배포 전 필수 관문).

문제: 검증된 설정은 추세 필터로 **5분봉 EMA2400**을 썼다. 그런데 라이브는 매 tick 캔들을
200봉만 받는다(`upbit_client.get_candles(count=200)`). 2400봉을 매번 받으면 API 호출이
12배로 늘어난다. 따라서 라이브에서는 **1시간봉 EMA200**(같은 200시간 시간상수)으로 계산해야 한다.

'같을 것'이라는 추정으로 실계좌를 돌릴 수는 없으므로 두 변형을 같은 조건에서 비교한다:
  A) trend = EMA2400(5분봉)        — 백테스트로 검증된 원본
  B) trend = EMA200(1시간봉)        — 라이브가 계산할 값 (5분봉을 1시간으로 리샘플해 산출)

B가 A와 같은 수준이면 B를 배포한다. 아니면 라이브 코드를 A에 맞춰야 한다.

실행: .venv/bin/python backtesting/research/lab_live_variant.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from config import charter as C
from indicators import ta
from backtesting.research import lab, portfolio
from backtesting.research.data_cache import DATA_DIR
from backtesting.research.fastsim import ExitCfg, Precomp, simulate_arrays, stats_chrono
from backtesting.research.lab_final import SELECTION_START
from backtesting.research.lab_screen import ENTRY_DELAY, LAB_WARMUP

TH, GATE, STOP, TS = 3.0, 0.006, 0.025, 96
EXIT_LEVEL = 70.0        # lab.sig_rsi2 의 기본값 — 검증된 설정의 청산 임계값


def trend_5m(df: pd.DataFrame) -> np.ndarray:
    """A안: 5분봉 EMA2400 위 (검증된 원본)."""
    return (df["close"] > ta.ema(df["close"], 2400)).fillna(False).to_numpy(bool)


def trend_1h(df: pd.DataFrame) -> np.ndarray:
    """B안: 1시간봉 EMA200 위. 라이브가 get_candles('minute60', 200)로 얻는 값과 동일.

    5분봉을 1시간으로 리샘플해 종가를 만들고 EMA200을 계산한 뒤, 각 5분봉 시점에서
    **직전에 완성된 1시간봉**의 값을 쓴다(현재 진행 중인 1시간봉을 쓰면 미래 정보가 섞인다).
    """
    h = df["close"].resample("1h").last().dropna()
    ema = ta.ema(h, 200)
    up = (h > ema)
    # 1시간봉 t의 판정은 t+1h 이후부터 사용 가능 → shift(1) 후 5분봉에 ffill
    aligned = up.shift(1).reindex(df.index, method="ffill")
    return aligned.astype(float).fillna(0.0).to_numpy(bool)


def signals(df, market, trend_fn):
    r2 = ta.rsi(df["close"], 2)
    atr_ratio = ta.atr(df) / df["close"]
    up = trend_fn(df)
    enter = (r2 <= TH).fillna(False).to_numpy(bool) & up & \
            (atr_ratio >= GATE).fillna(False).to_numpy(bool)
    exit_ = (r2 >= EXIT_LEVEL).fillna(False).to_numpy(bool) & ~enter
    return enter, exit_


def run(panel, ohlc, ctx, trend_fn, med, lo, hi):
    cfg = ExitCfg("pct", STOP, 1.0, time_stop_bars=TS, use_exit_signal=True)
    items, pts = [], []
    for m, df in panel.items():
        enter, exit_ = signals(df, m, trend_fn)
        close, high, low, atr, open_ = ohlc[m]
        pre = Precomp(enter, exit_, close, high, low, atr, open_)
        for t in simulate_arrays(pre, cfg, start=lo, end=hi, slippage=med[m],
                                 entry_delay=ENTRY_DELAY):
            items.append((ctx.index[t.entry_i], t.pnl))
            pts.append(portfolio.PortTrade(ctx.index[t.entry_i], ctx.index[t.exit_i], t.pnl,
                                           cfg.stop_ratio(atr[t.entry_i], close[t.entry_i]), m))
    return items, pts


def line(name, items, pts):
    s = stats_chrono(items)
    if s.trades == 0:
        return f"{name:34s}      0"
    pf = "  inf" if s.profit_factor == float("inf") else f"{s.profit_factor:5.2f}"
    r = portfolio.run(pts) if pts else None
    return (f"{name:34s} {s.trades:6d} {s.win_rate:6.1%} {pf} {s.exp:+8.3%} {s.t_stat:+6.2f} "
            f"│ 계좌 {r.total_return:+7.1%} MDD {r.mdd:5.1%}")


def main() -> None:
    med = {m: float(v) for m, v in json.loads((DATA_DIR / "spreads.json").read_text()).items()}
    low = {m for m, v in med.items() if v <= C.MAX_SPREAD_RATIO} if hasattr(C, "MAX_SPREAD_RATIO") \
        else {m for m, v in med.items() if v <= 0.001}
    full = lab.load_panel("minute5")
    ctx = lab.Ctx.build(full)
    panel = {m: df for m, df in full.items() if m in low}
    n = len(ctx.index)
    split = int(ctx.index.searchsorted(SELECTION_START))
    ohlc = {m: (df["close"].to_numpy(float), df["high"].to_numpy(float),
                df["low"].to_numpy(float), ta.atr(df).to_numpy(float),
                df["open"].to_numpy(float)) for m, df in panel.items()}

    print(f"라이브 변형 검증 · RSI(2)≤{TH:.0f} + ATR≥{GATE:.1%} + 추세필터 · "
          f"손절/익절 {STOP:.1%} · 시간손절 {TS}봉")
    print(f"대상 {len(panel)}종목({', '.join(sorted(m.replace('KRW-','') for m in panel))}) · "
          f"종목별 실측 스프레드 적용 · 다음봉 시가 진입\n")
    print(f"{'구성':34s} {'거래':>6s} {'승률':>6s} {'PF':>5s} {'exp':>8s} {'t':>6s}")
    rows = {}
    for tag, fn in (("A 5분봉 EMA2400 (검증된 원본)", trend_5m),
                    ("B 1시간봉 EMA200 (라이브 예정)", trend_1h)):
        for label, lo, hi in (("홀드아웃", LAB_WARMUP, split), ("전구간", LAB_WARMUP, n)):
            items, pts = run(panel, ohlc, ctx, fn, med, lo, hi)
            print(line(f"{tag} · {label}", items, pts))
            rows[(tag[0], label)] = stats_chrono(items)
        print()

    a, b = rows[("A", "홀드아웃")], rows[("B", "홀드아웃")]
    print("판정 (홀드아웃 기준):")
    print(f"  A exp {a.exp:+.3%} t{a.t_stat:+.2f} 거래 {a.trades} / "
          f"B exp {b.exp:+.3%} t{b.t_stat:+.2f} 거래 {b.trades}")
    ok = b.exp > 0 and b.t_stat > 2 and b.exp >= a.exp * 0.7
    print("  " + ("✅ B(1시간봉 EMA200)로 배포 가능 — A와 동등 수준"
                  if ok else "❌ B가 A보다 크게 열위 → 라이브도 A(5분봉 EMA2400) 방식으로 구현해야 함"))


if __name__ == "__main__":
    main()

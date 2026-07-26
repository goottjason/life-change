"""
15분봉 병행 검증 — 거래 빈도를 늘리는 정당한 경로인가.

배경: 5분봉은 '한 봉 평균 움직임 0.196% vs 왕복비용 0.15%' 라 비용이 기회의 77%를 먹는다.
15분봉은 0.323% vs 0.15% = 46% 로 구조적으로 유리하다. 같은 신호를 15분봉에도 적용하면
 (a) 거래당 기댓값이 커지고 (b) 5분봉과 신호 시점이 달라 거래 기회가 늘어난다.

데이터: 2년 5분봉 캐시를 15분봉으로 리샘플(업비트 15분봉과 동일 — 5분봉 3개 집계).
비용: 종목별 실측 스프레드 + 수수료 0.1%.
추세: 1시간봉 EMA200(라이브와 동일 계산).
홀드아웃: 2025-12-29 이전 = 앞선 어떤 선택에도 관여하지 않은 구간.

통과 기준(사전 등록): 홀드아웃에서 exp > 0 AND t > 2 AND 거래 ≥ 100.

실행: .venv/bin/python backtesting/research/lab_15m.py
"""
from __future__ import annotations

import json
import sys
from itertools import product
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
from backtesting.research.lab_screen import ENTRY_DELAY

WARM = 900              # 15분봉 워밍업(1시간봉 EMA200 확보에 충분)
TIME_STOP = 32          # 8시간 = 15분 × 32봉 (5분봉 96봉과 동일 시간)
GRID = list(product([3.0, 5.0], [0.006, 0.010, 0.012], [0.025, 0.030]))
PASS_T, PASS_TRADES = 2.0, 100


def to_15m(df5: pd.DataFrame) -> pd.DataFrame:
    """5분봉 → 15분봉 집계 (업비트 15분봉과 동일)."""
    return df5.resample("15min").agg({"open": "first", "high": "max", "low": "min",
                                      "close": "last", "volume": "sum"}).dropna()


def trend_1h_on(df: pd.DataFrame) -> np.ndarray:
    """1시간봉 EMA200 위 여부를 대상 봉 인덱스에 정렬(직전 완성 1시간봉 기준)."""
    h = df["close"].resample("1h").last().dropna()
    up = h > ta.ema(h, 200)
    aligned = up.shift(1).reindex(df.index, method="ffill")
    return aligned.astype(float).fillna(0.0).to_numpy(bool)


def signals(df: pd.DataFrame, th: float, gate: float) -> tuple[np.ndarray, np.ndarray]:
    r2 = ta.rsi(df["close"], 2)
    atr_ratio = ta.atr(df) / df["close"]
    enter = ((r2 <= th).fillna(False).to_numpy(bool)
             & trend_1h_on(df)
             & (atr_ratio >= gate).fillna(False).to_numpy(bool))
    exit_ = (r2 >= 70).fillna(False).to_numpy(bool) & ~enter
    return enter, exit_


def run(panel, ohlc, th, gate, stop, med, lo, hi):
    cfg = ExitCfg("pct", stop, 1.0, time_stop_bars=TIME_STOP, use_exit_signal=True)
    items, pts = [], []
    for m, df in panel.items():
        enter, exit_ = signals(df, th, gate)
        close, high, low, atr, open_ = ohlc[m]
        pre = Precomp(enter, exit_, close, high, low, atr, open_)
        for t in simulate_arrays(pre, cfg, start=lo, end=hi, slippage=med[m],
                                 entry_delay=ENTRY_DELAY):
            items.append((df.index[t.entry_i], t.pnl))
            pts.append(portfolio.PortTrade(df.index[t.entry_i], df.index[t.exit_i], t.pnl,
                                           cfg.stop_ratio(atr[t.entry_i], close[t.entry_i]), m))
    return items, pts


def main() -> None:
    med = {m: float(v) for m, v in json.loads((DATA_DIR / "spreads.json").read_text()).items()}
    low = {m for m, v in med.items() if v <= C.MAX_SPREAD_RATIO}
    panel5 = lab.load_panel("minute5")
    panel = {m: to_15m(df) for m, df in panel5.items() if m in low}
    idx = next(iter(panel.values())).index
    n = len(idx)
    split = int(idx.searchsorted(SELECTION_START))
    ohlc = {m: (df["close"].to_numpy(float), df["high"].to_numpy(float),
                df["low"].to_numpy(float), ta.atr(df).to_numpy(float),
                df["open"].to_numpy(float)) for m, df in panel.items()}

    print(f"15분봉 검증 · {len(panel)}종목({', '.join(sorted(m.replace('KRW-','') for m in panel))}) "
          f"· {n}봉 {idx[0].date()}~{idx[-1].date()}")
    print(f"홀드아웃 {idx[WARM].date()}~{idx[split-1].date()} / 선택구간 {SELECTION_START.date()}~")
    print(f"시간손절 {TIME_STOP}봉(8시간) · 종목별 실측 스프레드 + 수수료 0.1% · 다음봉 시가 진입")
    print(f"통과선: 홀드아웃 exp>0 & t>{PASS_T} & 거래≥{PASS_TRADES}\n")
    print(f"{'설정':28s} {'홀드아웃':>26s} │ {'전구간':>26s} │ {'계좌':>15s}")
    print(f"{'':28s} {'거래':>6s} {'승률':>6s} {'exp':>7s} {'t':>5s} │ "
          f"{'거래':>6s} {'승률':>6s} {'exp':>7s} {'t':>5s} │ {'수익':>7s} {'MDD':>6s}")

    best = None
    for th, gate, stop in GRID:
        h_items, _ = run(panel, ohlc, th, gate, stop, med, WARM, split)
        f_items, f_pts = run(panel, ohlc, th, gate, stop, med, WARM, n)
        sh, sf = stats_chrono(h_items), stats_chrono(f_items)
        r = portfolio.run(f_pts) if f_pts else None
        ok = sh.exp > 0 and sh.t_stat > PASS_T and sh.trades >= PASS_TRADES
        name = f"th={th:.0f} gate={gate:.1%} sl={stop:.1%}"
        print(f"{name:28s} {sh.trades:6d} {sh.win_rate:6.1%} {sh.exp:+7.3%} {sh.t_stat:+5.2f} │ "
              f"{sf.trades:6d} {sf.win_rate:6.1%} {sf.exp:+7.3%} {sf.t_stat:+5.2f} │ "
              f"{(r.total_return if r else 0):+7.1%} {(r.mdd if r else 0):6.1%}"
              f"{'  ✅통과' if ok else ''}")
        if ok and (best is None or sh.t_stat > best[0].t_stat):
            best = (sh, sf, r, th, gate, stop)

    print()
    if best is None:
        print("❌ 통과 조합 없음 — 15분봉 병행은 적용하지 않는다(거래만 늘고 엣지 없음).")
        return
    sh, sf, r, th, gate, stop = best
    print(f"✅ 채택: th={th:.0f} gate={gate:.1%} sl={stop:.1%} ts={TIME_STOP}봉")
    print(f"   홀드아웃 {sh.trades}거래 승률 {sh.win_rate:.1%} PF {sh.profit_factor:.2f} "
          f"exp {sh.exp:+.3%} t{sh.t_stat:+.2f}")
    print(f"   전구간 {sf.trades}거래 승률 {sf.win_rate:.1%} PF {sf.profit_factor:.2f} "
          f"exp {sf.exp:+.3%} t{sf.t_stat:+.2f} · 계좌 {r.total_return:+.1%} MDD {r.mdd:.1%}")
    days = (idx[-1] - idx[WARM]).days
    print(f"   빈도: {sf.trades}거래 / {days}일 = 하루 {sf.trades/days:.2f}회 "
          f"(5분봉 후보는 하루 0.78회)")


if __name__ == "__main__":
    main()

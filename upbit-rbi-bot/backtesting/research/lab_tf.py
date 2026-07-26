"""
살아남은 신호(rsi2 계열)의 타임프레임 견고성 — 5분봉 vs 15분봉.

엣지가 실재하면 인접 타임프레임에서도 방향이 유지된다. 5분봉에서만 보이고 15분봉에서
사라지면 그건 미시구조 잡음(호가·틱)일 가능성이 높다. 반대로 15분봉에서 더 강하면
같은 아이디어를 더 낮은 빈도로 쓰는 것이 수수료 측면에서 유리하다.

파라미터는 타임프레임에 맞춰 환산한다(고정값을 그대로 옮기면 비교가 성립하지 않는다):
 - 상위추세 필터: 5분 2400봉 = 15분 800봉 (동일하게 '1시간봉 EMA200' 수준)
 - 변동성 게이트: ATR은 √(봉길이)에 비례 → 15분 게이트 = 5분 게이트 × √3
 - 시간손절: 같은 실제 시간(5분 96봉 = 8시간 = 15분 32봉)

실행: .venv/bin/python backtesting/research/lab_tf.py
"""
from __future__ import annotations

import sys
from math import sqrt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config import charter as C
from indicators import ta
from backtesting.research import lab, portfolio
from backtesting.research.fastsim import ExitCfg, Precomp, simulate_arrays, stats_chrono
from backtesting.research.lab_screen import ENTRY_DELAY, IS_FRACTION, LAB_WARMUP, SLIPPAGE

# (interval, 봉길이배수, 상위추세 EMA봉수, 시간손절 봉수)
TFS = [("minute5", 1.0, 2400, 96), ("minute15", 3.0, 800, 32)]
BASE_GATES = [0.003, 0.005, 0.006, 0.007]
BASE_TH = [5.0, 10.0]
STOPS = [0.010, 0.015]


def main() -> None:
    for interval, mult, trend_bars, ts in TFS:
        panel = lab.load_panel(interval)
        if len(panel) < 6:
            print(f"[{interval}] 캐시 부족\n")
            continue
        ctx = lab.Ctx.build(panel)
        n = len(ctx.index)
        split = int(n * IS_FRACTION)
        warm = min(LAB_WARMUP, max(trend_bars + 200, 1000))
        ohlc = {m: (df["close"].to_numpy(float), df["high"].to_numpy(float),
                    df["low"].to_numpy(float), ta.atr(df).to_numpy(float),
                    df["open"].to_numpy(float)) for m, df in panel.items()}
        print(f"── {interval} · {len(panel)}종목 · {n}봉 "
              f"({ctx.index[0].date()}~{ctx.index[-1].date()}) · "
              f"IS~{ctx.index[split-1].date()} / OOS {ctx.index[split].date()}~ · "
              f"왕복비용 {C.FEE_ROUNDTRIP + SLIPPAGE:.2%}")
        print(f"{'파라미터':34s} {'IS거래':>6s} {'IS exp':>8s} {'IS t':>6s} | "
              f"{'OOS거래':>7s} {'OOS승률':>7s} {'OOS exp':>8s} {'OOS t':>6s} {'포트폴리오':>12s}")
        for th in BASE_TH:
            for g in BASE_GATES:
                gate = round(g * sqrt(mult), 5)
                params = dict(th=th, gate=gate, trend_bars=trend_bars)
                sigs = {m: lab.sig_rsi2(df, ctx, m, **params) for m, df in panel.items()}
                for stop in STOPS:
                    cfg = ExitCfg("pct", stop, 1.0, time_stop_bars=ts, use_exit_signal=True)
                    is_it, oos_it, pts = [], [], []
                    for m, (enter, exit_) in sigs.items():
                        close, high, low, atr, open_ = ohlc[m]
                        pre = Precomp(enter, exit_, close, high, low, atr, open_)
                        for t in simulate_arrays(pre, cfg, start=warm, end=split,
                                                 slippage=SLIPPAGE, entry_delay=ENTRY_DELAY):
                            is_it.append((ctx.index[t.entry_i], t.pnl))
                        for t in simulate_arrays(pre, cfg, start=split, end=n,
                                                 slippage=SLIPPAGE, entry_delay=ENTRY_DELAY):
                            oos_it.append((ctx.index[t.entry_i], t.pnl))
                            pts.append(portfolio.PortTrade(
                                ctx.index[t.entry_i], ctx.index[t.exit_i], t.pnl,
                                cfg.stop_ratio(atr[t.entry_i], close[t.entry_i]), m))
                    si, so = stats_chrono(is_it), stats_chrono(oos_it)
                    port = portfolio.run(pts).total_return if pts else 0.0
                    name = f"th={th:.0f} gate={gate:.2%} sl={stop:.1%} ts={ts}"
                    print(f"{name:34s} {si.trades:6d} {si.exp:+8.3%} {si.t_stat:+6.2f} | "
                          f"{so.trades:7d} {so.win_rate:7.1%} {so.exp:+8.3%} {so.t_stat:+6.2f} "
                          f"{port:+11.1%}")
        print()


if __name__ == "__main__":
    main()

"""
최종 후보 단일 설정의 정밀 검증 — 실전 투입 판단에 필요한 모든 각도.

후보 (lab_final.py 에서 유일하게 살아남은 설정):
  진입  RSI(2) ≤ 5  AND  종가 > EMA2400(≈1시간봉 EMA200)  AND  ATR/가격 ≥ 0.6%
  청산  RSI(2) ≥ 50(되돌림 완료) / 익절 +2.0% / 손절 −2.0% / 시간손절 96봉(8시간)
  체결  신호 다음 봉 시가, 왕복비용 = 수수료 0.1% + 슬리피지

검증 항목
 1) 슬리피지 민감도 — 엣지가 +0.05%/거래 수준이라 비용 가정이 결론을 좌우한다(가장 중요)
 2) 기간 안정성 — 반기별로 쪼개 한 시기에 몰려 있는지
 3) 종목 안정성 — 12종목 중 몇 개에서 양수인지
 4) 포트폴리오 계좌 곡선 — 동시 3포지션·리스크 1%·일일 손실 한도 반영, MDD 포함
 5) 헌장 §11 기준 대조

실행: .venv/bin/python backtesting/research/lab_verdict.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from config import charter as C
from indicators import ta
from backtesting.research import lab, portfolio
from backtesting.research.fastsim import ExitCfg, Precomp, simulate_arrays, stats_chrono
from backtesting.research.lab_final import SELECTION_START
from backtesting.research.lab_screen import ENTRY_DELAY, LAB_WARMUP

# 최종 후보 — 실측 스프레드까지 넣고 살아남은 유일한 설정 (저스프레드 종목 한정)
TH, GATE, STOP, TS = 3.0, 0.006, 0.025, 96
SLIPS = [0.0, 0.0005, 0.0010, 0.0015]


def collect(panel, ohlc, ctx, lo, hi, slip, per_market_slip: dict | None = None):
    """slip: 공통 슬리피지. per_market_slip: 종목별 실측 스프레드(있으면 이 값을 사용)."""
    cfg = ExitCfg("pct", STOP, 1.0, time_stop_bars=TS, use_exit_signal=True)
    items, pts, by_market = [], [], {}
    for m, df in panel.items():
        s = per_market_slip.get(m, slip) if per_market_slip else slip
        enter, exit_ = lab.sig_rsi2(df, ctx, m, th=TH, gate=GATE)
        close, high, low, atr, open_ = ohlc[m]
        pre = Precomp(enter, exit_, close, high, low, atr, open_)
        mk = []
        for t in simulate_arrays(pre, cfg, start=lo, end=hi, slippage=s,
                                 entry_delay=ENTRY_DELAY):
            it = (ctx.index[t.entry_i], t.pnl)
            items.append(it)
            mk.append(it)
            pts.append(portfolio.PortTrade(ctx.index[t.entry_i], ctx.index[t.exit_i], t.pnl,
                                           cfg.stop_ratio(atr[t.entry_i], close[t.entry_i]), m))
        by_market[m] = mk
    return items, pts, by_market


def line(name, items, pts=None):
    s = stats_chrono(items)
    if s.trades == 0:
        return f"{name:28s}      0"
    pf = "  inf" if s.profit_factor == float("inf") else f"{s.profit_factor:5.2f}"
    extra = ""
    if pts:
        r = portfolio.run(pts)
        extra = f" │ 계좌 {r.total_return:+7.1%} MDD {r.mdd:5.1%} 체결 {r.taken:5d}"
    flag = "  ✅" if s.exp > 0 and s.t_stat > 2 else ""
    return (f"{name:28s} {s.trades:6d} {s.win_rate:6.1%} {pf} {s.exp:+8.3%} {s.t_stat:+6.2f}"
            f"{extra}{flag}")


HEAD = f"{'구간':28s} {'거래':>6s} {'승률':>6s} {'PF':>5s} {'exp':>8s} {'t':>6s}"


def main() -> None:
    panel = lab.load_panel("minute5")
    ctx = lab.Ctx.build(panel)
    n = len(ctx.index)
    split = int(ctx.index.searchsorted(SELECTION_START))
    ohlc = {m: (df["close"].to_numpy(float), df["high"].to_numpy(float),
                df["low"].to_numpy(float), ta.atr(df).to_numpy(float),
                df["open"].to_numpy(float)) for m, df in panel.items()}

    print(f"후보 정밀검증 · RSI(2)≤{TH:.0f} + EMA2400 위 + ATR≥{GATE:.1%} · "
          f"손절/익절 {STOP:.1%} · 시간손절 {TS}봉(8h)")
    print(f"{len(panel)}종목 5분봉 {ctx.index[0].date()}~{ctx.index[-1].date()} · "
          f"다음봉 시가 진입\n")

    print("① 슬리피지 민감도 (수수료 0.1% + 슬리피지) " + "─" * 20)
    print(f"{'슬리피지':10s} {'홀드아웃 exp':>13s} {'t':>6s} │ {'전구간 exp':>11s} {'t':>6s} "
          f"│ {'계좌(전구간)':>12s} {'MDD':>6s}")
    for slip in SLIPS:
        h, _, _ = collect(panel, ohlc, ctx, LAB_WARMUP, split, slip)
        f_, fp, _ = collect(panel, ohlc, ctx, LAB_WARMUP, n, slip)
        sh, sf = stats_chrono(h), stats_chrono(f_)
        r = portfolio.run(fp)
        print(f"{slip:9.2%}  {sh.exp:+12.3%} {sh.t_stat:+6.2f} │ {sf.exp:+10.3%} "
              f"{sf.t_stat:+6.2f} │ {r.total_return:+11.1%} {r.mdd:6.1%}")
    print("  → 왕복비용이 0.25%(슬립 0.15%)면 엣지가 사라지는지가 실전 성패를 가른다.")

    slip = 0.0005
    print(f"\n② 기간 안정성 (슬리피지 {slip:.2%}) " + "─" * 20)
    print(HEAD + f" │ {'계좌':>7s} {'MDD':>6s} {'체결':>6s}")
    items, pts, by_market = collect(panel, ohlc, ctx, LAB_WARMUP, n, slip)
    halves = pd.date_range(ctx.index[LAB_WARMUP].normalize(), ctx.index[-1], freq="2QS")
    bounds = list(halves) + [ctx.index[-1] + pd.Timedelta(days=1)]
    for a, b in zip(bounds, bounds[1:]):
        seg = [x for x in items if a <= x[0] < b]
        segp = [p for p in pts if a <= p.entry_ts < b]
        if seg:
            tag = "홀드아웃" if b <= SELECTION_START else ("선택구간" if a >= SELECTION_START else "혼재")
            print(line(f"{a.date()}~{(b - pd.Timedelta(days=1)).date()} {tag}", seg, segp))

    print(f"\n③ 종목 안정성 (전구간, 슬리피지 {slip:.2%}) " + "─" * 20)
    pos = 0
    for m, mk in sorted(by_market.items(), key=lambda kv: -stats_chrono(kv[1]).exp):
        s = stats_chrono(mk)
        if s.trades == 0:
            continue
        pos += s.exp > 0
        print(line(f"  {m.replace('KRW-', '')}", mk))
    print(f"  → {pos}/{len(by_market)} 종목 양의 기댓값")

    print(f"\n④ 포트폴리오 (90,000원 시작 · 동시 {C.MAX_CONCURRENT_POSITIONS}포지션 · "
          f"리스크 {C.RISK_PER_TRADE_RATIO:.0%}/거래 · 일손실한도 {C.DAILY_LOSS_LIMIT_RATIO:.0%}) "
          + "─" * 10)
    for label, lo, hi in (("홀드아웃", LAB_WARMUP, split), ("선택구간", split, n),
                          ("전구간", LAB_WARMUP, n)):
        _, p_, _ = collect(panel, ohlc, ctx, lo, hi, slip)
        r = portfolio.run(p_)
        print(f"  {label:8s} {r.summary()}")

    print(f"\n⑤ 실측 스프레드 적용 (spread_check.py --sample 결과) " + "─" * 15)
    spath = Path(__file__).resolve().parent / "data" / "spreads.json"
    if not spath.exists():
        print("  spreads.json 없음 — `spread_check.py --sample` 먼저 실행")
    else:
        import json
        med = {m: float(v) for m, v in json.loads(spath.read_text()).items()}
        tradable = {m for m, v in med.items() if v <= 0.001}
        print("  종목별 슬리피지 = 실측 스프레드 중앙값 (호가 왕복 비용)")
        print(f"  거래가능(≤0.10%) {len(tradable)}종목: "
              f"{', '.join(m.replace('KRW-','') for m in sorted(tradable))}")
        print(f"  제외(>0.10%): " +
              ", ".join(f"{m.replace('KRW-','')} {v:.2%}"
                        for m, v in sorted(med.items(), key=lambda kv: -kv[1])
                        if v > 0.001))
        print("\n  " + HEAD + f" │ {'계좌':>7s} {'MDD':>6s}")
        for label, lo, hi in (("전종목·실측스프레드", LAB_WARMUP, n),
                              ("홀드아웃·실측", LAB_WARMUP, split)):
            it, pt, _ = collect(panel, ohlc, ctx, lo, hi, 0.0005, per_market_slip=med)
            print("  " + line(label, it, pt))
        sub = {m: df for m, df in panel.items() if m in tradable}
        sub_ohlc = {m: v for m, v in ohlc.items() if m in tradable}
        for label, lo, hi in (("저스프레드종목만·전구간", LAB_WARMUP, n),
                              ("저스프레드종목만·홀드아웃", LAB_WARMUP, split)):
            it, pt, _ = collect(sub, sub_ohlc, ctx, lo, hi, 0.0005, per_market_slip=med)
            print("  " + line(label, it, pt))

    s = stats_chrono(items)
    print(f"\n⑥ 헌장 §11 대조 (전구간, 슬리피지 0.05% 가정) " + "─" * 20)
    checks = [("거래수", s.trades, C.BACKTEST_MIN_TRADES, s.trades >= C.BACKTEST_MIN_TRADES),
              ("승률", f"{s.win_rate:.1%}", f"{C.BACKTEST_MIN_WINRATE:.0%}",
               s.win_rate >= C.BACKTEST_MIN_WINRATE),
              ("PF", f"{s.profit_factor:.2f}", C.BACKTEST_MIN_PROFIT_FACTOR,
               s.profit_factor >= C.BACKTEST_MIN_PROFIT_FACTOR)]
    r = portfolio.run(pts)
    checks.append(("MDD(계좌)", f"{r.mdd:.1%}", f"{C.BACKTEST_MAX_DRAWDOWN:.0%}",
                   r.mdd <= C.BACKTEST_MAX_DRAWDOWN))
    for name, got, need, ok in checks:
        print(f"  {name:10s} {str(got):>8s} (기준 {need}) {'✅' if ok else '❌'}")


if __name__ == "__main__":
    main()

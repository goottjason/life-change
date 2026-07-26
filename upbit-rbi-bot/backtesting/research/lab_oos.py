"""
선별 통과 후보의 OOS(out-of-sample) 검증 — **한 번만** 돈다.

`lab_screen.py`가 IS에서 통과시킨 조합(`data/finalists.json`)을 뒤 30% 구간에 그대로 적용한다.
추가 튜닝은 하지 않는다(그 순간 OOS가 아니게 되므로).

검증 항목
 1) OOS 거래당 기댓값·t (IS 대비 얼마나 깎이는지 = 과최적화 크기)
 2) 종목별 분해 — 소수 종목 편중이면 엣지가 아니다
 3) 시간 전·후반 분해
 4) **포트폴리오 시뮬** — 동시 3포지션·리스크 사이징·일일 손실 한도를 넣은 실제 계좌 곡선
    (거래당 기댓값이 +여도 계좌가 우상향하지 않을 수 있다)

실행: .venv/bin/python backtesting/research/lab_oos.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from config import charter as C
from indicators import ta
from backtesting.research import lab, portfolio
from backtesting.research.data_cache import DATA_DIR
from backtesting.research.fastsim import (ExitCfg, Precomp, simulate_arrays, stats_chrono)
from backtesting.research.lab_screen import (ENTRY_DELAY, IS_FRACTION, LAB_WARMUP, SLIPPAGE)


def load_finalists() -> list[dict]:
    p = DATA_DIR / "finalists.json"
    if not p.exists():
        return []
    return json.loads(p.read_text())


def main() -> None:
    fins = load_finalists()
    if not fins:
        print("통과 후보 없음(finalists.json 비어 있음) — lab_screen.py 먼저 실행/통과 필요")
        return
    panel = lab.load_panel("minute5")
    ctx = lab.Ctx.build(panel)
    n = len(ctx.index)
    split = int(n * IS_FRACTION)
    print(f"OOS 검증 · {len(fins)}개 후보 · 구간 {ctx.index[split].date()}~{ctx.index[-1].date()} "
          f"({n-split}봉) · 다음봉 시가 진입 · 왕복비용 {C.FEE_ROUNDTRIP + SLIPPAGE:.2%}\n")

    ohlc = {m: (df["close"].to_numpy(float), df["high"].to_numpy(float),
                df["low"].to_numpy(float), ta.atr(df).to_numpy(float),
                df["open"].to_numpy(float))
            for m, df in panel.items()}

    for f in fins:
        cand = next(c for c in lab.CANDIDATES if c.name == f["cand"])
        cfg = ExitCfg(**f["exit"])
        pstr = ",".join(f"{k}={v}" for k, v in f["params"].items())
        print(f"── {cand.name} [{pstr}] {cfg.label()} " + "─" * 20)
        isv = f["is_stats"]
        print(f"   IS: exp {isv['exp']:+.3%} t{isv['t']:+.2f} 거래 {isv['trades']} "
              f"승률 {isv['win_rate']:.1%} PF {isv['pf']:.2f}")

        per_market: dict[str, list] = {}
        ptrades: list[portfolio.PortTrade] = []
        for m, df in panel.items():
            enter, exit_ = cand.fn(df, ctx, m, **f["params"])
            close, high, low, atr, open_ = ohlc[m]
            pre = Precomp(enter, exit_, close, high, low, atr, open_)
            items = []
            for t in simulate_arrays(pre, cfg, start=max(split, LAB_WARMUP), end=n,
                                     slippage=SLIPPAGE, entry_delay=ENTRY_DELAY):
                ts = ctx.index[t.entry_i]
                items.append((ts, t.pnl))
                ptrades.append(portfolio.PortTrade(
                    entry_ts=ts, exit_ts=ctx.index[t.exit_i], pnl_ratio=t.pnl,
                    stop_ratio=cfg.stop_ratio(atr[t.entry_i], close[t.entry_i]), market=m))
            per_market[m] = items

        allitems = [x for v in per_market.values() for x in v]
        s = stats_chrono(allitems)
        pf = "inf" if s.profit_factor == float("inf") else f"{s.profit_factor:.2f}"
        print(f"  OOS: exp {s.exp:+.3%} t{s.t_stat:+.2f} 거래 {s.trades} "
              f"승률 {s.win_rate:.1%} PF {pf} MDD {s.mdd:.1%} 누적 {s.total:+.1%}"
              f"  {'✅ OOS 생존' if s.exp > 0 and s.t_stat > 1.5 else '❌ OOS 탈락'}")
        print(f"       과최적화 격차 IS {isv['exp']:+.3%} → OOS {s.exp:+.3%}")

        mid = split + (n - split) // 2
        h1 = [x for v in per_market.values() for x in v if x[0] < ctx.index[mid]]
        h2 = [x for v in per_market.values() for x in v if x[0] >= ctx.index[mid]]
        s1, s2 = stats_chrono(h1), stats_chrono(h2)
        print(f"       전반 exp {s1.exp:+.3%}(t{s1.t_stat:+.2f}, {s1.trades}건) / "
              f"후반 exp {s2.exp:+.3%}(t{s2.t_stat:+.2f}, {s2.trades}건)")
        pos = sum(1 for v in per_market.values() if v and stats_chrono(v).exp > 0)
        detail = " ".join(f"{m.replace('KRW-','')}{stats_chrono(v).exp:+.2%}"
                          for m, v in per_market.items() if v)
        print(f"       종목 {pos}/{len(per_market)} 양수 · {detail}")

        r = portfolio.run(ptrades)
        print(f"       포트폴리오(90,000원 시작, 동시3포지션, 리스크1%): {r.summary()}")
        print()


if __name__ == "__main__":
    main()

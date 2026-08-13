"""
15차 — **유니버스 확대: 스프레드 상한 0.10% 는 측정값인가 반올림인가**.

## 요구
"거래 빈도를 늘리고 싶다. 종목을 최대한 늘려라."

## 먼저 갈라야 할 것
**리스크를 키우는 것**(f 확대 = 공격)과 **기댓값이 음수인 종목을 추가하는 것**은 다르다.
후자는 공격이 아니라 그냥 손실이고, 9차에서 실측했다 — 거래당 기댓값이 음수면
**회전율은 손실 배수**다(±1% 목표는 연 −518~−829%).

## 그래서 답할 질문
현행 `MAX_SPREAD_RATIO = 0.001`(0.10%)은 **거래당 기댓값 ≈ +0.18%** 라는 근거로 정해졌다.
그런데 0.10% 는 **반올림한 값**이지 손익분기점을 측정한 값이 아니다. 진짜 질문은:
  ① rsi2 의 **gross 기댓값이 종목마다 얼마인가** (신호 품질이 종목별로 다른가)
  ② 그래서 **손익분기 스프레드가 몇 %인가** (0.10% 인가, 더 높은가)
  ③ 상한을 거기까지 풀면 **거래 빈도가 실제로 얼마나 느는가**

## 실측 스프레드 (2026-08-06, 업비트 279개 KRW 마켓 전수)
  ≤0.10% 13종목 · ≤0.15% 30 · ≤0.20% 53 · ≤0.25% 90 · ≤0.30% 114

실행: .venv/bin/python backtesting/research/lab_universe_expand.py
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from config import charter as C
from indicators import ta
from backtesting.research import lab
from backtesting.research.data_cache import load
from backtesting.research.fastsim import ExitCfg, Precomp, simulate_arrays
from backtesting.research.lab_screen import ENTRY_DELAY, SLIPPAGE

SPREADS = Path("/tmp/spreads_med.csv")   # 봇 실측 중앙값 (api/status 의 spread_stats)
# ⚠ 라이브 스펙에서 직접 읽는다 (하드코딩 금지 — 어긋나면 결론이 통째로 틀린다)
from config.charter import STRATEGY_SPECS as _S
CFG = dict(th=_S["rsi2"].entry_level, gate=_S["rsi2"].min_atr_ratio,
           sl=_S["rsi2"].stop_pct, time_stop=_S["rsi2"].time_stop_bars, tf="minute5")


def live_spreads() -> dict[str, float]:
    """
    봇이 실측한 스프레드 중앙값. **없으면 멈춘다.**

    ⚠ 2026-08-13: 이 함수가 존재하지 않는 파일(/tmp/spreads.csv)을 보면서 조용히 {} 를
      돌려주고 있었다. 그러면 아래에서 charter 의 VALIDATED_MARKETS 기본값(0.10%/0.25%)이나
      NaN 이 쓰이는데, **그 값으로 낸 결론이 15차 '유니버스 확대 불가' 판정의 근거였다.**
      조용한 폴백은 틀린 결론을 만든다 — 없으면 실패하게 바꿨다.
    생성: 서버 /api/status 의 spread_stats 중앙값을 market,spread(비율)로 저장.
    """
    if not SPREADS.exists():
        raise SystemExit(f"스프레드 실측 파일 없음: {SPREADS}\n"
                         "  서버 /api/status 의 spread_stats 로 먼저 생성할 것.")
    with open(SPREADS) as f:
        out = {r["market"].replace("KRW-", ""): float(r["spread"])
               for r in csv.DictReader(f)}
    if len(out) < 50:
        raise SystemExit(f"스프레드 실측값이 {len(out)}개뿐 — 파일을 다시 생성할 것.")
    return out


def all_cached():
    d = Path(__file__).resolve().parent / "data"
    return sorted({p.name.split("_")[0].replace("KRW-", "")
                   for p in d.glob("KRW-*_minute5.csv")})


def run_symbol(sym: str):
    """rsi2 를 라이브 파라미터로 돌려 **gross**(비용 차감 전) 거래를 얻는다."""
    df = load(f"KRW-{sym}", CFG["tf"])
    if df is None or len(df) < 4000:
        return None
    enter, exit_ = lab.sig_rsi2(df, df, f"KRW-{sym}", th=CFG["th"], gate=CFG["gate"])
    cfg = ExitCfg("pct", CFG["sl"], 1.0, time_stop_bars=CFG["time_stop"],
                  use_exit_signal=True)
    pre = Precomp(enter, exit_, df["close"].to_numpy(float), df["high"].to_numpy(float),
                  df["low"].to_numpy(float), ta.atr(df).to_numpy(float),
                  df["open"].to_numpy(float))
    # ★ fee=0.0 · slippage=0.0 이라야 t.pnl 이 진짜 gross 다.
    #   (fee 기본값은 C.FEE_ROUNDTRIP 이므로 생략하면 수수료가 이미 빠진 값이 나오고,
    #    아래에서 비용을 또 빼면 **이중 차감**이 된다 — 2026-08-13 에 실제로 그랬다)
    tr = list(simulate_arrays(pre, cfg, start=2500, end=len(df),
                              fee=0.0, slippage=0.0, entry_delay=ENTRY_DELAY))
    if len(tr) < 30:
        return None
    pnl = np.array([t.pnl for t in tr]) * 100
    ts = [df.index[t.entry_i] for t in tr]
    yrs = (df.index[-1] - df.index[2500]).days / 365.25
    return {"sym": sym, "n": len(pnl), "gross": pnl.mean(), "pnl": pnl,
            "ts": pd.Series(ts), "per_yr": len(pnl) / yrs, "yrs": yrs}


def cl_t(ts, v):
    g = pd.Series(v).groupby(pd.Series(ts).dt.to_period("M").values).mean()
    return float(g.mean() / (g.std(ddof=1) / math.sqrt(len(g)))) if len(g) > 1 else 0.0


def main() -> None:
    sp = live_spreads()
    syms = all_cached()
    rows = []
    for s in syms:
        r = run_symbol(s)
        if not r:
            continue
        spread = sp.get(s)
        if spread is None:            # 실측 없는 종목은 판정에서 제외(폴백 금지)
            continue
        cost = (C.FEE_ROUNDTRIP + spread) * 100
        r["spread"] = spread
        r["net"] = r["gross"] - cost
        r["breakeven_spread"] = r["gross"] / 100 - C.FEE_ROUNDTRIP   # 이 이하면 흑자
        r["ct"] = cl_t(r["ts"], r["pnl"])
        rows.append(r)
    d = pd.DataFrame([{k: v for k, v in r.items() if k not in ("pnl", "ts")}
                      for r in rows]).sort_values("gross", ascending=False)

    print(f"[유니버스 확대 검토 · rsi2 5분봉 · 캐시 {len(d)}종목 · 약 {d['yrs'].mean():.1f}년]")
    print(f"  현행 상한 MAX_SPREAD_RATIO = {C.MAX_SPREAD_RATIO*100:.2f}% · "
          f"수수료 왕복 {C.FEE_ROUNDTRIP*100:.2f}%\n")
    print(f"{'종목':6} {'거래':>6} {'연간':>6} {'gross%':>8} {'월t':>6} "
          f"{'실측스프레드':>10} {'손익분기스프레드':>14} {'net%':>8} {'판정':>6}")
    print("-" * 86)
    for _, r in d.iterrows():
        be = r["breakeven_spread"] * 100
        spd = r["spread"] * 100 if r["spread"] == r["spread"] else float("nan")
        mark = "✅" if r["net"] > 0 else "❌"
        cur = "●" if (r["sym"] in C.VALIDATED_MARKETS
                      and r["sym"] not in C.STRATEGY_BLACKLIST.get("rsi2", set())) else " "
        print(f"{cur}{r['sym']:5} {r['n']:6.0f} {r['per_yr']:6.0f} {r['gross']:+8.3f} "
              f"{r['ct']:+6.2f} {spd:9.3f}% {be:13.3f}% {r['net']:+8.3f} {mark:>6}")
    print("  ● = 현재 rsi2(5분) 가동 종목")

    ok = d[d["net"] > 0]
    print(f"\n■ 손익분기 스프레드 (= gross − 수수료 0.1%)")
    print(f"  중앙값 **{d['breakeven_spread'].median()*100:.3f}%** · "
          f"상위 25% {d['breakeven_spread'].quantile(0.75)*100:.3f}% · "
          f"하위 25% {d['breakeven_spread'].quantile(0.25)*100:.3f}%")
    print(f"  → 현행 상한 0.100% 는 이 중앙값과 비교해 "
          f"{'보수적' if d['breakeven_spread'].median()*100 > 0.10 else '적정하거나 느슨'}하다")
    print(f"\n■ net 양수 종목 {len(ok)}/{len(d)} — "
          f"{', '.join(ok['sym'].tolist())}")
    print(f"  그중 현재 미가동: "
          f"{', '.join([s for s in ok['sym'] if s not in C.VALIDATED_MARKETS or s in C.STRATEGY_BLACKLIST.get('rsi2', set())]) or '없음'}")
    cur_n = sum(1 for s in d['sym'] if s in C.VALIDATED_MARKETS
                and s not in C.STRATEGY_BLACKLIST.get("rsi2", set()))
    cur_f = d[d['sym'].isin([s for s in d['sym'] if s in C.VALIDATED_MARKETS
                             and s not in C.STRATEGY_BLACKLIST.get("rsi2", set())])]['per_yr'].sum()
    print(f"\n■ 거래 빈도 (연간, 캐시 종목 기준)")
    print(f"  현재 가동 {cur_n}종목 → 연 {cur_f:.0f}건")
    print(f"  net 양수 전체 {len(ok)}종목 → 연 {ok['per_yr'].sum():.0f}건 "
          f"(**{ok['per_yr'].sum()/max(cur_f,1):.1f}배**)")


if __name__ == "__main__":
    main()

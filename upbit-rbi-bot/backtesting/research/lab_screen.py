"""
5분봉 단타 후보 1차 선별 (in-sample) — **사전 등록된 절차**.

과최적화를 막기 위해 결과를 보기 전에 규칙을 고정한다:

  데이터   5분봉 12종목(BTC/ETH/XRP/SOL/DOGE/ADA/TRX/LINK/AVAX/DOT/BCH/ETC)
  분할     앞 70% = IS(선별용) / 뒤 30% = OOS(**여기서는 절대 보지 않음**, lab_oos.py에서 1회만)
  체결     intrabar(저가가 손절선 터치 시 손절, 같은 봉 양쪽이면 손절 우선), 수수료 왕복 0.1%
  통과선   IS 거래수 ≥ 200  AND  거래당 기댓값 ≥ +0.05%  AND  t ≥ 3.5
           (t 3.5 = 테스트 수 약 200개에 대한 Bonferroni α=0.05 보정 수준. 단순 t>2는 우연히 통과함)

통과 후보만 `data/finalists.json`에 저장 → `lab_oos.py`가 OOS로 1회 검증한다.

실행: .venv/bin/python backtesting/research/lab_screen.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from config import charter as C
from indicators import ta
from backtesting.research import lab
from backtesting.research.data_cache import DATA_DIR
from backtesting.research.fastsim import (ExitCfg, Precomp, simulate_arrays, stats_chrono)

LAB_WARMUP = 2_500          # 가장 긴 지표(EMA2400)까지 수렴시킨 뒤 시작
IS_FRACTION = 0.70

# 현실 체결 모델 (lab_robust.py 결과 반영 — 이 두 값이 결론을 좌우한다)
#  ENTRY_DELAY=1: 신호 봉 종가 즉시 체결 가정을 버리고 다음 봉 시가에 체결.
#    라이브는 10초 폴링·지정가라 신호 봉 종가에 살 수 없다. 이 가정만으로 단기 반전
#    후보(xrev/zdip)의 '승률 70%대 엣지'가 전부 소멸했다 = 호가 착시였다.
#  SLIPPAGE=0.05%: 왕복 호가 스프레드/미끄러짐. 업비트 KRW 지정가 체결을 전제한 보수적 값.
ENTRY_DELAY = 1
SLIPPAGE = 0.0005

# 청산 규칙 그리드 — 5분봉에서는 ATR 하한 1%가 너무 넓어 고정% 손절도 함께 본다
EXITS = [
    ExitCfg("pct", 0.004, 1.0, time_stop_bars=12),
    ExitCfg("pct", 0.006, 1.0, time_stop_bars=24),
    ExitCfg("pct", 0.006, 1.5, time_stop_bars=24),
    ExitCfg("pct", 0.010, 1.0, time_stop_bars=48),
    ExitCfg("pct", 0.010, 2.0, time_stop_bars=48),
    ExitCfg("pct", 0.008, 1.0, time_stop_bars=24, trail=True),
    ExitCfg("pct", 0.010, 1.0, time_stop_bars=96),      # 평균회귀는 되돌림을 오래 기다릴 수도
    ExitCfg("pct", 0.015, 1.0, time_stop_bars=96),
]

PASS_TRADES, PASS_EXP, PASS_T = 200, 0.0005, 3.5


def main() -> None:
    panel = lab.load_panel("minute5")
    if len(panel) < 6:
        print("5분봉 확장 캐시 부족 — `.venv/bin/python backtesting/research/data_cache.py lab` 먼저 실행")
        return
    ctx = lab.Ctx.build(panel)
    n = len(ctx.index)
    split = int(n * IS_FRACTION)
    print(f"5분봉 단타 후보 선별 · {len(panel)}종목 · 총 {n}봉 "
          f"({ctx.index[0].date()}~{ctx.index[-1].date()})")
    print(f"IS {ctx.index[LAB_WARMUP].date()}~{ctx.index[split-1].date()} ({split-LAB_WARMUP}봉) "
          f"/ OOS {ctx.index[split].date()}~{ctx.index[-1].date()} ({n-split}봉, 이번 단계에서 미사용)")
    print(f"체결모델: 신호 다음 봉 시가 진입(delay={ENTRY_DELAY}) · 수수료 {C.FEE_ROUNDTRIP:.1%} "
          f"+ 슬리피지 {SLIPPAGE:.2%} = 왕복비용 {C.FEE_ROUNDTRIP + SLIPPAGE:.2%}")
    print(f"통과선: 거래≥{PASS_TRADES}, exp≥{PASS_EXP:.2%}, t≥{PASS_T}\n")

    ohlc = {}
    for m, df in panel.items():
        ohlc[m] = (df["close"].to_numpy(float), df["high"].to_numpy(float),
                   df["low"].to_numpy(float), ta.atr(df).to_numpy(float),
                   df["open"].to_numpy(float))

    rows = []
    for cand in lab.CANDIDATES:
        for params in cand.params:
            sigs = {}
            for m, df in panel.items():
                try:
                    sigs[m] = cand.fn(df, ctx, m, **params)
                except Exception as e:
                    print(f"  ! {cand.name} {params} {m}: {e}")
            if not sigs:
                continue
            for cfg in EXITS:
                cfg = ExitCfg(cfg.stop_kind, cfg.stop_val, cfg.rr,
                              time_stop_bars=cfg.time_stop_bars,
                              use_exit_signal=cand.has_exit, trail=cfg.trail)
                items = []
                for m, (enter, exit_) in sigs.items():
                    close, high, low, atr, open_ = ohlc[m]
                    pre = Precomp(enter, exit_, close, high, low, atr, open_)
                    for t in simulate_arrays(pre, cfg, start=LAB_WARMUP, end=split,
                                             slippage=SLIPPAGE, entry_delay=ENTRY_DELAY):
                        items.append((ctx.index[t.entry_i], t.pnl))
                s = stats_chrono(items)
                rows.append(dict(cand=cand.name, params=params, exit=cfg.label(),
                                 use_exit_signal=cand.has_exit, cfg=cfg, stats=s))

    rows.sort(key=lambda r: -r["stats"].t_stat)
    print(f"테스트 {len(rows)}개 조합 · t 상위 25개")
    print(f"{'후보':10s} {'파라미터':40s} {'청산':26s} {'거래':>6s} {'승률':>6s} "
          f"{'PF':>5s} {'exp':>8s} {'누적':>9s} {'MDD':>6s} {'t':>6s}")
    for r in rows[:25]:
        s = r["stats"]
        pf = "  inf" if s.profit_factor == float("inf") else f"{s.profit_factor:5.2f}"
        star = "  ★통과" if (s.trades >= PASS_TRADES and s.exp >= PASS_EXP
                            and s.t_stat >= PASS_T) else ""
        pstr = ",".join(f"{k}={v}" for k, v in r["params"].items())
        print(f"{r['cand']:10s} {pstr:40s} {r['exit']:26s} {s.trades:6d} {s.win_rate:6.1%} "
              f"{pf} {s.exp:+8.3%} {s.total:+9.1%} {s.mdd:6.1%} {s.t_stat:+6.2f}{star}")

    finalists = [r for r in rows if r["stats"].trades >= PASS_TRADES
                 and r["stats"].exp >= PASS_EXP and r["stats"].t_stat >= PASS_T]
    print(f"\n후보별 최고 t (조합 전체 중):")
    best_by_cand = {}
    for r in rows:
        if r["cand"] not in best_by_cand or r["stats"].t_stat > best_by_cand[r["cand"]]["stats"].t_stat:
            best_by_cand[r["cand"]] = r
    for name, r in sorted(best_by_cand.items(), key=lambda kv: -kv[1]["stats"].t_stat):
        s = r["stats"]
        cand = next(c for c in lab.CANDIDATES if c.name == name)
        print(f"  {name:10s} t{s.t_stat:+6.2f} exp {s.exp:+.3%} 거래 {s.trades:6d} "
              f"승률 {s.win_rate:5.1%}  ({cand.note})")

    out = DATA_DIR / "finalists.json"
    payload = [dict(cand=r["cand"], params=r["params"], exit=dict(
        stop_kind=r["cfg"].stop_kind, stop_val=r["cfg"].stop_val, rr=r["cfg"].rr,
        time_stop_bars=r["cfg"].time_stop_bars, use_exit_signal=r["cfg"].use_exit_signal,
        trail=r["cfg"].trail), is_stats=dict(
        trades=r["stats"].trades, exp=r["stats"].exp, t=r["stats"].t_stat,
        win_rate=r["stats"].win_rate, pf=r["stats"].profit_factor)) for r in finalists]
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\n통과 {len(finalists)}개 → {out} 저장. 다음: lab_oos.py (OOS 1회 검증)")
    if not finalists:
        print("통과 후보 없음 — 5분봉에서 수수료를 넘는 엣지가 이 후보군에는 없다는 뜻.")


if __name__ == "__main__":
    main()

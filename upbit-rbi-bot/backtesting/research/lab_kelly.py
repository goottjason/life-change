"""
11차 — **rsi2 포지션 사이징: 지금 1%는 보수적인가?** (켈리 기준)

## 문제
현행 `RISK_PER_TRADE_RATIO = 0.01` (1거래 최대손실 = 자본 1%)은 **백테스트로 정한 값이
아니다.** 헌장에 적힌 관례값이다. 사용자가 "시드가 작으니 공격적으로 가고 싶다"고 했으므로,
**검증된 엣지(rsi2)에 대해 수학적으로 최적인 배팅 크기**를 구한다.

## 핵심 개념 — R 배수
포지션 크기 = 리스크예산 ÷ 손절거리비율(`charter.position_size_krw`) 이므로,
거래 하나의 **계좌 수익률 = f × R**, 여기서
    f = 1거래 리스크 비율(현행 0.01)
    R = 거래수익률 ÷ 손절거리비율   (= '리스크 몇 배를 벌었나')
따라서 사이징 문제는 **R 분포 위에서 f 를 고르는 문제**로 정확히 환원된다.

## 켈리와 그 함정
켈리 f* 는 `E[log(1 + f·R)]` 를 최대화하는 f 다. 장기 자산 성장률을 최대화하지만:
  - **f* 는 백테스트 표본에 과적합된다.** 엣지를 10% 과대추정하면 f* 는 크게 흔들린다.
  - **낙폭이 잔인하다.** 풀켈리의 기대 최대낙폭은 통상 50%+ 다.
  - 실무 표준은 **하프켈리(f*/2) 또는 쿼터켈리(f*/4)** — 성장률은 각각 75%·44%만 잃고
    낙폭은 훨씬 작다.
그래서 f* 를 계산만 하지 않고 **f 별 성장률·낙폭·파산확률을 같이 낸다.**

## 동시 포지션 보정 (중요)
헌장은 `MAX_CONCURRENT_POSITIONS = 3` 이다. 암호화폐는 횡단면 상관이 매우 높아서
3포지션이 사실상 **한 방향 3배 베팅**에 가깝다. 그래서 R 을 **시각별로 합산**해
'동시 노출'을 반영한 실제 계좌 수익률 계열로 켈리를 푼다. 거래를 독립으로 놓고 계산하면
f* 가 과대평가된다.

실행: .venv/bin/python backtesting/research/lab_kelly.py [--tf 5m|15m]
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from config import charter as C
from indicators import ta
from backtesting.research import lab
from backtesting.research.fastsim import ExitCfg, Precomp, simulate_arrays
from backtesting.research.lab_screen import ENTRY_DELAY, SLIPPAGE

# 라이브 rsi2 파라미터 (charter.STRATEGY_SPECS["rsi2"] 와 lab.sig_rsi2 기본값)
CFG5 = dict(th=5.0, gate=0.006, sl=0.025, time_stop=96, tf="minute5")
CFG15 = dict(th=3.0, gate=0.010, sl=0.030, time_stop=32, tf="minute15")


def collect(tfkey: str):
    """rsi2 거래를 (진입시각, 청산시각, R배수, 종목) 으로 모은다."""
    g = CFG5 if tfkey == "5m" else CFG15
    panel = lab.load_panel(g["tf"]) if hasattr(lab, "load_panel") else None
    if panel is None:                       # lab 에 로더가 없으면 직접 만든다
        from backtesting.research.data_cache import load
        panel = {}
        for sym in C.VALIDATED_MARKETS:
            if sym in C.STRATEGY_BLACKLIST.get("rsi2", set()) and tfkey == "5m":
                continue
            d = load(f"KRW-{sym}", g["tf"])
            if d is None or len(d) < 3000:
                continue
            panel[f"KRW-{sym}"] = d
    ctx = next(iter(panel.values()))
    cfg = ExitCfg("pct", g["sl"], 1.0, time_stop_bars=g["time_stop"], use_exit_signal=True)
    rows = []
    for m, df in panel.items():
        enter, exit_ = lab.sig_rsi2(df, ctx, m, th=g["th"], gate=g["gate"])
        pre = Precomp(enter, exit_, df["close"].to_numpy(float), df["high"].to_numpy(float),
                      df["low"].to_numpy(float), ta.atr(df).to_numpy(float),
                      df["open"].to_numpy(float))
        for t in simulate_arrays(pre, cfg, start=2500, end=len(df),
                                 slippage=SLIPPAGE, entry_delay=ENTRY_DELAY):
            rows.append({"in": df.index[t.entry_i], "out": df.index[t.exit_i],
                         "pnl": t.pnl, "R": t.pnl / g["sl"], "m": m})
    return pd.DataFrame(rows).sort_values("in").reset_index(drop=True), g


def account_series(d: pd.DataFrame, max_pos: int) -> np.ndarray:
    """
    동시 포지션을 반영한 **계좌 단위 R 계열**.
    같은 시각에 열려 있는 포지션은 함께 오르내리므로, 청산 시각 기준으로 묶어
    '한 번의 계좌 사건'으로 본다. 동시 보유 상한을 넘는 신호는 버린다(라이브와 동일).
    """
    open_until = []
    events = {}
    for _, r in d.iterrows():
        open_until = [t for t in open_until if t > r["in"]]
        if len(open_until) >= max_pos:
            continue                                    # 자리 없음 → 라이브도 못 잡는다
        open_until.append(r["out"])
        events.setdefault(r["out"], []).append(r["R"])
    return np.array([sum(v) for _, v in sorted(events.items())])


def growth(R: np.ndarray, f: float) -> float:
    """f 로 베팅했을 때의 거래당 로그 성장률. 파산(1+f·R ≤ 0)이면 −inf."""
    x = 1 + f * R
    if (x <= 0).any():
        return -np.inf
    return float(np.mean(np.log(x)))


def kelly_f(R: np.ndarray) -> float:
    lo, hi = 0.0, 1.0 / max(1e-9, -R.min()) * 0.999 if R.min() < 0 else 5.0
    best, bf = -np.inf, 0.0
    for f in np.linspace(1e-4, hi, 4000):
        g = growth(R, f)
        if g > best:
            best, bf = g, f
    return bf


def path_stats(R: np.ndarray, f: float, n_boot: int = 400, seed: int = 3):
    """부트스트랩으로 f 배팅의 낙폭·파산확률을 낸다(거래 순서를 섞어 재추출)."""
    rng = np.random.default_rng(seed)
    mdds, ruins, finals = [], 0, []
    for _ in range(n_boot):
        s = rng.choice(R, size=len(R), replace=True)
        eq = np.cumprod(np.maximum(1 + f * s, 1e-9))
        peak = np.maximum.accumulate(eq)
        mdds.append(float(np.max(1 - eq / peak)))
        if eq.min() <= 0.5:                      # 자본 반토막을 '사실상 파산'으로 본다
            ruins += 1
        finals.append(eq[-1])
    return float(np.median(mdds)), ruins / n_boot, float(np.median(finals))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="5m", choices=["5m", "15m"])
    ap.add_argument("--max-pos", type=int, default=C.MAX_CONCURRENT_POSITIONS)
    a = ap.parse_args()
    d, g = collect(a.tf)
    if d.empty:
        print("거래 0건 — 데이터 확인 필요"); return
    R_ind = d["R"].to_numpy()
    R_acc = account_series(d, a.max_pos)
    yrs = (d["out"].max() - d["in"].min()).days / 365.25

    print(f"[rsi2 {a.tf} 포지션 사이징 · 손절거리 {g['sl']*100:.1f}% · "
          f"동시보유 최대 {a.max_pos}]")
    print(f"  거래 {len(d):,}건 / {yrs:.1f}년 · 승률 {(R_ind > 0).mean()*100:.1f}% · "
          f"거래당 {d['pnl'].mean()*100:+.3f}%")
    print(f"  R 배수: 평균 {R_ind.mean():+.3f} · 표준편차 {R_ind.std(ddof=1):.3f} · "
          f"최악 {R_ind.min():+.2f} · 최고 {R_ind.max():+.2f}")
    print(f"  동시보유 반영 계좌사건 {len(R_acc):,}건 · 평균 R {R_acc.mean():+.3f} · "
          f"최악 {R_acc.min():+.2f}")

    fk = kelly_f(R_acc)
    print(f"\n  ★ 켈리 최적 f* = **{fk*100:.2f}%** (계좌사건 기준, 동시보유 반영)")
    print(f"     (거래 독립 가정 시 f* = {kelly_f(R_ind)*100:.2f}% — 이 값을 쓰면 과대베팅)")

    print(f"\n{'f (1거래 리스크)':>16} {'거래당 성장률':>12} {'연 성장률(추정)':>14} "
          f"{'중앙 MDD':>9} {'반토막 확률':>10}")
    print("-" * 68)
    ev_per_yr = len(R_acc) / yrs
    cands = [("현행 1%", 0.01), ("2%", 0.02), ("3%", 0.03), ("5%", 0.05),
             (f"쿼터켈리 {fk/4*100:.1f}%", fk / 4), (f"하프켈리 {fk/2*100:.1f}%", fk / 2),
             (f"풀켈리 {fk*100:.1f}%", fk), ("풀켈리×1.5(과베팅)", fk * 1.5)]
    for name, f in cands:
        gr = growth(R_acc, f)
        if not np.isfinite(gr):
            print(f"{name:>16} {'파산':>12}"); continue
        ann = (math.exp(gr * ev_per_yr) - 1) * 100
        mdd, ruin, _ = path_stats(R_acc, f)
        print(f"{name:>16} {gr*100:+11.4f}% {ann:+13.1f}% {mdd*100:8.1f}% {ruin*100:9.1f}%")

    print(f"\n[해석]")
    cur = growth(R_acc, 0.01); ann_cur = (math.exp(cur * ev_per_yr) - 1) * 100
    hk = growth(R_acc, fk / 2); ann_hk = (math.exp(hk * ev_per_yr) - 1) * 100
    print(f"  현행 1% → 연 {ann_cur:+.1f}% · 하프켈리 → 연 {ann_hk:+.1f}% "
          f"(**{ann_hk / max(ann_cur, 1e-9):.1f}배**)")
    cap = 90_000
    print(f"  자본 {cap:,}원 기준: 현행 {cap*ann_cur/100:+,.0f}원/년 · "
          f"하프켈리 {cap*ann_hk/100:+,.0f}원/년")
    print(f"\n  ⚠ 이 f* 는 **백테스트 표본에 대한 최적값**이다. 실전 엣지가 백테스트보다")
    print(f"     20%만 작아도 풀켈리는 과베팅이 된다. 인큐베이션 100건으로 엣지가 확인되기")
    print(f"     전까지는 쿼터켈리 이하가 안전하다.")


if __name__ == "__main__":
    main()

"""
13차 — **서킷 브레이커 재설계** (f = 1% → 2.4% 쿼터켈리에 맞춰).

## 왜 필요한가
11차에서 rsi2 의 켈리 f* = 9.48%, 현행 1% 는 그 1/9.5 로 보수적임을 확인했다.
그런데 **f 만 올리면 서킷이 먼저 걸려 봇이 멈춘다** — 헌장 값이 f=1% 기준이기 때문이다.
  `DAILY_LOSS_LIMIT_RATIO = 0.03` → f=2.4% 면 **손절 1.25번**에 하루 종료
  `MAX_DRAWDOWN_RATIO   = 0.15` → f=2.4% 의 중앙 MDD 는 30.6% → **정상 운영 중 영구 정지**
서킷·f·동시보유는 **한 세트**다. 따로 고치면 서로를 무력화한다.

## ★ 설계 원칙 — 오발률로 정한다, 수익률로 정하지 않는다
서킷은 안전장치다. "백테스트 수익이 가장 높은 임계값"을 고르면 그건 **또 하나의 과최적화**다
(4차: 전수 탐색 train↔test 상관 −0.281). 그래서 임계값은 이렇게 고른다:
  ① **정상 작동 시 거의 안 울릴 것** — 일시정지형은 연 1~2회, 영구정지형은 생애 5% 이하
  ② **엣지가 죽었을 때는 확실히 울릴 것** — 수익률 0/음수 시나리오에서 조기 발동
즉 임계값은 **정상 분포의 꼬리**에서 뽑고, **고장 시나리오로 검증**한다. 수익은 사후 확인만 한다.

## 현행 코드의 버그 두 개 (f 를 올리기 전에 반드시 수정)
① `reset_daily()` 가 **리포 어디에서도 호출되지 않는다** → `daily_pnl` 영구 누적
   → 누적 손실이 자본 3% 를 넘는 순간 '일일' 한도가 **영구 정지**로 변한다.
② `consecutive_losses >= 5` → 진입 차단 → 이길 기회 없음 → **리셋 불가 데드락**.
   (보유 중이던 포지션이 익절로 닫히는 경우에만 탈출)
→ 본 실험은 두 버그가 **고쳐진 것을 전제**로 재설계안을 만든다.

실행: .venv/bin/python backtesting/research/lab_circuit.py [--f 0.024]
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from backtesting.research.lab_kelly import collect

BARS_YEAR = 365.25


@dataclass
class Circuit:
    f: float = 0.024              # 1거래 리스크
    max_pos: int = 3              # 동시 보유
    daily_R: float = 4.0          # 일일 손실 한도 (R 배수) — 0 이면 끔
    streak: int = 5               # 연속 손절 임계
    streak_reset_daily: bool = True   # 연속손절 카운터를 날짜 경계에서 리셋(데드락 방지)
    mdd: float = 0.15             # 전면 정지 낙폭


def simulate(d: pd.DataFrame, c: Circuit, dead: bool = False, seed: int = 0,
             resume_bars: int = 0):
    """
    체결 순서대로 계좌를 굴린다. dead=True 면 **엣지가 죽은 시나리오**
    (R 을 평균 0 으로 중심화 + 섞기) — 서킷이 고장을 잡아내는지 보는 검정.
    반환: 지표 dict
    """
    R = d["R"].to_numpy().copy()
    if dead:
        rng = np.random.default_rng(seed)
        R = rng.permutation(R - R.mean())          # 평균 0, 분포는 그대로
    tin, tout = d["in"].to_numpy(), d["out"].to_numpy()
    n = len(d)

    eq, peak = 1.0, 1.0
    day = None
    daily_pnl = 0.0
    streak = 0
    halted = False
    open_until = []
    taken = skipped = 0
    fires = {"daily": 0, "streak": 0, "mdd": 0, "slots": 0}
    halt_at = None
    halt_idx = None
    curve = []
    order = np.argsort(tin)
    for k in order:
        t0, t1, r = tin[k], tout[k], R[k]
        if halted:
            if resume_bars and halt_idx is not None and (taken + skipped) - halt_idx >= resume_bars:
                halted = False; peak = eq          # 재가동: 고점 기준을 현재로 리셋
            else:
                skipped += 1; continue
        dd = pd.Timestamp(t0).date()
        if day != dd:                              # ★ 날짜 경계 리셋 (현행 코드의 버그 ①)
            day = dd
            daily_pnl = 0.0
            if c.streak_reset_daily:               # ★ 데드락 방지 (버그 ②)
                streak = 0
        open_until = [x for x in open_until if x > t0]
        if len(open_until) >= c.max_pos:
            fires["slots"] += 1; skipped += 1; continue
        if c.daily_R and daily_pnl <= -c.daily_R * c.f:
            fires["daily"] += 1; skipped += 1; continue
        if c.streak and streak >= c.streak:
            fires["streak"] += 1; skipped += 1; continue
        # 체결
        open_until.append(t1)
        pnl = c.f * r
        eq *= max(1 + pnl, 1e-9)
        daily_pnl += pnl
        streak = streak + 1 if r < 0 else 0
        taken += 1
        peak = max(peak, eq)
        curve.append((t0, eq))
        if c.mdd and (peak - eq) / peak >= c.mdd:
            halted = True
            halt_at = (pd.Timestamp(t0), eq)
            halt_idx = taken + skipped
            fires["mdd"] += 1
    yrs = (pd.Timestamp(tout.max()) - pd.Timestamp(tin.min())).days / BARS_YEAR
    e = np.array([x[1] for x in curve]) if curve else np.array([1.0])
    mdd = float(np.max(1 - e / np.maximum.accumulate(e)))
    cagr = (eq ** (1 / yrs) - 1) * 100 if yrs > 0 else 0.0
    return {"cagr": cagr, "mdd": mdd * 100, "eq": eq, "taken": taken,
            "skipped": skipped, "halted": halted, "fires": fires, "yrs": yrs,
            "halt_eq": None if not halt_at else halt_at[1],
            "halt_when": None if not halt_at else halt_at[0]}


def calibrate(d: pd.DataFrame, f: float, max_pos: int):
    """정상 분포의 꼬리에서 임계값 후보를 뽑는다 — 수익률은 보지 않는다."""
    # 일일 R 합계 분포 (동시보유 상한 반영 없이 보수적으로)
    dd = d.copy()
    dd["date"] = dd["in"].dt.date
    daily = dd.groupby("date")["R"].sum()
    # 연속 손절 스트릭 분포
    losses = (d.sort_values("in")["R"].to_numpy() < 0).astype(int)
    streaks, cur = [], 0
    for x in losses:
        cur = cur + 1 if x else 0
        if x:
            streaks.append(cur)
        elif cur:
            cur = 0
    mx = []
    cur = 0
    for x in losses:
        cur = cur + 1 if x else 0
        mx.append(cur)
    return daily, np.array(mx)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--f", type=float, default=0.024)
    ap.add_argument("--tf", default="5m")
    a = ap.parse_args()
    d, g = collect(a.tf)
    d = d.sort_values("in").reset_index(drop=True)
    yrs = (d["out"].max() - d["in"].min()).days / BARS_YEAR
    daily, streak_run = calibrate(d, a.f, 3)

    print(f"[서킷 재설계 · rsi2 {a.tf} · f = {a.f*100:.1f}% · {len(d):,}거래 / {yrs:.1f}년]")
    print(f"\n■ 1단계 — 정상 작동의 분포부터 본다 (여기서 임계값을 뽑는다)")
    print(f"  일일 R 합계: 중앙 {daily.median():+.2f}R · 5%분위 {daily.quantile(0.05):+.2f}R "
          f"· 1%분위 {daily.quantile(0.01):+.2f}R · 최악 {daily.min():+.2f}R "
          f"({len(daily)}거래일)")
    for k in (3, 4, 5, 6, 8):
        hit = (daily <= -k).mean()
        print(f"    일일한도 {k}R → 정상 작동 중 발동 {hit*100:5.2f}% 의 날 "
              f"(연 {hit*len(daily)/yrs:4.1f}회)")
    print(f"  연속 손절: 최대 {streak_run.max()}연패 · "
          f"5연패 이상 {int((streak_run >= 5).sum())}회 · 7연패 이상 "
          f"{int((streak_run >= 7).sum())}회 · 9연패 이상 {int((streak_run >= 9).sum())}회")

    print(f"\n■ 2단계 — 연속손절 서킷은 현행 값에서 사실상 상시 발동한다")
    for k in (5, 7, 9, 12, 15):
        cnt = int((streak_run >= k).sum())
        print(f"    {k:2d}연패 임계 → 1.9년간 {cnt:3d}회 발동 (연 {cnt/yrs:5.1f}회)")

    print(f"\n■ 3단계 — MDD 정지선: '살아있는 엣지'와 '죽은 엣지'를 가르는가")
    print(f"  부트스트랩 300경로. 정지선에 **도달하기까지 걸린 거래 수**로 본다 —")
    print(f"  살아있으면 늦게(또는 영영) 닿고, 죽었으면 일찍 닿아야 좋은 정지선이다.")
    R = d["R"].to_numpy()
    rng = np.random.default_rng(7)
    def hit_stats(Rsrc, thr, n_path=300):
        hits, times = 0, []
        for _ in range(n_path):
            s_ = rng.choice(Rsrc, size=len(Rsrc), replace=True)
            eq = np.cumprod(np.maximum(1 + a.f * s_, 1e-9))
            dd = 1 - eq / np.maximum.accumulate(eq)
            idx = np.nonzero(dd >= thr)[0]
            if len(idx):
                hits += 1; times.append(int(idx[0]))
        return hits / n_path, (np.median(times) if times else None)
    print(f"\n{'정지선':>8} {'살아있을 때 도달률':>16} {'중앙 도달거래':>12} │ "
          f"{'죽었을 때 도달률':>15} {'중앙 도달거래':>12}")
    print("-" * 76)
    Rdead = R - R.mean()
    for thr in (0.15, 0.20, 0.25, 0.30, 0.40, 0.50):
        pa, ta = hit_stats(R, thr)
        pd_, td = hit_stats(Rdead, thr)
        f_ta = f"{ta:,}" if ta else "—"
        f_td = f"{td:,}" if td else "—"
        print(f"{thr*100:7.0f}% {pa*100:15.1f}% {f_ta:>12} │ "
              f"{pd_*100:14.1f}% {f_td:>12}")

    print(f"\n■ 4단계 — 서킷이 수익을 얼마나 깎는가 (정지 후 재가동 반영)")
    print(f"  MDD 정지는 '영구 사망'이 아니라 **사람이 확인 후 재가동**이다.")
    print(f"  점검에 10거래일 걸린다고 보고 그동안만 쉬는 것으로 모델링한다.")
    print(f"\n{'구성':30} {'CAGR':>8} {'MDD':>7} {'체결/전체':>12} "
          f"{'일일발동':>8} {'연패발동':>8} {'정지':>6}")
    print("-" * 88)
    for name, c in [
        ("현행 서킷 · f=1%", Circuit(f=0.01, daily_R=3.0, streak=5, mdd=0.15)),
        ("현행 서킷 · f=2.4%", Circuit(f=a.f, daily_R=3.0*0.01/a.f, streak=5, mdd=0.15)),
        ("★ 재설계 · f=2.4%", Circuit(f=a.f, daily_R=5.0, streak=12, mdd=0.35)),
        ("재설계(보수) · f=2.4%", Circuit(f=a.f, daily_R=4.0, streak=10, mdd=0.30)),
        ("서킷 전부 끔 (상한 참고)", Circuit(f=a.f, daily_R=0, streak=0, mdd=0)),
    ]:
        r = simulate(d, c, resume_bars=10)
        fr = r["fires"]
        print(f"{name:30} {r['cagr']:+8.1f}% {r['mdd']:6.1f}% "
              f"{r['taken']:5d}/{len(d):<6d} {fr['daily']:8d} {fr['streak']:8d} "
              f"{fr['mdd']:6d}")
    print("\n  * 차단 대부분은 동시보유 3개 상한(정상 동작)이다 — 위 표의 발동 수는 서킷만 센 것.")


if __name__ == "__main__":
    main()

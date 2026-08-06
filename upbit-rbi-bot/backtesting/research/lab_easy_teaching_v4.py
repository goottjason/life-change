"""
easy_teaching 5차 후속 — **V4 단일 셀 정밀 검정**.

## 왜 이 파일이 있는가
`lab_easy_teaching_defs.py` 의 사전 등록 22셀 중 **V4(FVG 단독 진입존 + 확인봉 조건 제거)**
하나가 탐색 구간에서 Šidák 보정을 통과했다(1시간 5,228거래 gross +0.1136% t +3.30).
이는 1~4차에서 한 번도 나오지 않은 결과다 — V4 의 정의(FVG **자체를 진입 존**으로,
확인봉 요구 없이)는 이전 구현의 모집단 **밖**이었기 때문이다.

사전 등록 규칙: "탐색 구간에서 통과 셀이 나올 때만 홀드아웃을 연다." → 연다. **단 한 번.**

## 그 전에 반드시 걸러야 할 두 가지 착시
1. **상승장 드리프트** — 2024-07~2025-12 는 대체로 상승장이다. 아무 봉에서나 롱을 잡아도
   1시간봉 +48봉 보유 수익률이 +0.100% 다. 진입 신호의 정보량은 **초과분**으로만 말해야 한다.
2. **종목 간 동시성** — 14종목을 동시에 돌리면 거래가 독립이 아니다(암호화폐 횡단면 상관은
   매우 높다). 거래 단위 t 는 **과대평가**된다. 월 단위로 클러스터링해 다시 잰다.

실행: .venv/bin/python backtesting/research/lab_easy_teaching_v4.py
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from config import charter as C
from indicators import ta
from backtesting.research.data_cache import load
from backtesting.research import lab_easy_teaching_defs as D

TF = "1h"
WARM = D.WARM[TF]
V4 = D.CELLS[4]
V0 = D.CELLS[0]


def collect(defn: D.Defn, period: str) -> pd.DataFrame:
    rows = []
    for sym in C.VALIDATED_MARKETS:
        d5 = load(f"KRW-{sym}", "minute5")
        if d5 is None:
            continue
        df = d5.resample(TF).agg({"open": "first", "high": "max", "low": "min",
                                  "close": "last", "volume": "sum"}).dropna()
        if len(df) < WARM + 200:
            continue
        atr = ta.atr(df).to_numpy()
        mask = np.zeros(len(df), dtype=bool)
        mask[WARM:] = True
        mask &= (np.asarray(df.index < D.SPLIT) if period == "selection"
                 else np.asarray(df.index >= D.SPLIT))
        cost = C.FEE_ROUNDTRIP + C.VALIDATED_MARKETS.get(sym, 0.0025)
        ev = D.entry_events(df, defn, atr)
        out = simulate_with_ts(df, ev, cost, defn, mask)
        for r in out:
            r["market"] = sym
        rows += out
    return pd.DataFrame(rows)


def simulate_with_ts(df, events, cost, d, mask):
    """D.simulate 와 동일하되 진입 시각·무조건부 대조군을 함께 남긴다."""
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    n = len(df)
    out, busy = [], -1
    for i, stop, target in events:
        if i <= busy or i + 1 >= n or not mask[i]:
            continue
        entry = float(o[i + 1])
        half, parts, px, why, j = False, [], None, "", i + 1
        for j in range(i + 1, min(n, i + 1 + D.TIME_STOP + 1)):
            s = entry if half else stop
            if l[j] <= s:
                px, why = s, ("breakeven" if half else "stop")
                break
            if not half and h[j] >= target:
                half = True
                parts.append((target, D.PARTIAL_TP))
            if j - (i + 1) >= D.TIME_STOP:
                px, why = float(o[j]), "time"
                break
        if px is None:
            px, why, j = float(c[n - 1]), "eod", n - 1
        parts.append((px, 1.0 - sum(w for _, w in parts)))
        g = sum(w * (p / entry - 1) for p, w in parts)
        row = {"ts": df.index[i], "gross": g * 100, "net": (g - cost) * 100, "reason": why}
        for b in D.FWD_BARS:
            row[f"fwd{b}"] = (float(c[min(i + 1 + b, n - 1)]) / entry - 1) * 100
        out.append(row)
        busy = j
    return out


def baseline_fwd(period: str) -> dict[int, float]:
    """무조건부(모든 봉에서 롱) 전방 수익률 — 상승장 드리프트 대조군."""
    acc = defaultdict(list)
    for sym in C.VALIDATED_MARKETS:
        d5 = load(f"KRW-{sym}", "minute5")
        if d5 is None:
            continue
        df = d5.resample(TF).agg({"open": "first", "high": "max", "low": "min",
                                  "close": "last", "volume": "sum"}).dropna()
        if len(df) < WARM + 200:
            continue
        o, c = df["open"].to_numpy(), df["close"].to_numpy()
        n = len(df)
        m = np.zeros(n, dtype=bool); m[WARM:] = True
        m &= (np.asarray(df.index < D.SPLIT) if period == "selection"
              else np.asarray(df.index >= D.SPLIT))
        idx = np.nonzero(m)[0]
        for b in D.FWD_BARS:
            k = idx[idx + 1 + b < n]
            acc[b].append((c[k + 1 + b] / o[k + 1] - 1) * 100)
    return {b: float(np.concatenate(v).mean()) for b, v in acc.items()}


def cluster_t(d: pd.DataFrame, col: str, by: str = "M") -> tuple[float, int]:
    """월 클러스터 t — 같은 달의 거래는 서로 독립이 아니라고 본다(횡단면 상관 보정)."""
    g = d.groupby(d["ts"].dt.to_period(by))[col].mean()
    if len(g) < 2:
        return 0.0, len(g)
    return float(g.mean() / (g.std(ddof=1) / math.sqrt(len(g)))), len(g)


def describe(tag: str, d: pd.DataFrame, base: dict[int, float]) -> None:
    if d.empty:
        print(f"{tag}: 거래 0건")
        return
    g = d["gross"].to_numpy(); net = d["net"].to_numpy()
    t = g.mean() / (g.std(ddof=1) / math.sqrt(len(g)))
    ct, nm = cluster_t(d, "gross")
    w, lo = net[net > 0], net[net <= 0]
    pf = w.sum() / abs(lo.sum()) if lo.sum() else math.inf
    print(f"\n── {tag} ──")
    print(f"  거래 {len(d):,} · 승률 {(net > 0).mean()*100:.1f}% · PF {pf:.2f}")
    print(f"  gross {g.mean():+.4f}%  (거래단위 t {t:+.2f} · **월클러스터 t {ct:+.2f}**"
          f" · {nm}개월)")
    print(f"  net   {net.mean():+.4f}%   (왕복비용 ≈ 0.20~0.25%)")
    print(f"  [청산설계와 무관한 전방수익률 — 초과분이 진짜 정보량]")
    for b in D.FWD_BARS:
        v = d[f"fwd{b}"].to_numpy()
        ex = v.mean() - base[b]
        tb = (v - base[b]).mean() / (v.std(ddof=1) / math.sqrt(len(v)))
        print(f"    +{b:2d}봉 {v.mean():+.3f}%  − 기준선 {base[b]:+.3f}%  = "
              f"**초과 {ex:+.3f}%p** (t {tb:+.2f})")
    print(f"  종목별 gross 양수: ", end="")
    per = d.groupby("market")["gross"].mean()
    print(f"{(per > 0).sum()}/{len(per)}종목  "
          f"[{', '.join(f'{k}{v:+.2f}' for k, v in per.sort_values().items())}]")


def fixed_horizon(defn: D.Defn, period: str, hold: int) -> pd.DataFrame:
    """
    손절·익절 없이 **고정 N봉 보유** 후 청산. 청산 설계가 결과를 만드는지 분리한다.
    초과분(=정보량)이 아니라 **원시 수익률 − 실측 비용**이 실제로 버는 값이다.
    """
    rows = []
    for sym in C.VALIDATED_MARKETS:
        d5 = load(f"KRW-{sym}", "minute5")
        if d5 is None:
            continue
        df = d5.resample(TF).agg({"open": "first", "high": "max", "low": "min",
                                  "close": "last", "volume": "sum"}).dropna()
        if len(df) < WARM + 200:
            continue
        atr = ta.atr(df).to_numpy()
        n = len(df)
        m = np.zeros(n, dtype=bool); m[WARM:] = True
        m &= (np.asarray(df.index < D.SPLIT) if period == "selection"
              else np.asarray(df.index >= D.SPLIT))
        cost = (C.FEE_ROUNDTRIP + C.VALIDATED_MARKETS.get(sym, 0.0025)) * 100
        o, c = df["open"].to_numpy(), df["close"].to_numpy()
        busy = -1
        for i, _s, _t in D.entry_events(df, defn, atr):
            if i <= busy or i + 1 >= n or not m[i]:
                continue
            j = min(i + 1 + hold, n - 1)
            g = (c[j] / o[i + 1] - 1) * 100
            rows.append({"ts": df.index[i], "gross": g, "net": g - cost, "market": sym})
            busy = j
    return pd.DataFrame(rows)


def main() -> None:
    print("=" * 78)
    print("V4 정밀 검정 — FVG 단독 진입존 + 확인봉 조건 제거 (1시간봉)")
    print("사전 등록 22셀 중 유일하게 탐색구간 Šidák(|t|>3.04)을 통과한 셀")
    print("=" * 78)
    for period in ("selection", "holdout"):
        base = baseline_fwd(period)
        print(f"\n\n■■■ {period.upper()} 구간 ■■■")
        print(f"무조건부 기준선(모든 봉 롱): " +
              " · ".join(f"+{b}봉 {v:+.3f}%" for b, v in base.items()))
        describe(f"V4 (FVG 단독 + 확인제거)", collect(V4, period), base)
        describe(f"V0 (기준선 · 현행 정의)", collect(V0, period), base)

    print("\n\n" + "=" * 78)
    print("■ 고정 N봉 보유 청산 — 청산 설계를 빼고 신호만 본다")
    print("  초과분은 '정보가 있다'는 증거일 뿐, 실제로 버는 건 **원시 − 비용**이다.")
    print("=" * 78)
    for hold in D.FWD_BARS:
        print(f"\n[{hold}봉 보유]")
        for period in ("selection", "holdout"):
            d = fixed_horizon(V4, period, hold)
            if d.empty:
                continue
            net = d["net"].to_numpy(); g = d["gross"].to_numpy()
            t = net.mean() / (net.std(ddof=1) / math.sqrt(len(net)))
            ct, _ = cluster_t(d, "net")
            pm = d.groupby("market")["net"].mean()
            print(f"  {period:10} {len(d):5d}거래  gross {g.mean():+.4f}%  "
                  f"net {net.mean():+.4f}%  (거래t {t:+.2f} · 월클러스터t {ct:+.2f})  "
                  f"종목양수 {(pm > 0).sum()}/{len(pm)}")


if __name__ == "__main__":
    main()

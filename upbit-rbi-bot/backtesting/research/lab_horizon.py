"""
9차 — **"단타라서 어려운가? 스윙으로 늘리면 비용 영향이 줄어드는가"**.

## 질문의 산술
왕복비용은 **거래당 고정**이다(업비트 현물 0.22%, 선물 0.12%).
  목표 0.3% → 비용이 목표의 **73%**
  목표 3.0% → 비용이 목표의 **7%**
그러므로 "목표를 키우고 오래 들고 있으면 비용 영향이 준다"는 **산술적으로 옳다.**

## 그런데 진짜 질문은 따로 있다
보유를 늘리면 gross 는 당연히 커진다. 문제는 **그게 신호의 엣지가 커진 것인가, 아니면
그냥 시장에 더 오래 노출된 것(베타)인가**이다. 후자라면 신호는 무의미하다 —
같은 노출은 **그냥 사서 들고 있으면** 비용 한 번만 내고 얻을 수 있기 때문이다.

그래서 이 실험의 핵심 통제는 셋이다:
  ① **드리프트 대조군** — 같은 구간 아무 봉에서나 진입했을 때의 같은 기간 수익률
  ② **무작위 진입 대조군** — 신호 대신 무작위 시점, 같은 청산 규칙 (신호 vs 타이밍 분리)
  ③ **매수 후 보유(buy & hold)** — 전략이 이걸 못 이기면 존재 이유가 없다
그리고 **연율화**해서 본다. 거래당 기댓값만 보면 "거래를 줄이면 좋아진다"는 착시가 생긴다 —
회전율이 줄면 단위 시간당 버는 것도 같이 준다.

## 두 구간을 모두 본다 (진단 목적)
탐색(2024-07~2025-12, 상승장)과 홀드아웃(2025-12~2026-07, 하락장)을 **둘 다** 쓴다.
"보유를 늘리면 좋아지는가"는 전략 선택이 아니라 **구조 진단**이고, 상승장만 보면
반드시 "좋아진다"는 답이 나오게 되어 있기 때문이다. 그 대비가 이 실험의 답이다.

실행: .venv/bin/python backtesting/research/lab_horizon.py [--venue spot|futures]
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
from backtesting.research.data_cache import load as load_spot
from backtesting.research import lab_easy_teaching_defs as D
from backtesting.research import data_cache_futures as F

TF = "1h"
WARM = 260
SIGNAL = D.CELLS[4]        # FVG 진입존 — 8차까지 5가지 중 가장 나았던 신호
BARS_PER_YEAR = 24 * 365

# 보유 기간(1시간봉) — 8시간부터 2주까지
HORIZONS = [8, 24, 48, 96, 168, 336]
HZ_LABEL = {8: "8시간", 24: "1일", 48: "2일", 96: "4일", 168: "1주", 336: "2주"}
# 목표/손절 크기 스윕 (%) — 목표가 커지면 비용 비중이 준다는 가설의 직접 검정
TARGETS = [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (5.0, 5.0)]
MAX_HOLD_TP = 336          # 목표·손절 어느 쪽도 안 닿으면 2주에서 끊는다


def markets(venue: str):
    if venue == "spot":
        for sym in C.VALIDATED_MARKETS:
            d5 = load_spot(f"KRW-{sym}", "minute5")
            if d5 is None:
                continue
            df = d5.resample(TF).agg({"open": "first", "high": "max", "low": "min",
                                      "close": "last", "volume": "sum"}).dropna()
            cost = (C.FEE_ROUNDTRIP + C.VALIDATED_MARKETS.get(sym, 0.0025)) * 100
            yield sym, df, cost
    else:
        for sym in F.SYMBOLS:
            df = F.load(sym, "1h")
            if df is None:
                continue
            yield sym, df, 0.12          # 선물 taker 0.10% + 슬리피지 0.02%


def period_mask(df, period):
    m = np.zeros(len(df), dtype=bool)
    m[WARM:] = True
    return m & (np.asarray(df.index < D.SPLIT) if period == "selection"
                else np.asarray(df.index >= D.SPLIT))


def fixed_horizon(df, ev, mask, hold, cost):
    """진입 후 hold 봉 뒤 무조건 청산. 손절·익절 없음 = 신호와 보유기간만 남긴다."""
    o, c = df["open"].to_numpy(), df["close"].to_numpy()
    n = len(df)
    out, busy = [], -1
    for i, _s, _t in ev:
        if i <= busy or i + 1 >= n or not mask[i]:
            continue
        j = min(i + 1 + hold, n - 1)
        g = (c[j] / o[i + 1] - 1) * 100
        out.append({"ts": df.index[i], "gross": g, "net": g - cost, "bars": j - (i + 1)})
        busy = j
    return out


def tp_sl(df, ev, mask, tp, sl, cost):
    """목표 +tp% / 손절 −sl% 도달 시 청산, 둘 다 아니면 MAX_HOLD_TP 에서 종료."""
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    n = len(df)
    out, busy = [], -1
    for i, _s, _t in ev:
        if i <= busy or i + 1 >= n or not mask[i]:
            continue
        e = float(o[i + 1])
        up, dn = e * (1 + tp / 100), e * (1 - sl / 100)
        px, j = None, i + 1
        for j in range(i + 1, min(n, i + 1 + MAX_HOLD_TP + 1)):
            if l[j] <= dn:                       # 손절 우선(보수적)
                px = dn; break
            if h[j] >= up:
                px = up; break
        if px is None:
            px = float(c[j])
        g = (px / e - 1) * 100
        out.append({"ts": df.index[i], "gross": g, "net": g - cost, "bars": j - (i + 1)})
        busy = j
    return out


def summarize(rows, label):
    if len(rows) < 2:
        return None
    d = pd.DataFrame(rows)
    net = d["net"].to_numpy(); g = d["gross"].to_numpy()
    bars = d["bars"].to_numpy()
    mo = d.groupby(d["ts"].dt.to_period("M"))["net"].mean()
    ct = float(mo.mean() / (mo.std(ddof=1) / math.sqrt(len(mo)))) if len(mo) > 1 else 0.0
    # 연율화: 포지션 1개를 계속 굴린다고 가정 → 연간 회전수 = 8760 / 평균보유봉
    turns = BARS_PER_YEAR / max(bars.mean(), 1e-9)
    return {"label": label, "n": len(d), "gross": g.mean(), "net": net.mean(),
            "win": (net > 0).mean() * 100, "bars": bars.mean(), "ct": ct,
            "ann": net.mean() * turns, "turns": turns,
            "pos_mo": int((mo > 0).sum()), "n_mo": len(mo)}


def buy_hold(venue, period, hold):
    """대조군 ③ — 같은 구간, 아무 봉에서나 진입해 같은 기간 보유. 비용은 왕복 1회만."""
    acc = []
    for _sym, df, cost in markets(venue):
        if len(df) < WARM + 400:
            continue
        m = period_mask(df, period)
        o, c = df["open"].to_numpy(), df["close"].to_numpy()
        n = len(df)
        idx = np.nonzero(m)[0]
        idx = idx[idx + 1 + hold < n]
        acc.append((c[idx + 1 + hold] / o[idx + 1] - 1) * 100 - cost)
    return float(np.concatenate(acc).mean()) if acc else 0.0


def random_entry(venue, period, hold, seed=7):
    """대조군 ② — 신호 대신 무작위 시점. 신호가 타이밍에 기여하는지 분리한다."""
    rng = np.random.default_rng(seed)
    rows = []
    for _sym, df, cost in markets(venue):
        if len(df) < WARM + 400:
            continue
        m = period_mask(df, period)
        idx = np.nonzero(m)[0]
        if len(idx) < 50:
            continue
        n_take = max(10, len(idx) // max(hold, 1))
        pick = np.sort(rng.choice(idx, size=min(n_take, len(idx)), replace=False))
        ev = [(int(i), 0.0, 0.0) for i in pick]
        rows += fixed_horizon(df, ev, m, hold, cost)
    return summarize(rows, "무작위 진입")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", default="spot", choices=["spot", "futures"])
    args = ap.parse_args()
    v = args.venue
    cost_txt = "0.22~0.25%" if v == "spot" else "0.12%"
    print(f"[보유기간·목표크기 스윕 · {v} · 1시간봉 · 신호=FVG 진입존 · 왕복비용 {cost_txt}]")
    print("핵심 질문: 보유를 늘리면 좋아지는가 — 그게 신호 때문인가 시장 노출 때문인가?\n")

    # 신호 이벤트를 한 번만 계산해 재사용
    cache = []
    for sym, df, cost in markets(v):
        if len(df) < WARM + 400:
            continue
        atr = ta.atr(df).to_numpy()
        cache.append((sym, df, cost, D.entry_events(df, SIGNAL, atr)))
    print(f"신호 이벤트 확보: {len(cache)}종목\n")

    for period in ("selection", "holdout"):
        tag = "탐색(2024-07~2025-12, 상승장)" if period == "selection" \
              else "홀드아웃(2025-12~2026-07, 하락장)"
        print(f"■■■ {tag} ■■■")
        print(f"{'보유':>6} {'거래':>6} {'승률%':>6} {'gross%':>8} {'net%':>8} {'월t':>6} "
              f"{'연율%':>9} │ {'무작위net%':>10} {'매수보유net%':>12} {'초과%':>8}")
        print("-" * 104)
        for hz in HORIZONS:
            rows = []
            for _sym, df, cost, ev in cache:
                rows += fixed_horizon(df, ev, period_mask(df, period), hz, cost)
            s = summarize(rows, HZ_LABEL[hz])
            if not s:
                continue
            rnd = random_entry(v, period, hz)
            bh = buy_hold(v, period, hz)
            print(f"{HZ_LABEL[hz]:>6} {s['n']:6d} {s['win']:6.1f} {s['gross']:+8.3f} "
                  f"{s['net']:+8.3f} {s['ct']:+6.2f} {s['ann']:+9.1f} │ "
                  f"{rnd['net']:+10.3f} {bh:+12.3f} {s['net'] - bh:+8.3f}")
        print(f"\n{'목표/손절':>10} {'거래':>6} {'승률%':>6} {'평균보유':>8} {'net%':>8} "
              f"{'월t':>6} {'연율%':>9} │ {'비용/목표':>9}")
        print("-" * 84)
        for tp, sl in TARGETS:
            rows = []
            for _sym, df, cost, ev in cache:
                rows += tp_sl(df, ev, period_mask(df, period), tp, sl, cost)
            s = summarize(rows, f"±{tp}%")
            if not s:
                continue
            cost_ratio = (0.235 if v == "spot" else 0.12) / tp * 100
            print(f"{'±' + str(tp) + '%':>10} {s['n']:6d} {s['win']:6.1f} "
                  f"{s['bars']:8.0f} {s['net']:+8.3f} {s['ct']:+6.2f} {s['ann']:+9.1f} │ "
                  f"{cost_ratio:8.1f}%")
        print()


if __name__ == "__main__":
    main()

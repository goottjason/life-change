"""
easy_teaching 7차 — **5가지 매매법을 조합하면 되는가**.

## 질문
6차까지 각 매매법을 **단독으로** 쟀다. 원문은 "근거가 겹치는 자리일수록 신뢰도가 높다"고
반복한다 — 유동성 흡수 자리 + 구조물 + 오더블록/FVG 가 동시에 겹치는 자리.
그러면 조합하면 비용 벽을 넘는가?

## 넘어야 하는 산술 (먼저 못 박는다)
- 단일 기법 최대 엣지 = **FVG +0.161%p**(드리프트 초과, 홀드아웃 재현됨)
- 왕복비용 = **0.20~0.25%**
→ 조합은 엣지를 **+0.16 → +0.22%p 이상으로, 즉 40%+ 끌어올려야** 의미가 있다.
  동시에 §11 표본(100거래)을 남겨야 한다. 조합은 거래를 급감시키므로 이 둘은 상충한다.

## 조합의 성질 — 왜 기대가 낮은가 (그래도 측정한다)
6차 결과: 오더블록 +0.015%p · FVG **+0.161** · 추세선 −0.152 · 채널 −0.041 ·
페이크아웃/트랩 **−0.196**. **정보가 있는 건 FVG 하나뿐이고 둘은 음수다.**
정보 0인 신호로 교집합을 만들면 보통 0이 남고, **음수 정보와 교집합하면 깎인다.**
그러나 이것은 논증이지 측정이 아니다(2차의 "채널은 pivot_k 와 같은 축" 논증이 6차에서
틀린 것으로 드러났다) → 잰다.

## 설계 (4차 발견 3의 교훈: 전수 조합 탐색 = 과최적화 생산)
- 기저 신호는 **FVG 진입 존**(5차 V4 — 유일하게 정보가 있는 정의)으로 고정한다.
  거기에 나머지 근거를 **맥락 필터**로 얹는다. 조합의 축을 자유롭게 풀면 조합 폭발이다.
- **사전 등록 8조합 × 2TF = 16셀, 전부 보고.** Šidák α=0.05 → |t| > 2.97.
- 판정은 거래 단위 t 가 아니라 **월 클러스터 t** 와 **드리프트 초과분**으로 한다.
- 홀드아웃(2025-12-29~)은 탐색에서 통과 셀이 나올 때만 연다.

실행: .venv/bin/python backtesting/research/lab_easy_teaching_combo.py [--tf 1h|15min]
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from config import charter as C
from indicators import ta
from backtesting.research.data_cache import load
from backtesting.research import lab_easy_teaching_defs as D
from backtesting.research import lab_easy_teaching_structure as S

BASE = D.CELLS[4]        # V4 = FVG 단독 진입존 + 확인봉 제거 (유일하게 정보가 있는 정의)
K = S.PIVOT_K
NEAR_ATR = 0.5           # '겹친다'의 허용오차 = ATR 배수 (현행 CONFLUENCE_ATR_MULT 와 동일)
CH_LOW_FRAC = 0.34       # 채널 하단부(과매도 구간)의 정의 = 아래 1/3


@dataclass(frozen=True)
class Combo:
    label: str
    note: str
    need: tuple[str, ...] = ()     # 모두 만족해야 하는 근거
    any_n: int = 0                 # >0 이면 '근거 4개 중 N개 이상'


CELLS: list[Combo] = [
    Combo("K0 FVG 단독(기준선)", "6차에서 유일하게 정보가 있던 신호 — 비교 기준"),
    Combo("K1 FVG+오더블록", "원문의 '근거 중첩' — 두 타점 도구가 겹치는 자리", ("ob",)),
    Combo("K2 FVG+추세선", "3강 방향 필터: 유효한 상승 추세선 위/근처", ("tl",)),
    Combo("K3 FVG+채널하단", "4강: 과매도 구간(채널 아래 1/3)에서만", ("ch",)),
    Combo("K4 FVG+페이크아웃", "5강: 유동성 흡수(지지 스윕 후 되찾기) 직후", ("ft",)),
    Combo("K5 FVG+OB+추세선", "타점 2개 + 방향 1개 (3중)", ("ob", "tl")),
    Combo("K6 FVG+채널+페이크아웃", "원문 문구 그대로: 구조물 + 유동성 흡수 + FVG",
          ("ch", "ft")),
    Combo("K7 FVG+근거2개이상", "OB·추세선·채널·페이크아웃 중 2개 이상 동시 성립",
          any_n=2),
]

FLAGS = ("ob", "tl", "ch", "ft")


# ── 맥락 근거를 봉 단위 플래그로 ─────────────────────────────
def ob_flag(df, atr) -> np.ndarray:
    """그 봉에서 가격이 살아있는 상승형 오더블록 구간에 닿아 있는가."""
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    n = len(df)
    idx, bot, top, low = D.find_obs(o, h, l, c, BASE)
    out = np.zeros(n, dtype=bool)
    for f, b, t, lw in zip(idx, bot, top, low):
        hi = min(n - 1, f + D.LOOKBACK)
        for i in range(f + 1, hi + 1):
            if l[i] < lw:                      # 무효화되면 그 존은 죽는다
                break
            if l[i] <= t + (atr[i] * NEAR_ATR if np.isfinite(atr[i]) else 0):
                out[i] = True
    return out


def tl_flag(df, atr, k: int = K) -> np.ndarray:
    """유효한 **상승** 추세선이 있고, 가격이 그 선 위 1 ATR 이내인가(=선 근처)."""
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    n = len(df)
    plo = S.pivots(l, k, high=False)
    out = np.zeros(n, dtype=bool)
    for i in range(2 * k + 2, n):
        a = float(atr[i]) if np.isfinite(atr[i]) else 0.0
        if a <= 0:
            continue
        pair = S.last_two_confirmed(plo, i, k)
        if pair is None:
            continue
        j1, j2 = pair
        if j2 - j1 < k or i - j1 > S.MAX_ANCHOR_AGE:
            continue
        slope = (l[j2] - l[j1]) / (j2 - j1)
        if slope <= 0:
            continue
        xs = np.arange(j2 + 1, i)
        if xs.size and (c[xs] < S.line_at(xs, j1, l[j1], slope)).any():
            continue                            # 이미 깨진 선
        lv = S.line_at(i, j1, l[j1], slope)
        if lv <= c[i] <= lv + a:                # 선 위, 1 ATR 이내
            out[i] = True
    return out


def ch_flag(df, atr, k: int = K) -> np.ndarray:
    """유효한 채널이 있고, 가격이 **하단부(아래 1/3)** = 과매도 구간인가."""
    h = df["high"].to_numpy(); l = df["low"].to_numpy(); c = df["close"].to_numpy()
    n = len(df)
    plo = S.pivots(l, k, high=False)
    phi = S.pivots(h, k, high=True)
    out = np.zeros(n, dtype=bool)
    for i in range(2 * k + 2, n):
        a = float(atr[i]) if np.isfinite(atr[i]) else 0.0
        if a <= 0:
            continue
        cands = []
        pl = S.last_two_confirmed(plo, i, k)
        if pl and pl[1] - pl[0] >= k and i - pl[0] <= S.MAX_ANCHOR_AGE:
            j1, j2 = pl
            s = (l[j2] - l[j1]) / (j2 - j1)
            if s > 0:
                xs = np.arange(j1, i + 1)
                base = S.line_at(xs, j1, l[j1], s)
                off = float((h[j1:i + 1] - base).max())
                if off >= a * S.MIN_WIDTH_ATR:
                    lo_v = S.line_at(i, j1, l[j1], s)
                    cands.append((lo_v, lo_v + off))
        ph = S.last_two_confirmed(phi, i, k)
        if ph and ph[1] - ph[0] >= k and i - ph[0] <= S.MAX_ANCHOR_AGE:
            j1, j2 = ph
            s = (h[j2] - h[j1]) / (j2 - j1)
            if s < 0:
                xs = np.arange(j1, i + 1)
                base = S.line_at(xs, j1, h[j1], s)
                off = float((base - l[j1:i + 1]).max())
                if off >= a * S.MIN_WIDTH_ATR:
                    up_v = S.line_at(i, j1, h[j1], s)
                    cands.append((up_v - off, up_v))
        if not cands:
            continue
        lo_v, up_v = min(cands, key=lambda z: min(abs(c[i] - z[0]), abs(c[i] - z[1])))
        if up_v <= lo_v:
            continue
        pos = (c[i] - lo_v) / (up_v - lo_v)     # 0=하단, 1=상단
        if -0.2 <= pos <= CH_LOW_FRAC:
            out[i] = True
    return out


def ft_flag(df, atr, k: int = K) -> np.ndarray:
    """그 봉에서 '지지 스윕 후 되찾기'가 완성됐는가(5강)."""
    n = len(df)
    out = np.zeros(n, dtype=bool)
    for i, _s, _t in S.fakeout_signals(df, False, atr, k):
        out[i] = True
    return out


def flags_for(df, atr) -> dict[str, np.ndarray]:
    return {"ob": ob_flag(df, atr), "tl": tl_flag(df, atr),
            "ch": ch_flag(df, atr), "ft": ft_flag(df, atr)}


# ── 실행 ─────────────────────────────────────────────────────
def run_cell(combo: Combo, tf: str, period: str) -> pd.DataFrame:
    warm = D.WARM[tf]
    rows = []
    for sym in C.VALIDATED_MARKETS:
        d5 = load(f"KRW-{sym}", "minute5")
        if d5 is None:
            continue
        df = d5.resample(tf).agg({"open": "first", "high": "max", "low": "min",
                                  "close": "last", "volume": "sum"}).dropna()
        if len(df) < warm + 200:
            continue
        atr = ta.atr(df).to_numpy()
        mask = np.zeros(len(df), dtype=bool)
        mask[warm:] = True
        mask &= (np.asarray(df.index < D.SPLIT) if period == "selection"
                 else np.asarray(df.index >= D.SPLIT))
        fl = _FLAG_CACHE.get((sym, tf))
        if fl is None:
            fl = flags_for(df, atr)
            _FLAG_CACHE[(sym, tf)] = fl
        ev = _EV_CACHE.get((sym, tf))
        if ev is None:
            ev = D.entry_events(df, BASE, atr)
            _EV_CACHE[(sym, tf)] = ev
        if combo.any_n:
            keep = [e for e in ev
                    if sum(int(fl[f][e[0]]) for f in FLAGS) >= combo.any_n]
        elif combo.need:
            keep = [e for e in ev if all(fl[f][e[0]] for f in combo.need)]
        else:
            keep = ev
        cost = C.FEE_ROUNDTRIP + C.VALIDATED_MARKETS.get(sym, 0.0025)
        r = S.run_market(df, keep, cost, mask)
        for x in r:
            x["market"] = sym
        rows += r
    return pd.DataFrame(rows)


_FLAG_CACHE: dict = {}
_EV_CACHE: dict = {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--period", default="selection", choices=["selection", "holdout"])
    args = ap.parse_args()

    base = S.baseline_fwd(args.tf, args.period)
    print(f"[근거 조합 검정 · {args.tf} · {args.period} 구간 · 롱 전용]")
    print(f"기저 신호 = FVG 진입존(5차 V4). 사전 등록 8조합 × 2TF = 16셀 → |t| > 2.97")
    print(f"무조건부 드리프트: " + " · ".join(f"+{b}봉 {v:+.3f}%" for b, v in base.items()))
    print(f"★ 넘어야 할 선: 드리프트 초과 **+0.22%p**(왕복비용) · 표본 100거래\n")
    hdr = (f"{'셀':24} {'거래':>6} {'승률%':>6} {'PF':>5} {'gross%':>8} "
           f"{'거래t':>6} {'월t':>6} {'net%':>8} {'+24봉초과':>9} {'종목+':>6}")
    print(hdr); print("-" * len(hdr))
    out = []
    for cb in CELLS:
        d = run_cell(cb, args.tf, args.period)
        if d.empty or len(d) < 2:
            print(f"{cb.label:24} {len(d):6d}  — 표본 없음")
            out.append((cb, None))
            continue
        g = d["gross"].to_numpy(); net = d["net"].to_numpy()
        t = g.mean() / (g.std(ddof=1) / math.sqrt(len(g)))
        ct = S.cluster_t(d, "gross")
        w, lo = net[net > 0], net[net <= 0]
        pf = w.sum() / abs(lo.sum()) if lo.sum() else math.inf
        ex = d["fwd24"].mean() - base[24]
        pm = d.groupby("market")["gross"].mean()
        print(f"{cb.label:24} {len(d):6d} {(net > 0).mean()*100:6.1f} {pf:5.2f} "
              f"{g.mean():+8.4f} {t:+6.2f} {ct:+6.2f} {net.mean():+8.4f} "
              f"{ex:+9.3f} {(pm > 0).sum():4d}/{len(pm)}")
        out.append((cb, {"n": len(d), "g": g.mean(), "ct": ct, "net": net.mean(), "ex": ex}))

    print("\n[판정] 표본 ≥100 · gross > 0 · 월클러스터 t > 2.97")
    hits = [(c_, r) for c_, r in out
            if r and r["n"] >= 100 and r["g"] > 0 and r["ct"] > 2.97]
    if not hits:
        print("  없음 — 조합으로 비용 벽을 넘는 셀이 없다.")
    for c_, r in hits:
        print(f"  ★ {c_.label}: gross {r['g']:+.4f}% 월t {r['ct']:+.2f} net {r['net']:+.4f}%")

    k0 = next((r for c_, r in out if c_.label.startswith("K0")), None)
    if k0:
        print(f"\n[조합이 기준선(FVG 단독)을 개선했는가]  기준선 초과 {k0['ex']:+.3f}%p")
        for c_, r in out:
            if r is None or c_.label.startswith("K0"):
                continue
            d_ex = r["ex"] - k0["ex"]
            d_n = r["n"] / k0["n"] * 100
            print(f"  {c_.label:24} 초과 {r['ex']:+.3f}%p ({d_ex:+.3f}%p) · "
                  f"표본 {d_n:5.1f}% 로 감소")
    print("\n각 셀의 근거:")
    for cb in CELLS:
        print(f"  {cb.label:24} {cb.note}")


if __name__ == "__main__":
    main()

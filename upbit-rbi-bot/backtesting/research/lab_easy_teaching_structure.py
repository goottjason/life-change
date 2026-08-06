"""
easy_teaching 6차 — **추세선(3강)·채널(4강) 최초 구현 및 5가지 매매법 일괄 검정**.

## 왜 이 파일이 있는가
5차까지 측정한 것은 오더블록(1강)·FVG(2강)·페이크아웃/트랩(5강)뿐이다.
**추세선과 채널은 한 번도 구현된 적이 없다.** 2차 README 는 "추세선·채널은 스윙 저점의
상위 개념이라 pivot_k 축이 사실상 같은 축을 훑었다"고 적었지만 그건 **논증이지 측정이
아니다.** 채널은 평행선·과매수/과매도 예측이라 단일 스윙 저점과 다른 대상일 수 있다.

여기서 둘을 실제로 작도해 재고, 앞서 잰 세 가지도 **같은 통제**로 다시 재서
5가지를 하나의 표에 놓는다.

## 통제 (5차에서 배운 것)
1. **무조건부 드리프트 대조군** — 2024~2025 는 상승장이다. 아무 봉에서나 롱을 잡아도
   1시간봉 48봉 보유가 +0.100%. 정보량은 **초과분**으로만 말한다.
2. **월 클러스터 t** — 14종목 동시 실행이라 거래는 독립이 아니다. 거래 단위 t 는
   과대평가된다(5차 V4: 거래 t +3.30 → 월클러스터 t +1.92).
3. **홀드아웃(2025-12-29~)은 탐색에서 통과 셀이 나올 때만** 연다.
4. 사전 등록 8셀 × 2TF = 16셀, **전부 보고**. Šidák α=0.05 → |t| > 2.97.

## 롱 전용이라는 제약 (중요)
업비트 현물이므로 **매수만** 가능하다. 원문의 양방향 규칙 중 롱이 되는 쪽만 구현한다:
  - 추세 추종  : **상승** 추세선 터치 → 매수
  - 추세 반전  : **하락** 추세선 상방 돌파 후 리테스트 → 매수
  - 채널 역추세: 채널 **하단**(과매도 구간) 터치 → 매수
  - 채널 추세  : 채널 **상단** 상방 돌파 후 리테스트 → 매수 (S/R Flip)
  - 채널 돌파  : 채널 상단 돌파 즉시 추격 매수
숏 쪽 기회를 통째로 버리는 것은 이 거래소의 구조적 제약이며, 결과 해석에 반영해야 한다.

## 미래참조 방지
피벗은 **좌우 k봉**을 봐야 확정되므로, 봉 i 에서는 **i−k 이전에 확정된 피벗만** 쓴다.
채널 폭·직전 고점 등은 전부 [앵커, i] 구간 데이터만 쓴다.

실행: .venv/bin/python backtesting/research/lab_easy_teaching_structure.py [--tf 1h]
      [--period selection|holdout]
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

SPLIT = D.SPLIT
WARM = D.WARM
TIME_STOP = D.TIME_STOP
PARTIAL_TP = D.PARTIAL_TP
FWD_BARS = D.FWD_BARS

PIVOT_K = 3          # 기존 strategies/easy_teaching.PIVOT_K 와 동일 — 탐색으로 고르지 않는다
MIN_WIDTH_ATR = 1.0  # 채널 폭이 ATR 미만이면 '채널'로 보지 않는다(퇴화 방지)
RETEST_BARS = 10     # 돌파 후 리테스트를 기다리는 최대 봉 수
MAX_STOP_PCT = 5.0   # 손절거리 상한(§7 MAX_STRUCT_STOP_RATIO 과 동일 취지). 자리로 안 본다
MAX_ANCHOR_AGE = 200  # 앵커 피벗이 이보다 오래되면 그 선/채널은 쓰지 않는다(계산량·유효성)


@dataclass(frozen=True)
class Setup:
    label: str
    note: str
    kind: str          # tl_follow | tl_reversal | ch_counter | ch_trend | ch_break | fakeout
    confirm: bool = True     # 확인봉 양봉 마감을 요구하는가


CELLS: list[Setup] = [
    Setup("TL1 추세추종(확인봉)", "3강: 상승 추세선 터치 + 하위TF 반전 확인 후 진입",
          "tl_follow", confirm=True),
    Setup("TL2 추세추종(확인없음)", "3강 원칙: '라인을 터치할 때 진입한다'",
          "tl_follow", confirm=False),
    Setup("TL3 추세반전(리테스트)", "3강: 하락 추세선 붕괴 후 리테스트에서 역추세 진입",
          "tl_reversal", confirm=False),
    Setup("CH1 채널역추세(확인봉)", "4강: 4포인트(과매도) + 하위TF 반전 구조물 확인",
          "ch_counter", confirm=True),
    Setup("CH2 채널역추세(확인없음)", "4강 원칙: 과매도 구간에서 역추세 진입",
          "ch_counter", confirm=False),
    Setup("CH3 채널추세(S/R Flip)", "4강: 채널 상단 돌파 → 지지로 전환 → 리테스트 진입",
          "ch_trend", confirm=False),
    Setup("CH4 채널돌파(추격)", "4강: '채널이 뚫리면 추격 매매'", "ch_break", confirm=False),
    Setup("FT1 페이크아웃/트랩", "5강: 지지 스윕 후 되찾기 (2차 구현 재측정)",
          "fakeout", confirm=True),
]


# ── 피벗 ─────────────────────────────────────────────────────
def pivots(arr: np.ndarray, k: int, high: bool) -> np.ndarray:
    """좌우 k봉보다 극단이면 피벗. 반환 = 피벗 인덱스 배열(오름차순)."""
    n = len(arr)
    if n < 2 * k + 1:
        return np.empty(0, dtype=int)
    j = np.arange(k, n - k)
    ok = np.ones(len(j), dtype=bool)
    for d_ in range(1, k + 1):
        if high:
            ok &= (arr[j] >= arr[j - d_]) & (arr[j] >= arr[j + d_])
        else:
            ok &= (arr[j] <= arr[j - d_]) & (arr[j] <= arr[j + d_])
    return j[ok].astype(int)


def last_two_confirmed(piv: np.ndarray, i: int, k: int) -> tuple[int, int] | None:
    """봉 i 시점에 **확정된**(i−k 이전) 피벗 중 최근 2개. 미래참조 방지의 핵심."""
    m = piv[piv <= i - k]
    if len(m) < 2:
        return None
    return int(m[-2]), int(m[-1])


# ── 구조물 작도 ──────────────────────────────────────────────
def line_at(x: float, j1: int, v1: float, slope: float) -> float:
    return v1 + slope * (x - j1)


def trendline_signals(df, kind: str, confirm: bool, atr: np.ndarray, k: int = PIVOT_K):
    """
    tl_follow  : 상승 추세선(피벗 저점 2개, 기울기>0) 터치 → 매수
    tl_reversal: 하락 추세선(피벗 고점 2개, 기울기<0) 상방 봉마감 돌파 → 리테스트 → 매수
    """
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    n = len(df)
    plo = pivots(l, k, high=False)
    phi = pivots(h, k, high=True)
    ev = []
    broke_at: dict[tuple[int, int], int] = {}     # 하락선이 돌파된 봉 기록
    for i in range(2 * k + 2, n - 1):
        a = float(atr[i]) if np.isfinite(atr[i]) else 0.0
        if a <= 0:
            continue
        if kind == "tl_follow":
            pair = last_two_confirmed(plo, i, k)
            if pair is None:
                continue
            j1, j2 = pair
            if j2 - j1 < k or i - j1 > MAX_ANCHOR_AGE:
                continue
            slope = (l[j2] - l[j1]) / (j2 - j1)
            if slope <= 0:
                continue                                   # 상승 추세선만
            # 선이 아직 유효한가 — j2 이후 **종가**가 선 아래로 마감한 적이 없어야 한다
            xs = np.arange(j2 + 1, i)
            if xs.size and (c[xs] < line_at(xs, j1, l[j1], slope)).any():
                continue
            lv = line_at(i, j1, l[j1], slope)
            if not (l[i] <= lv):                           # 터치
                continue
            if c[i] < lv:                                  # 종가가 선 아래면 이탈이다
                continue
            if confirm and not (c[i] > o[i]):
                continue
            stop = float(min(l[i], lv))
            target = float(h[j1:i + 1].max())
            ev.append((i, stop, target))
        else:                                              # tl_reversal
            pair = last_two_confirmed(phi, i, k)
            if pair is None:
                continue
            j1, j2 = pair
            if j2 - j1 < k or i - j1 > MAX_ANCHOR_AGE:
                continue
            slope = (h[j2] - h[j1]) / (j2 - j1)
            if slope >= 0:
                continue                                   # 하락 추세선만
            lv = line_at(i, j1, h[j1], slope)
            key = (j1, j2)
            if key not in broke_at:
                if c[i] > lv:                              # 상방 봉마감 돌파
                    broke_at[key] = i
                continue
            b = broke_at[key]
            if i - b > RETEST_BARS:
                continue
            if not (l[i] <= lv and c[i] > lv):             # 리테스트 후 선 위 마감
                continue
            if confirm and not (c[i] > o[i]):
                continue
            stop = float(min(l[i], l[b:i + 1].min()))
            target = float(h[j1:i + 1].max())
            if target <= c[i]:
                target = float(c[i] + (c[i] - stop))       # 목표가 없으면 1:1
            ev.append((i, stop, target))
    return ev


def channel_signals(df, kind: str, confirm: bool, atr: np.ndarray, k: int = PIVOT_K):
    """
    채널 = 평행한 두 선. 원문: 메인 추세선 + 반대편에 맞춘 서브 추세선 + 미들라인.
    작도에는 3개의 점이 필요하다 — 앵커 피벗 2개 + 반대편 최대 이탈점 1개.
      ch_counter: 채널 **하단** 터치(과매도, 4포인트) → 매수
      ch_trend  : 채널 **상단** 상방 봉마감 돌파 → 리테스트(지지로 전환) → 매수
      ch_break  : 채널 상단 돌파 즉시 추격 매수
    """
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    n = len(df)
    plo = pivots(l, k, high=False)
    phi = pivots(h, k, high=True)
    ev = []
    broke_at: dict[tuple, int] = {}
    for i in range(2 * k + 2, n - 1):
        a = float(atr[i]) if np.isfinite(atr[i]) else 0.0
        if a <= 0:
            continue
        # 상승 채널(저점 앵커)과 하락 채널(고점 앵커)을 둘 다 시도하고, 가까운 쪽을 쓴다
        cands = []
        pl = last_two_confirmed(plo, i, k)
        if pl and pl[1] - pl[0] >= k and i - pl[0] <= MAX_ANCHOR_AGE:
            j1, j2 = pl
            s = (l[j2] - l[j1]) / (j2 - j1)
            if s > 0:                                       # 상승 채널
                xs = np.arange(j1, i + 1)
                base = line_at(xs, j1, l[j1], s)
                off = float((h[j1:i + 1] - base).max())     # 3번째 점 = 최대 이탈 고점
                if off >= a * MIN_WIDTH_ATR:
                    lo_v = line_at(i, j1, l[j1], s)
                    cands.append(("up", lo_v, lo_v + off, j1, off))
        ph = last_two_confirmed(phi, i, k)
        if ph and ph[1] - ph[0] >= k and i - ph[0] <= MAX_ANCHOR_AGE:
            j1, j2 = ph
            s = (h[j2] - h[j1]) / (j2 - j1)
            if s < 0:                                       # 하락 채널
                xs = np.arange(j1, i + 1)
                base = line_at(xs, j1, h[j1], s)
                off = float((base - l[j1:i + 1]).max())
                if off >= a * MIN_WIDTH_ATR:
                    up_v = line_at(i, j1, h[j1], s)
                    cands.append(("down", up_v - off, up_v, j1, off))
        if not cands:
            continue
        # 가격에 더 가까운(=더 관련 있는) 채널 하나만 쓴다
        tag, lo_v, up_v, anchor, width = min(
            cands, key=lambda z: min(abs(c[i] - z[1]), abs(c[i] - z[2])))

        if kind == "ch_counter":
            if not (l[i] <= lo_v):                          # 하단 터치 = 과매도 구간
                continue
            if c[i] < lo_v:                                 # 이탈 마감이면 손절 자리다
                continue
            if confirm and not (c[i] > o[i]):
                continue
            stop = float(min(l[i], lo_v - 0.25 * width))
            target = float(up_v)                            # 원문: 채널 반대편이 1차 익절
            ev.append((i, stop, target))
        elif kind in ("ch_trend", "ch_break"):
            key = (tag, anchor, kind)
            if key not in broke_at:
                if c[i] > up_v:
                    broke_at[key] = i
                    if kind == "ch_break":                  # 돌파 즉시 추격
                        stop = float(min(l[i], up_v - 0.25 * width))
                        target = float(c[i] + width)        # 확장 채널 폭만큼
                        ev.append((i, stop, target))
                continue
            if kind == "ch_break":
                continue
            b = broke_at[key]
            if i - b > RETEST_BARS:
                continue
            if not (l[i] <= up_v and c[i] > up_v):          # 리테스트 후 지지 확인
                continue
            if confirm and not (c[i] > o[i]):
                continue
            stop = float(min(l[i], up_v - 0.25 * width))
            target = float(c[i] + width)
            ev.append((i, stop, target))
    return ev


def fakeout_signals(df, confirm: bool, atr: np.ndarray, k: int = PIVOT_K):
    """5강 — 지지 스윕 후 되찾기. 2차 구현(strategies/easy_teaching._find_fakeout)과 같은 규칙."""
    from strategies.easy_teaching import MAX_SWEEP_BARS, MIN_SWEEP_ATR_MULT
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    n = len(df)
    plo = pivots(l, k, high=False)
    ev = []
    for i in range(2 * k + 2, n - 1):
        a = float(atr[i]) if np.isfinite(atr[i]) else 0.0
        if a <= 0 or (confirm and c[i] <= o[i]):
            continue
        m = plo[plo <= i - k]
        best = None
        for j in m[::-1][:12]:                              # 최근 지지부터
            level = float(l[j])
            if c[i] <= level:
                continue
            seg = l[j + 1:i + 1]
            below = seg < level
            if not below.any():
                continue
            first = int(below.argmax())
            if (len(below) - 1) - first > MAX_SWEEP_BARS:
                continue
            sweep_low = float(seg[first:].min())
            if (level - sweep_low) < a * MIN_SWEEP_ATR_MULT:
                continue
            cand = (level, sweep_low, float(h[j:i + 1].max()))
            if best is None or cand[0] > best[0]:
                best = cand
        if best:
            ev.append((i, best[1], best[2]))
    return ev


def signals_for(df, s: Setup, atr: np.ndarray, k: int = PIVOT_K):
    if s.kind.startswith("tl_"):
        return trendline_signals(df, s.kind, s.confirm, atr, k)
    if s.kind.startswith("ch_"):
        return channel_signals(df, s.kind, s.confirm, atr, k)
    return fakeout_signals(df, s.confirm, atr, k)


# ── 시뮬레이션 ───────────────────────────────────────────────
def run_market(df, ev, cost, mask):
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    n = len(df)
    out, busy = [], -1
    for i, stop, target in ev:
        if i <= busy or i + 1 >= n or not mask[i]:
            continue
        entry = float(o[i + 1])
        if not (stop < entry < target):
            continue
        if (entry - stop) / entry * 100 > MAX_STOP_PCT:
            continue
        half, parts, px, why, j = False, [], None, "", i + 1
        for j in range(i + 1, min(n, i + 1 + TIME_STOP + 1)):
            sv = entry if half else stop
            if l[j] <= sv:
                px, why = sv, ("breakeven" if half else "stop")
                break
            if not half and h[j] >= target:
                half = True
                parts.append((target, PARTIAL_TP))
            if j - (i + 1) >= TIME_STOP:
                px, why = float(o[j]), "time"
                break
        if px is None:
            px, why, j = float(c[n - 1]), "eod", n - 1
        parts.append((px, 1.0 - sum(w for _, w in parts)))
        g = sum(w * (p / entry - 1) for p, w in parts)
        row = {"ts": df.index[i], "gross": g * 100, "net": (g - cost) * 100, "reason": why}
        for b in FWD_BARS:
            row[f"fwd{b}"] = (float(c[min(i + 1 + b, n - 1)]) / entry - 1) * 100
        out.append(row)
        busy = j
    return out


def run_cell(s: Setup, tf: str, period: str, k: int = PIVOT_K) -> pd.DataFrame:
    warm = WARM[tf]
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
        mask &= (np.asarray(df.index < SPLIT) if period == "selection"
                 else np.asarray(df.index >= SPLIT))
        cost = C.FEE_ROUNDTRIP + C.VALIDATED_MARKETS.get(sym, 0.0025)
        r = run_market(df, signals_for(df, s, atr, k), cost, mask)
        for x in r:
            x["market"] = sym
        rows += r
    return pd.DataFrame(rows)


def baseline_fwd(tf: str, period: str) -> dict[int, float]:
    acc = defaultdict(list)
    warm = WARM[tf]
    for sym in C.VALIDATED_MARKETS:
        d5 = load(f"KRW-{sym}", "minute5")
        if d5 is None:
            continue
        df = d5.resample(tf).agg({"open": "first", "high": "max", "low": "min",
                                  "close": "last", "volume": "sum"}).dropna()
        if len(df) < warm + 200:
            continue
        o, c = df["open"].to_numpy(), df["close"].to_numpy()
        n = len(df)
        m = np.zeros(n, dtype=bool); m[warm:] = True
        m &= (np.asarray(df.index < SPLIT) if period == "selection"
              else np.asarray(df.index >= SPLIT))
        idx = np.nonzero(m)[0]
        for b in FWD_BARS:
            kk = idx[idx + 1 + b < n]
            acc[b].append((c[kk + 1 + b] / o[kk + 1] - 1) * 100)
    return {b: float(np.concatenate(v).mean()) for b, v in acc.items()}


def cluster_t(d: pd.DataFrame, col: str) -> float:
    g = d.groupby(d["ts"].dt.to_period("M"))[col].mean()
    if len(g) < 2:
        return 0.0
    return float(g.mean() / (g.std(ddof=1) / math.sqrt(len(g))))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--period", default="selection", choices=["selection", "holdout"])
    ap.add_argument("--pivot-k", type=int, default=PIVOT_K,
                    help="구조물 작도의 피벗 유의성. 원문의 '의미 있는 구조물'을 "
                         "몇 봉 기준으로 볼 것인가 — 작도 방식 자체가 정의다.")
    args = ap.parse_args()

    base = baseline_fwd(args.tf, args.period)
    print(f"[구조물 매매법 검정 · {args.tf} · {args.period} 구간 · 롱 전용 · pivot_k={args.pivot_k}]")
    print(f"사전 등록 8셀 × 2TF = 16셀 → Šidák 보정 |t| > 2.97")
    print(f"무조건부 드리프트 기준선: " +
          " · ".join(f"+{b}봉 {v:+.3f}%" for b, v in base.items()))
    print(f"왕복비용 ≈ 0.20~0.25%\n")
    hdr = (f"{'셀':24} {'거래':>6} {'승률%':>6} {'PF':>5} {'gross%':>8} "
           f"{'거래t':>6} {'월t':>6} {'net%':>8} {'+24봉초과':>9} {'종목+':>6}")
    print(hdr); print("-" * len(hdr))
    results = []
    for s in CELLS:
        d = run_cell(s, args.tf, args.period, args.pivot_k)
        if d.empty:
            print(f"{s.label:24} {'거래 0건':>6}")
            results.append((s, None))
            continue
        g = d["gross"].to_numpy(); net = d["net"].to_numpy()
        t = g.mean() / (g.std(ddof=1) / math.sqrt(len(g))) if len(g) > 1 else 0.0
        ct = cluster_t(d, "gross")
        w, lo = net[net > 0], net[net <= 0]
        pf = w.sum() / abs(lo.sum()) if lo.sum() else math.inf
        ex24 = d["fwd24"].mean() - base[24]
        pm = d.groupby("market")["gross"].mean()
        print(f"{s.label:24} {len(d):6d} {(net > 0).mean()*100:6.1f} {pf:5.2f} "
              f"{g.mean():+8.4f} {t:+6.2f} {ct:+6.2f} {net.mean():+8.4f} "
              f"{ex24:+9.3f} {(pm > 0).sum():4d}/{len(pm)}")
        results.append((s, {"g": g.mean(), "t": t, "ct": ct, "net": net.mean(),
                            "n": len(d), "ex24": ex24}))

    print("\n[판정] gross > 0 이고 **월클러스터 t > 2.97** 인 셀")
    hits = [(s, r) for s, r in results if r and r["g"] > 0 and r["ct"] > 2.97]
    if not hits:
        print("  없음.")
        near = [(s, r) for s, r in results if r and r["g"] > 0 and r["ct"] > 2.0]
        for s, r in near:
            print(f"  (참고) {s.label}: gross {r['g']:+.4f}% 월t {r['ct']:+.2f} "
                  f"— 무보정 2.0은 넘지만 보정 임계값 미달")
    for s, r in hits:
        print(f"  ★ {s.label}: gross {r['g']:+.4f}% 월t {r['ct']:+.2f} → "
              f"net {r['net']:+.4f}% ({'비용 초과 ✅' if r['net'] > 0 else '비용 미달 ❌'})")
    print("\n각 셀의 근거:")
    for s in CELLS:
        print(f"  {s.label:24} {s.note}")


if __name__ == "__main__":
    main()

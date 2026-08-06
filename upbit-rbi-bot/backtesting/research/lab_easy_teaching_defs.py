"""
easy_teaching 5차 — **"정의를 다르게 하면 결과가 뒤집히는가"** 에 대한 답.

## 왜 또 하는가
1~4차는 전부 **하나의 렌더링**만 쟀다. 4차가 "필터를 전부 끄고" 모집단을 모았다지만,
껐던 것은 `require_trend / use_rr_gate / min_ob_body_mult / require_mtf_overlap` 뿐이고
**진입 존의 정의 자체는 고정**돼 있었다:
  - `confluence_mode="ob_fvg"` → 진입 존은 **항상 오더블록**, FVG 는 인접 근거일 뿐
  - 확인봉 양봉 마감 요구는 `strategies/easy_teaching.py` 에 **하드코딩** (스펙 축이 아니다)
  - 오더블록 존 = 감싸인 음봉의 **몸통**, 감쌈은 **완전 감쌈**만

그런데 원저자의 2025-12-16 영상(docs/strategies/easy-teaching-man/2025. 12. 16. …)은
이와 다르게 말한다:
  - **"FVG가 생긴 구간에 진입하는 걸 원칙으로 한다"** — FVG 는 근거가 아니라 **진입 존**이다
  - **"오더블록이 생긴 구간에 진입을 합니다"** — 겹침(confluence)을 진입 요건으로 걸지 않는다
  - 확인봉 양봉 마감 요구는 **원문에 없다**(우리 구현이 실매매 실패 후 추가한 조건이다)
  - **"연속된 3개 캔들이 모두 크기가 비슷하면 FVG 로서의 역할을 못 할 가능성이 높다"** — 미구현
  - 익절 기준에 **"반대 오더블록의 출현"** 이 있다 — 미구현

즉 4차의 "gross 엣지 = 0" 은 **그 정의 하나에 대한 답**이고, 위 항목들은 그 모집단 밖이다.
이 실험은 정의 축을 바꿨을 때 **진입 신호의 방향성 정보 자체가 달라지는가**를 잰다.

## 설계 원칙 (4차 발견 3의 교훈)
전수 탐색은 과최적화만 만든다(15분봉 train↔test 상관 **−0.281**). 그래서:
  - **사전 등록한 11개 셀만** 돌리고 **전부 보고**한다. 사후에 셀을 추가하지 않는다.
  - 각 셀은 기준선(V0)에서 **한 축만** 바꾼 ablation 이다(V8·V9 종합 셀 제외).
  - 다중비교 보정: 22셀(11 × 2TF) Šidák α=0.05 → **|t| > 3.04**.
  - **홀드아웃(2025-12-29~)은 건드리지 않는다.** 탐색 구간에서 통과 셀이 나올 때만 연다.
  - 파라미터는 원문이 말한 값만 쓴다(유사캔들 판정 2배 = 원문의 "2배 이상 차이" 어법).
    임의 탐색으로 임계값을 고르지 않는다.

## 판정의 핵심 지표
승률·PF 가 아니라 **거래당 gross**(비용 차감 전)다. 4차가 밝힌 대로 이 값이 0이면
비용을 0으로 만들어도, 거래를 줄여도 0이다. 어떤 정의가 이 값을 **의미 있게 0에서
떼어놓는가**가 이 실험의 질문이다.

추가로 **청산 설계와 무관한** 지표를 병기한다 — 진입 후 +8/+24/+48봉 단순 보유 수익률.
청산 규칙이 결과를 만들어내는 것이 아님을 분리해서 보기 위함이다.

실행: .venv/bin/python backtesting/research/lab_easy_teaching_defs.py [--tf 15min|1h]
      [--full-period]  탐색/홀드아웃 분할 없이 전 구간 (V0 재현 검증용)
      [--sanity]       V0(추세필터 ON)이 기존 1차 결과를 재현하는지만 확인
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

from config import charter as C
from indicators import ta
from backtesting.research.data_cache import load

SPLIT = pd.Timestamp("2025-12-29")        # 이후는 홀드아웃 — 이 실험에서 열지 않는다
HTF = {"15min": "1h", "1h": "4h", "4h": "1D"}
WARM = {"15min": 900, "1h": 260, "4h": 220}

LOOKBACK = 50            # 존 탐색 구간(현행 전략과 동일)
MIN_FVG_ATR_MULT = 0.2   # 현행 전략과 동일
TIME_STOP = 48           # charter: easy_teaching.time_stop_bars
PARTIAL_TP = 0.5         # charter: partial_tp_ratio (반익반본)
FWD_BARS = (8, 24, 48)   # 청산 설계와 무관한 보유 수익률 측정 지점

# 원문 어법을 그대로 쓴다: "몸통의 크기가 **2배 이상** 차이 날 경우 신뢰할 만하다".
# FVG 3캔들 '크기가 비슷하다'의 반대말로 같은 2배를 쓴다 — 임의 탐색으로 고르지 않는다.
SIMILAR_RATIO = 2.0


# ── 정의 축 ──────────────────────────────────────────────────
@dataclass(frozen=True)
class Defn:
    """진입 신호의 정의. 이름은 영상 원문 표현을 따른다."""
    label: str
    note: str
    entry_zone: str = "ob"        # "ob" | "fvg" | "either"
    ob_zone: str = "body"         # "body"=감싸인 음봉 몸통(현행) | "candle"=음봉 캔들 전체
    engulf: str = "full"          # "full"=완전 감쌈(현행) | "half"=몸통 50% 이상
    require_confluence: bool = True   # OB 옆에 FVG 가 있어야 하는가(현행 True)
    confirm: str = "bull_close"   # "bull_close"=확인봉 양봉 마감(현행) | "none"=존 도달 즉시
    fvg_similar_veto: bool = False    # 3캔들 크기 유사 → FVG 무효 (영상 명시, 미구현)
    exit_opposite_ob: bool = False    # 반대(하락형) OB 출현 시 청산 (영상 익절 기준3, 미구현)
    rr_gate: bool = False             # 손익비 게이트(§7). 원문에 없는 제약 — 재현 검증용 축


# ★ 사전 등록 셀 — 실행 전에 확정했다. 사후 추가·삭제하지 않는다.
CELLS: list[Defn] = [
    Defn("V0 기준선(현행 재현)", "4차와 같은 정의 — 비교 기준"),
    Defn("V1 확인봉 조건 제거", "영상: '오더블록이 생긴 구간에 진입' (확인 요구 없음)",
         confirm="none"),
    Defn("V2 겹침 요구 제거", "영상 1강은 OB 단독으로 매매한다(FVG 겹침은 신뢰도 조건일 뿐)",
         require_confluence=False),
    Defn("V3 FVG 단독 진입존", "영상 2강: 'FVG가 생긴 구간에 진입하는 걸 원칙으로'",
         entry_zone="fvg", require_confluence=False),
    Defn("V4 FVG 단독 + 확인제거", "V3 에서 확인봉 조건까지 원문대로",
         entry_zone="fvg", require_confluence=False, confirm="none"),
    Defn("V5 OB존=캔들 전체", "존을 몸통이 아니라 꼬리 포함 캔들 전체로 해석",
         ob_zone="candle"),
    Defn("V6 부분 감쌈 허용", "'잡아먹는다'를 몸통 50% 이상으로 완화", engulf="half"),
    Defn("V7 FVG 유사캔들 무효", "영상: '3캔들 크기가 모두 비슷하면 역할을 못 한다'",
         fvg_similar_veto=True),
    Defn("V8 영상충실 종합", "OB·FVG 둘 다 진입존 + 확인없음 + 유사무효 + 겹침불요",
         entry_zone="either", require_confluence=False, confirm="none",
         fvg_similar_veto=True),
    Defn("V9 영상충실 + 확인봉", "V8 에 확인봉 양봉만 되살림",
         entry_zone="either", require_confluence=False, fvg_similar_veto=True),
    Defn("V10 반대 OB 청산", "영상 익절 기준③ '반대 오더블록의 출현'", exit_opposite_ob=True),
]


# ── 존 탐색 ──────────────────────────────────────────────────
def find_obs(o, h, l, c, d: Defn, bearish: bool = False):
    """
    상승형 오더블록: 직전 음봉의 몸통을 잡아먹는 양봉. (bearish=True 면 반대형)
    반환: (formed_at, bottom, top, low) 배열 튜플.
    """
    if len(o) < 2:
        z = np.empty(0, dtype=int)
        return z, z.astype(float), z.astype(float), z.astype(float)
    if not bearish:
        prev_ok = o[:-1] > c[:-1]                   # 직전 음봉
        curr_ok = c[1:] > o[1:]                     # 현재 양봉
        if d.engulf == "full":
            cover = (c[1:] >= o[:-1]) & (o[1:] <= c[:-1])
        else:                                        # 몸통 50% 이상만 덮어도 인정
            mid = (o[:-1] + c[:-1]) / 2.0
            cover = (c[1:] >= mid) & (o[1:] <= c[:-1])
    else:
        prev_ok = c[:-1] > o[:-1]                   # 직전 양봉
        curr_ok = o[1:] > c[1:]                     # 현재 음봉
        if d.engulf == "full":
            cover = (o[1:] >= c[:-1]) & (c[1:] <= o[:-1])
        else:
            mid = (o[:-1] + c[:-1]) / 2.0
            cover = (o[1:] >= mid) & (c[1:] <= o[:-1])
    ok = prev_ok & curr_ok & cover
    idx = np.nonzero(ok)[0] + 1
    if idx.size == 0:
        e = np.empty(0)
        return idx, e, e, e
    p = idx - 1
    if bearish:
        bot, top = (o[p], c[p]) if d.ob_zone == "body" else (l[p], h[p])
        bot, top = np.minimum(bot, top), np.maximum(bot, top)
        extreme = np.maximum(h[p], h[idx])          # 하락형의 무효화 지점 = 최고점
    else:
        if d.ob_zone == "body":
            bot, top = c[p], o[p]                   # 음봉이므로 close 가 몸통 하단
        else:
            bot, top = l[p], h[p]
        extreme = np.minimum(l[p], l[idx])          # 상승형의 무효화 지점 = 최저점
    return idx, bot.astype(float), top.astype(float), extreme.astype(float)


def find_fvgs(o, h, l, c, atr, d: Defn):
    """
    Bullish FVG: n-2 고가 < n 저가. 그 사이가 빈 공간.
    ⚠ ATR 대비 최소 크기 필터는 여기서 걸지 않는다 — 원본 전략은 **판단하는 봉의 ATR**로
      매번 다시 재므로(같은 갭이 어느 봉에선 유효, 다른 봉에선 무효), 터치 시점에 건다.
    """
    if len(h) < 3:
        e = np.empty(0)
        return np.empty(0, dtype=int), e, e, e
    bottom, top = h[:-2], l[2:]
    ok = top > bottom
    if d.fvg_similar_veto:
        # 영상: "연속된 3개 캔들이 모두 크기가 비슷하다면 FVG 로서의 역할을 못 할 가능성이
        # 높다." → 세 캔들 몸통 크기의 최대/최소 비가 2배 미만이면 버린다.
        b = np.abs(c - o)
        b0, b1, b2 = b[:-2], b[1:-1], b[2:]
        mx = np.maximum(np.maximum(b0, b1), b2)
        mn = np.minimum(np.minimum(b0, b1), b2)
        ok &= mx >= mn * SIMILAR_RATIO
    idx = np.nonzero(ok)[0] + 2
    if idx.size == 0:
        e = np.empty(0)
        return idx, e, e, e
    return (idx, h[idx - 2].astype(float), l[idx].astype(float),
            h[idx - 2].astype(float))   # 무효화 지점 = 갭 하단


# ── 진입 이벤트 ──────────────────────────────────────────────
def entry_events(df: pd.DataFrame, d: Defn, atr: np.ndarray):
    """
    각 존의 **첫 터치** 시점을 진입 후보로 만든다(현행 전략의 '소진' 규칙과 동일 —
    첫 터치에서 조건을 못 채우면 그 존은 죽는다).
    반환: (bar, stop, target) 리스트. bar 는 확인/터치가 일어난 봉(체결은 bar+1 시가).
    """
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    n = len(df)

    zones = []          # (formed_at, bottom, top, invalid_low, kind)
    ob_i, ob_b, ob_t, ob_low = find_obs(o, h, l, c, d)
    fvg_i, fvg_b, fvg_t, fvg_low = find_fvgs(o, h, l, c, atr, d)
    fz = list(zip(fvg_i.tolist(), fvg_b.tolist(), fvg_t.tolist()))
    if d.entry_zone in ("ob", "either"):
        zones += [(int(i), float(b), float(t), float(lo), "ob")
                  for i, b, t, lo in zip(ob_i, ob_b, ob_t, ob_low)]
    if d.entry_zone in ("fvg", "either"):
        zones += [(int(i), float(b), float(t), float(lo), "fvg")
                  for i, b, t, lo in zip(fvg_i, fvg_b, fvg_t, fvg_low)]
    if not zones:
        return []

    zones.sort(key=lambda z: z[0])
    events: list[tuple[int, float, float]] = []
    for f, bot, top, inv_low, kind in zones:
        hi = min(n - 2, f + LOOKBACK)
        if hi <= f:
            continue
        seg_l = l[f + 1: hi + 1]
        if seg_l.size == 0:
            continue
        hit = np.nonzero(seg_l <= top)[0]
        if hit.size == 0:
            continue
        i = f + 1 + int(hit[0])                       # 첫 터치 봉
        # 첫 터치 전에 무효화선이 깨졌으면 근거는 이미 죽었다
        if i > f + 1 and (l[f + 1:i] < inv_low).any():
            continue
        # ── ATR 의존 판정은 전부 '판단하는 봉'(i)의 ATR로 한다 (원본과 동일) ──
        a = float(atr[i]) if i < len(atr) and np.isfinite(atr[i]) else 0.0
        if kind == "fvg" and a > 0 and (top - bot) < a * MIN_FVG_ATR_MULT:
            continue                                   # 갭이 노이즈 수준
        if kind == "ob" and d.require_confluence:
            tol = a * 0.5
            near = any(fi < i and fb <= top + tol and ft >= bot - tol
                       and (a <= 0 or (ft - fb) >= a * MIN_FVG_ATR_MULT)
                       for fi, fb, ft in fz)
            if not near:
                continue
        if d.confirm == "bull_close":
            if not (c[i] > o[i] and c[i] >= bot):
                continue                              # 소진 — 재진입 없음
        stop = min(inv_low, float(l[i]))
        target = float(h[f:i + 1].max())              # 직전 파동 고점
        entry = float(o[min(i + 1, n - 1)])
        if d.rr_gate:
            if not C.entry_rr_ok(entry, stop, target):
                continue
        elif not (stop < entry < target):
            continue
        events.append((i, stop, target))
    events.sort()
    return events


# ── 시뮬레이션 ───────────────────────────────────────────────
def simulate(df: pd.DataFrame, events, cost: float, d: Defn,
             mask: np.ndarray, bear_bars: set[int] | None):
    """진입=다음 봉 시가. 반익반본 + 구조 손절 + 시간손절. 현행 lab 과 동일한 체결 가정."""
    o = df["open"].to_numpy(); h = df["high"].to_numpy(); l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    n = len(df)
    out = []
    busy_until = -1
    for i, stop, target in events:
        if i <= busy_until or i + 1 >= n:
            continue
        if not mask[i]:
            continue
        entry = float(o[i + 1])
        half, parts = False, []
        px, why, j = None, "", i + 1
        for j in range(i + 1, min(n, i + 1 + TIME_STOP + 1)):
            s = entry if half else stop
            if l[j] <= s:
                px, why = s, ("breakeven" if half else "stop")
                break
            if not half and h[j] >= target:
                half = True
                parts.append((target, PARTIAL_TP))
            if d.exit_opposite_ob and bear_bars and j in bear_bars:
                px, why = float(c[j]), "opposite_ob"   # 반대 OB 확정 → 종가 청산
                break
            if j - (i + 1) >= TIME_STOP:
                px, why = float(o[j]), "time"
                break
        if px is None:
            px, why, j = float(c[n - 1]), "eod", n - 1
        parts.append((px, 1.0 - sum(w for _, w in parts)))
        g = sum(w * (p / entry - 1) for p, w in parts)
        fwd = {f"fwd{b}": (float(c[min(i + 1 + b, n - 1)]) / entry - 1) * 100
               for b in FWD_BARS}
        out.append({"gross": g * 100, "net": (g - cost) * 100, "reason": why,
                    "bars": j - (i + 1), **fwd})
        busy_until = j
    return out


def stats(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    g = np.array([r["gross"] for r in rows])
    net = np.array([r["net"] for r in rows])
    w, lo = net[net > 0], net[net <= 0]
    pf = (w.sum() / abs(lo.sum())) if lo.sum() else math.inf
    tg = float(g.mean() / (g.std(ddof=1) / math.sqrt(len(g)))) if len(g) > 1 else 0.0
    tn = float(net.mean() / (net.std(ddof=1) / math.sqrt(len(net)))) if len(net) > 1 else 0.0
    r = {"n": len(g), "winrate": len(w) / len(net) * 100, "pf": pf,
         "gross": float(g.mean()), "net": float(net.mean()), "t_gross": tg, "t_net": tn}
    for b in FWD_BARS:
        r[f"fwd{b}"] = float(np.mean([x[f"fwd{b}"] for x in rows]))
    return r


def trend_mask(df: pd.DataFrame, tf: str) -> np.ndarray:
    h = df["close"].resample(HTF.get(tf, "1h")).last().dropna()
    up = h > ta.ema(h, 200)
    return up.shift(1).reindex(df.index, method="ffill").astype(float).fillna(0.0).to_numpy(bool)


def run_cell(d: Defn, tf: str, period: str, require_trend: bool) -> dict:
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
        if period == "selection":
            mask &= np.asarray(df.index < SPLIT)
        elif period == "holdout":
            mask &= np.asarray(df.index >= SPLIT)
        if require_trend:
            mask &= trend_mask(df, tf)
        bear = None
        if d.exit_opposite_ob:
            bi, _, _, _ = find_obs(df["open"].to_numpy(), df["high"].to_numpy(),
                                   df["low"].to_numpy(), df["close"].to_numpy(),
                                   d, bearish=True)
            bear = set(int(x) for x in bi)
        ev = entry_events(df, d, atr)
        cost = (C.FEE_ROUNDTRIP + C.VALIDATED_MARKETS.get(sym, 0.0025)) * 100
        rows += simulate(df, ev, cost / 100, d, mask, bear)
    return stats(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="15min")
    ap.add_argument("--period", default="selection",
                    choices=["selection", "holdout", "full"])
    ap.add_argument("--sanity", action="store_true",
                    help="V0(추세필터 ON, 전 구간)이 1차 결과를 재현하는지 확인")
    ap.add_argument("--cells", default="", help="쉼표로 셀 인덱스 지정(디버그)")
    args = ap.parse_args()

    if args.sanity:
        from dataclasses import replace as _rep
        print("[재현 검증] V0 · 추세필터 ON · RR게이트 ON · 전 구간")
        print("  기대(1차 결과): 15분 518거래 승률 26.6% PF 0.58 gross +0.047% net −0.211%")
        print("                  1시간 98거래 승률 32.7% PF 0.80 gross +0.040% net −0.209%")
        for tf in ("15min", "1h"):
            r = run_cell(_rep(CELLS[0], rr_gate=True), tf, "full", require_trend=True)
            if r["n"]:
                print(f"  실측 {tf:6} {r['n']:5d}거래 승률 {r['winrate']:5.1f}% "
                      f"PF {r['pf']:.2f} gross {r['gross']:+.3f}% net {r['net']:+.3f}%")
        return

    cells = CELLS
    if args.cells:
        cells = [CELLS[int(x)] for x in args.cells.split(",")]

    print(f"[정의 변형 ablation · {args.tf} · {args.period} 구간 · 추세필터 OFF]")
    print(f"사전 등록 {len(CELLS)}셀 × 2TF = {len(CELLS) * 2}셀 → "
          f"Šidák 보정 임계값 |t| > 3.04")
    print(f"왕복비용 ≈ 0.20~0.25% · 판정의 핵심은 **gross**(비용 차감 전)\n")
    hdr = (f"{'셀':24} {'거래':>6} {'승률%':>6} {'PF':>5} {'gross%':>8} {'t':>6} "
           f"{'net%':>8} {'+8봉':>7} {'+24봉':>7} {'+48봉':>7}")
    print(hdr)
    print("-" * len(hdr))
    results = []
    for d in cells:
        r = run_cell(d, args.tf, args.period, require_trend=False)
        results.append((d, r))
        if not r["n"]:
            print(f"{d.label:24} {'거래 0건':>6}")
            continue
        print(f"{d.label:24} {r['n']:6d} {r['winrate']:6.1f} {r['pf']:5.2f} "
              f"{r['gross']:+8.4f} {r['t_gross']:+6.2f} {r['net']:+8.4f} "
              f"{r['fwd8']:+7.3f} {r['fwd24']:+7.3f} {r['fwd48']:+7.3f}")

    print("\n[판정] gross 가 0에서 의미 있게 떨어져 있는 셀 (|t| > 3.04)")
    hits = [(d, r) for d, r in results
            if r.get("n") and abs(r["t_gross"]) > 3.04 and r["gross"] > 0]
    if not hits:
        print("  없음 — 어떤 정의로 바꿔도 진입 신호의 gross 는 0과 구별되지 않는다.")
    for d, r in hits:
        cover = "비용 초과 ✅" if r["net"] > 0 else "비용 미달 ❌"
        print(f"  {d.label}: gross {r['gross']:+.4f}% t {r['t_gross']:+.2f} → net "
              f"{r['net']:+.4f}% ({cover})")
    print("\n각 셀의 근거:")
    for d in cells:
        print(f"  {d.label:24} {d.note}")


if __name__ == "__main__":
    main()

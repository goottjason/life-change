"""
easy_teaching — "어떤 필터를 걸면 해볼 만한가"를 한 번에 답하는 실험.

## 문제 설정
1·2·3차 검증에서 조합·타임프레임·지지유의성·손절기준·손익비게이트·몸통필터·MTF 겹침을
50여 셀 갈랐지만 전부 §11 미통과였다. 여기서 "필터를 더 걸어 거래를 줄이면?"이 다음 질문인데,
필터 조합을 그리드로 돌리면 (a) 조합 폭발 (b) 다중비교로 우연한 승자 양산 두 문제가 생긴다.

**필터는 결국 거래의 부분집합을 고르는 일이다.** 그러므로:
  - 승패와 상관 있는 관측 가능한 특징이 **하나도 없다면 → 어떤 필터도 도움이 될 수 없다.**
  - 갈리는 특징이 있다면 → 그것이 곧 만들어야 할 필터다(그리고 그 값도 데이터가 알려준다).

그래서 손으로 건 필터를 **전부 끄고**(RR 게이트 off, 몸통필터 off, MTF off) 신호 모집단을
있는 그대로 수집하되, 각 거래마다 **진입 시점에 관측 가능한 특징**을 함께 기록한다.
미래 정보는 절대 넣지 않는다(진입 봉 이후의 어떤 값도 특징이 되지 않는다).

## 데이터 분할 (중요)
  탐색   : 2024-07-25 ~ 2025-12-28 (약 17개월)
  홀드아웃: 2025-12-29 ~ 2026-07-26 (약 7개월) — **마지막에 딱 한 번만** 쓴다.
리포지토리의 기존 관례(lab_final.SELECTION_START)와 반대 방향이지만, 앞선 검증들이 전 구간을
이미 훑었으므로 '더 최근 구간'을 홀드아웃으로 두는 편이 현재 시장에 대한 검정력이 높다.
⚠ 완전히 순결한 홀드아웃은 아니다 — 1~3차 50여 셀이 전 구간을 봤다. 다만 그때 고른 승자가
  없으므로(전부 음수) 오염은 '기본 셋업이 통하지 않는다'는 사실까지이고, 필터 선택에는
  관여하지 않았다. 이 한계를 결과 해석에 반드시 반영할 것.

실행: .venv/bin/python backtesting/research/lab_easy_teaching_features.py [--tf 15min] [--holdout]
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from config import charter as C
from indicators import ta
from strategies.base import Action
from strategies.easy_teaching import EasyTeachingStrategy
from backtesting.research import lab_easy_teaching as L
from backtesting.research.data_cache import load

SPLIT = pd.Timestamp("2025-12-29")
OUT = Path(__file__).resolve().parent / "data"


def features_for(df: pd.DataFrame, tf: str) -> dict[str, np.ndarray]:
    """진입 시점에 관측 가능한 특징만. 미래 정보 금지."""
    cl, o = df["close"].to_numpy(), df["open"].to_numpy()
    h, l, v = df["high"].to_numpy(), df["low"].to_numpy(), df["volume"].to_numpy()
    atr = ta.atr(df).to_numpy()
    rsi = ta.rsi(df["close"], 14).to_numpy()
    ema50 = ta.ema(df["close"], 50).to_numpy()
    ema200 = ta.ema(df["close"], 200).to_numpy()
    vol_ma = pd.Series(v).rolling(20).mean().to_numpy()
    body = np.abs(cl - o)
    rng = np.maximum(h - l, 1e-12)
    return {
        "atr_pct": atr / cl * 100,                       # 변동성
        "rsi14": rsi,                                    # 과매수/과매도
        "ema50_dist": (cl - ema50) / cl * 100,           # 단기 추세 이격
        "ema200_dist": (cl - ema200) / cl * 100,         # 장기 추세 이격
        "vol_ratio": v / np.maximum(vol_ma, 1e-12),      # 거래량 급증 (강의: "거래량 터질 때")
        "confirm_body": body / rng,                      # 확인봉이 얼마나 강한 양봉인가
        "confirm_body_atr": body / np.maximum(atr, 1e-12),
        "upper_wick": (h - np.maximum(o, cl)) / rng,     # 윗꼬리(매도압력)
        "lower_wick": (np.minimum(o, cl) - l) / rng,     # 아랫꼬리(매수압력·강의의 '긴 꼬리')
        "hour": np.array([t.hour for t in df.index], dtype=float),   # KST 시간대
    }


def collect(tf: str, holdout: bool) -> pd.DataFrame:
    """필터를 전부 끈 신호 모집단 + 진입 시점 특징 + 결과."""
    L.TF = tf
    L.WARM = {"15min": 900, "1h": 260, "4h": 220}[tf]
    L.COST_MODEL = "full"
    spec = replace(C.STRATEGY_SPECS["easy_teaching"], confluence_mode="ob_fvg",
                   require_trend=False,          # 추세도 '필터'이므로 특징으로 낮춰서 검사
                   use_rr_gate=False, min_ob_body_mult=0.0, require_mtf_overlap=False)
    strat = EasyTeachingStrategy(spec)
    time_stop = C.time_stop_bars_for(spec)

    rows = []
    for sym in C.VALIDATED_MARKETS:
        d5 = load(f"KRW-{sym}", "minute5")
        if d5 is None:
            continue
        df = L.resample(d5, tf)
        if len(df) < L.WARM + 200:
            continue
        mask = (df.index >= SPLIT) if holdout else (df.index < SPLIT)
        cost = L.cost_ratio(f"KRW-{sym}")
        feats = features_for(df, tf)
        up = L.trend_htf(df)
        o = df["open"].to_numpy(); h = df["high"].to_numpy()
        lo = df["low"].to_numpy()
        n = len(df)
        pos = None
        for i in range(L.WARM, n - 1):
            if pos is not None:
                held = i - pos["bar"]
                stop = pos["entry"] if pos["half"] else pos["stop"]
                px, why = None, ""
                if lo[i] <= stop:
                    px, why = stop, "stop"
                elif not pos["half"] and h[i] >= pos["target"]:
                    pos["half"] = True
                    pos["parts"].append((pos["target"], spec.partial_tp_ratio))
                elif held >= time_stop:
                    px, why = o[i], "time"
                if px is not None:
                    pos["parts"].append((px, 1.0 - sum(w for _, w in pos["parts"])))
                    g = sum(w * (p / pos["entry"] - 1) for p, w in pos["parts"])
                    rows.append({**pos["f"], "market": sym, "ts": pos["ts"], "reason": why,
                                 "gross": g * 100, "net": (g - cost) * 100})
                    pos = None
            if pos is not None:
                continue
            sig = strat.signal(df.iloc[max(0, i - 400): i + 2], {"trend_up": bool(up[i])})
            if sig.action != Action.ENTER_LONG:
                continue
            entry = o[i + 1]
            if not (sig.stop_price < entry < sig.target_price):
                continue
            if not mask[i]:
                continue                       # 구간 밖은 특징만 계산하고 거래로 세지 않는다
            f = {k: float(arr[i]) for k, arr in feats.items()}
            f["trend_up"] = float(up[i])
            f["stop_pct"] = (entry - sig.stop_price) / entry * 100
            f["target_pct"] = (sig.target_price - entry) / entry * 100
            f["rr"] = f["target_pct"] / max(f["stop_pct"], 1e-9)
            pos = {"entry": entry, "stop": sig.stop_price, "target": sig.target_price,
                   "bar": i + 1, "half": False, "parts": [], "f": f, "ts": df.index[i]}
    return pd.DataFrame(rows)


FEATURES = ["atr_pct", "rsi14", "ema50_dist", "ema200_dist", "vol_ratio", "confirm_body",
            "confirm_body_atr", "upper_wick", "lower_wick", "hour", "trend_up",
            "stop_pct", "target_pct", "rr"]


def analyse(d: pd.DataFrame, col: str = "gross") -> None:
    print(f"\n표본 {len(d)}거래 · 평균 {col} {d[col].mean():+.4f}% · "
          f"승률 {(d[col] > 0).mean() * 100:.1f}%")
    print(f"\n[특징별 5분위 평균 {col}%] — 어떤 특징이 승패를 가르는가")
    print(f"{'특징':18} {'Q1(최저)':>9} {'Q2':>9} {'Q3':>9} {'Q4':>9} {'Q5(최고)':>9} "
          f"{'Q5−Q1':>8} {'상관':>7}")
    hits = []
    for f in FEATURES:
        if f not in d or d[f].nunique() < 5:
            if f in d and d[f].nunique() == 2:      # 이진 특징
                g = d.groupby(f)[col].mean()
                print(f"{f:18} {'(이진)':>9} " + " ".join(f"{v:+9.4f}" for v in g.values))
            continue
        try:
            q = pd.qcut(d[f], 5, labels=False, duplicates="drop")
        except ValueError:
            continue
        m = d.groupby(q)[col].mean()
        if len(m) < 5:
            continue
        spread = m.iloc[-1] - m.iloc[0]
        corr = d[f].corr(d[col])
        print(f"{f:18} " + " ".join(f"{v:+9.4f}" for v in m.values) +
              f" {spread:+8.4f} {corr:+7.3f}")
        hits.append((abs(spread), f, spread, corr, m.max()))
    print("\n[정렬: 5분위 격차가 큰 순]")
    for _, f, spread, corr, best in sorted(hits, reverse=True)[:6]:
        print(f"  {f:18} 격차 {spread:+.4f}%p · 상관 {corr:+.3f} · 최고분위 평균 {best:+.4f}%")
    cost = 0.245
    print(f"\n왕복비용 기준선 ≈ {cost}% — 어떤 분위도 이걸 넘지 못하면 필터로는 살릴 수 없다.")
    top = max((h[4] for h in hits), default=float("-inf"))
    print(f"전 특징 최고분위 중 최댓값: {top:+.4f}% "
          f"({'비용 초과 ✅' if top > cost else '비용 미달 ❌'})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="15min")
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()
    tag = "holdout" if args.holdout else "selection"
    d = collect(args.tf, args.holdout)
    path = OUT / f"et_features_{args.tf}_{tag}.csv"
    d.to_csv(path, index=False)
    print(f"저장: {path} ({len(d)}거래)")
    if len(d):
        analyse(d)


if __name__ == "__main__":
    main()

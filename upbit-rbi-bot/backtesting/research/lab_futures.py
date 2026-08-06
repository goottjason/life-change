"""
easy_teaching 8차 — **선물·양방향 검정**. 3차부터 적어 둔 가설의 첫 직접 검정.

## 검정하는 가설
3~7차 README 가 반복해 적은 문장: "원저자의 환경은 **레버리지 선물·저수수료·양방향**이다.
업비트 현물 롱 전용에 그대로 옮기면 비용은 그대로 물고 하락 국면 기회는 통째로 버린다."
**이 문장은 한 번도 검정된 적이 없다.** 선물 데이터가 없었기 때문이다. 여기서 잰다.

선물로 오면 두 가지가 동시에 바뀐다:
  ① **비용** 업비트 현물 왕복 0.20~0.25% → 바이낸스 선물 taker 0.10% + 슬리피지 + 펀딩
  ② **방향** 숏이 열린다 → 원문 규칙의 나머지 절반
7차까지의 최대 엣지가 +0.16%p 였으므로 **①만으로도 부호가 바뀔 수 있는 크기다.**

## 숏 구현 — 역수 변환 (구현 비대칭을 원천 차단)
숏 로직을 따로 짜면 롱과 미묘하게 어긋날 수 있고, 그 차이가 결과로 오해된다.
그래서 가격을 **p → 1/p** 로 바꾸고(고가↔저가 교환) **같은 롱 코드**를 돌린다.
  - 역수 공간의 상승형 오더블록 = 원공간의 하락형 오더블록 (정확히 대응)
  - 역수 공간 롱 수익률 (1/p_exit)/(1/p_entry) − 1 = p_entry/p_exit − 1 = **원공간 숏 수익률**
즉 숏은 롱의 **수학적으로 정확한 거울상**이다.
⚠ 한계: ATR 등 절대 스케일 지표는 역수 공간에서 값이 달라진다(비율 구조는 근사 보존).
  이는 미러링에 내재한 것이며, 롱/숏 비교의 해석에 반영해야 한다.

## 비용 모델 (셋 다 보고한다)
  full  — taker 0.05%×2 + 슬리피지 0.01%×2 = **0.12%** (현실적 기본값)
  taker — taker 0.05%×2 = **0.10%** (완벽 체결 가정)
  vip0  — **0.00%** 수수료 (원저자가 말한 VIP 조건 — 가장 유리한 경우의 상한)
셋 모두에 **실측 펀딩비**를 방향에 맞춰 더한다(롱은 지불, 숏은 수취; 평균 +0.004%/8h 로
상승장에서 숏에 유리하다 — 넣지 않으면 롱/숏 비교가 편향된다).

## 사전 등록
5개 매매법 × 2방향 × 2TF = **20셀, 전부 보고.** Šidák α=0.05 → |t| > 3.02.
판정은 거래단위 t 가 아니라 **월클러스터 t** 와 **드리프트 초과분**으로 한다(7차 교훈).
홀드아웃(2025-12-29~)은 탐색에서 통과 셀이 나올 때만 연다.

실행: .venv/bin/python backtesting/research/lab_futures.py [--tf 1h|15m] [--cost full|taker|vip0]
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

from indicators import ta
from backtesting.research import lab_easy_teaching_defs as D
from backtesting.research import lab_easy_teaching_structure as S
from backtesting.research import data_cache_futures as F

SPLIT = D.SPLIT
WARM = {"1h": 260, "15m": 900}
COSTS = {"full": 0.0012, "taker": 0.0010, "vip0": 0.0}


@dataclass(frozen=True)
class Method:
    label: str
    run: str            # "zone" (defs 모듈) | "struct" (structure 모듈)
    cfg: object


METHODS: list[Method] = [
    Method("1강 오더블록", "zone",
           D.Defn("ob", "", entry_zone="ob", require_confluence=False, confirm="none")),
    Method("2강 FVG", "zone", D.CELLS[4]),                       # V4
    Method("3강 추세선", "struct", S.CELLS[1]),                   # TL2 추세추종(확인없음)
    Method("4강 채널", "struct", S.CELLS[4]),                     # CH2 역추세(확인없음)
    Method("5강 페이크아웃/트랩", "struct", S.CELLS[7]),           # FT1
]


def invert(df: pd.DataFrame) -> pd.DataFrame:
    """p → 1/p. 고가↔저가가 뒤바뀐다. 역수 공간의 롱 = 원공간의 숏."""
    return pd.DataFrame({"open": 1.0 / df["open"], "high": 1.0 / df["low"],
                         "low": 1.0 / df["high"], "close": 1.0 / df["close"],
                         "volume": df["volume"]}, index=df.index)


def funding_cum(idx: pd.DatetimeIndex, sym: str) -> np.ndarray:
    """봉 인덱스에 맞춘 **누적 펀딩비**. 롱은 이만큼 지불, 숏은 수취."""
    f = F.load_funding(sym)
    if f is None or f.empty:
        return np.zeros(len(idx))
    s = f["rate"].reindex(f.index.union(idx)).fillna(0.0).sort_index().cumsum()
    return s.reindex(idx).to_numpy()


def collect(m: Method, tf: str, period: str, short: bool, cost_key: str) -> pd.DataFrame:
    """
    S.run_market 대신 여기서 직접 돌린다 — 펀딩을 **실제 보유 구간**에 맞춰 넣어야 하므로
    진입·청산 봉 인덱스가 필요하다.
    """
    warm = WARM[tf]
    fee = COSTS[cost_key]
    rows = []
    for sym in F.SYMBOLS:
        raw = F.load(sym, tf)
        if raw is None or len(raw) < warm + 300:
            continue
        fcum = funding_cum(raw.index, sym)
        df = invert(raw) if short else raw
        atr = ta.atr(df).to_numpy()
        n = len(df)
        mask = np.zeros(n, dtype=bool)
        mask[warm:] = True
        mask &= (np.asarray(df.index < SPLIT) if period == "selection"
                 else np.asarray(df.index >= SPLIT))
        ev = (D.entry_events(df, m.cfg, atr) if m.run == "zone"
              else S.signals_for(df, m.cfg, atr))
        o = df["open"].to_numpy(); h = df["high"].to_numpy()
        l = df["low"].to_numpy(); c = df["close"].to_numpy()
        busy = -1
        for i, stop, target in ev:
            if i <= busy or i + 1 >= n or not mask[i]:
                continue
            entry = float(o[i + 1])
            if not (stop < entry < target):
                continue
            if (entry - stop) / entry * 100 > S.MAX_STOP_PCT:
                continue
            half, parts, px, why, j = False, [], None, "", i + 1
            for j in range(i + 1, min(n, i + 1 + D.TIME_STOP + 1)):
                sv = entry if half else stop
                if l[j] <= sv:
                    px, why = sv, ("breakeven" if half else "stop")
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
            # 펀딩: 롱은 누적분을 지불(+), 숏은 수취(−). 역수 공간이어도 원공간 기준으로 계산.
            fund = float(fcum[j] - fcum[i + 1])
            fund_cost = fund if not short else -fund
            row = {"ts": df.index[i], "market": sym, "short": short,
                   "gross": g * 100, "net": (g - fee - fund_cost) * 100,
                   "fund": fund_cost * 100, "reason": why, "bars": j - (i + 1)}
            for b in D.FWD_BARS:
                row[f"fwd{b}"] = (float(c[min(i + 1 + b, n - 1)]) / entry - 1) * 100
            rows.append(row)
            busy = j
    return pd.DataFrame(rows)


def drift(tf: str, period: str, short: bool) -> dict[int, float]:
    """무조건부 기준선. 숏이면 역수 공간에서 재므로 상승장에서 음수가 나온다."""
    acc = defaultdict(list)
    warm = WARM[tf]
    for sym in F.SYMBOLS:
        raw = F.load(sym, tf)
        if raw is None or len(raw) < warm + 300:
            continue
        df = invert(raw) if short else raw
        o, c = df["open"].to_numpy(), df["close"].to_numpy()
        n = len(df)
        m = np.zeros(n, dtype=bool); m[warm:] = True
        m &= (np.asarray(df.index < SPLIT) if period == "selection"
              else np.asarray(df.index >= SPLIT))
        idx = np.nonzero(m)[0]
        for b in D.FWD_BARS:
            k = idx[idx + 1 + b < n]
            acc[b].append((c[k + 1 + b] / o[k + 1] - 1) * 100)
    return {b: float(np.concatenate(v).mean()) for b, v in acc.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--period", default="selection", choices=["selection", "holdout"])
    ap.add_argument("--cost", default="full", choices=list(COSTS))
    args = ap.parse_args()

    fee_pct = COSTS[args.cost] * 100
    print(f"[선물 양방향 검정 · {args.tf} · {args.period} · 비용모델 {args.cost} "
          f"(수수료 {fee_pct:.2f}% + 실측 펀딩)]")
    print(f"거래소 Binance USDT-M 무기한 · {len(F.SYMBOLS)}종목 · 사전등록 20셀 → |t| > 3.02")
    for sh in (False, True):
        b = drift(args.tf, args.period, sh)
        print(f"  드리프트({'숏' if sh else '롱'}): " +
              " · ".join(f"+{k}봉 {v:+.3f}%" for k, v in b.items()))
    print()
    hdr = (f"{'매매법':22} {'방향':>4} {'거래':>6} {'승률%':>6} {'PF':>5} {'gross%':>8} "
           f"{'월t':>6} {'펀딩%':>7} {'net%':>8} {'+24봉초과':>9} {'종목+':>6}")
    print(hdr); print("-" * len(hdr))
    out = []
    for m in METHODS:
        for sh in (False, True):
            base = drift(args.tf, args.period, sh)
            d = collect(m, args.tf, args.period, sh, args.cost)
            if d.empty or len(d) < 2:
                print(f"{m.label:22} {'숏' if sh else '롱':>4} {'거래 없음':>6}")
                out.append((m, sh, None)); continue
            g = d["gross"].to_numpy(); net = d["net"].to_numpy()
            ct = S.cluster_t(d, "gross")
            w, lo = net[net > 0], net[net <= 0]
            pf = w.sum() / abs(lo.sum()) if lo.sum() else math.inf
            ex = d["fwd24"].mean() - base[24]
            pm = d.groupby("market")["net"].mean()
            print(f"{m.label:22} {'숏' if sh else '롱':>4} {len(d):6d} "
                  f"{(net > 0).mean()*100:6.1f} {pf:5.2f} {g.mean():+8.4f} {ct:+6.2f} "
                  f"{d['fund'].mean():+7.4f} {net.mean():+8.4f} {ex:+9.3f} "
                  f"{(pm > 0).sum():4d}/{len(pm)}")
            out.append((m, sh, {"n": len(d), "g": g.mean(), "ct": ct,
                                "net": net.mean(), "ex": ex}))

    print(f"\n[판정] 표본 ≥100 · **net > 0** · 월클러스터 t > 3.02")
    hits = [(m, sh, r) for m, sh, r in out
            if r and r["n"] >= 100 and r["net"] > 0 and r["ct"] > 3.02]
    if not hits:
        print("  없음.")
        near = [(m, sh, r) for m, sh, r in out
                if r and r["n"] >= 100 and r["net"] > 0]
        for m, sh, r in near:
            print(f"  (참고) {m.label} {'숏' if sh else '롱'}: net {r['net']:+.4f}% "
                  f"월t {r['ct']:+.2f} — net 은 양수지만 유의성 미달")
    for m, sh, r in hits:
        print(f"  ★ {m.label} {'숏' if sh else '롱'}: net {r['net']:+.4f}% "
              f"월t {r['ct']:+.2f} (n={r['n']})")


if __name__ == "__main__":
    main()

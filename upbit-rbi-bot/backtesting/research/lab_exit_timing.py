"""
rsi2 청산이 '너무 성급한가' — 2년 데이터로 검증.

## 질문
실전 첫 거래 2건이 모두 `reverse`(RSI2 ≥ 70 되돌림 완료)로 나가면서 소폭 손실이었다.
"더 들고 있었으면 이겼던 것 아닌가?"는 자연스러운 의심이다. 2건으로는 답할 수 없으므로
**같은 신호를 2년 치에 적용해 청산 규칙만 바꿔가며** 비교한다.

## 비교하는 청산 규칙
  A 현행        : 되돌림(RSI2≥70) + 익절/손절 + 시간손절        ← 검증된 설정
  B 되돌림 제거 : 익절/손절 + 시간손절만 (끝까지 들고 감)
  C 시간손절 2배: 현행에서 시간손절만 2배로 늘림
  D 되돌림+시간 : 되돌림 제거 + 시간손절 2배 (가장 오래 홀드)

'성급하다'가 사실이라면 B·C·D 가 A 보다 좋아야 한다.

비용: 수수료 0.1% + 종목별 실측 스프레드(보수적). 진입은 신호 다음 봉 시가.
실행: .venv/bin/python backtesting/research/lab_exit_timing.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from config import charter as C
from indicators import ta
from backtesting.research.data_cache import load
from backtesting.research import lab_easy_teaching as L   # resample / cost_ratio 재사용

WARM = 300
EXIT_LEVEL = 70.0          # strategies/rsi2_pullback.EXIT_LEVEL


def run(sym: str, tf: str, spec, use_exit_signal: bool, ts_mult: float) -> list[float]:
    """한 종목의 거래별 net% 목록. 라이브 규칙을 그대로 옮긴다."""
    d5 = load(f"KRW-{sym}", "minute5")
    if d5 is None:
        return []
    df = L.resample(d5, tf)
    if len(df) < WARM + 200:
        return []
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    lo = df["low"].to_numpy(); cl = df["close"].to_numpy()
    rsi2 = ta.rsi(df["close"], 2).to_numpy()
    atr_ratio = (ta.atr(df) / df["close"]).to_numpy()
    # 1시간봉 EMA200 추세 (라이브와 동일: 직전 완성봉 기준)
    hour = df["close"].resample("1h").last().dropna()
    up = (hour > ta.ema(hour, 200)).shift(1).reindex(df.index, method="ffill")
    up = up.astype(float).fillna(0.0).to_numpy(bool)

    cost = L.cost_ratio(f"KRW-{sym}")
    stop_pct, rr = spec.stop_pct, spec.rr
    tstop = int(C.time_stop_bars_for(spec) * ts_mult)
    entry_level, gate = spec.entry_level, spec.min_atr_ratio

    out: list[float] = []
    pos = None
    for i in range(WARM, len(df) - 1):
        if pos is not None:
            held = i - pos["bar"]
            px = None
            if lo[i] <= pos["sl"]:                       # 손절 우선(불리하게)
                px = pos["sl"]
            elif h[i] >= pos["tp"]:
                px = pos["tp"]
            elif use_exit_signal and rsi2[i] >= EXIT_LEVEL:
                px = cl[i]                                # 되돌림 완료 → 종가 청산
            elif held >= tstop:
                px = o[i]
            if px is not None:
                out.append(((px / pos["e"] - 1) - cost) * 100)
                pos = None
        if pos is not None:
            continue
        if not (rsi2[i] <= entry_level and up[i] and atr_ratio[i] >= gate):
            continue
        e = o[i + 1]
        pos = {"e": e, "sl": e * (1 - stop_pct), "tp": e * (1 + stop_pct * rr), "bar": i + 1}
    return out


def stats(v: list[float]) -> dict:
    if not v:
        return {"n": 0}
    a = np.array(v)
    w, l = a[a > 0], a[a <= 0]
    pf = (w.sum() / abs(l.sum())) if l.size and l.sum() else math.inf
    t = float(a.mean() / (a.std(ddof=1) / math.sqrt(len(a)))) if len(a) > 1 else 0.0
    eq = np.cumsum(a)
    mdd = float(np.max(np.maximum.accumulate(eq) - eq)) if eq.size else 0.0
    return {"n": len(a), "wr": len(w) / len(a) * 100, "pf": pf,
            "exp": float(a.mean()), "t": t, "total": float(a.sum()), "mdd": mdd}


VARIANTS = [
    ("A 현행(검증된 설정)",   True,  1.0),
    ("B 되돌림 청산 제거",     False, 1.0),
    ("C 시간손절 2배",         True,  2.0),
    ("D 되돌림제거+시간2배",   False, 2.0),
]


def main() -> None:
    for name, tf in (("rsi2", "5min"), ("rsi2_15m", "15min")):
        spec = C.STRATEGY_SPECS[name]
        print(f"\n{'='*74}\n{name} ({tf}) · 진입선 RSI2≤{spec.entry_level} · "
              f"게이트 {spec.min_atr_ratio:.2%} · 손절 {spec.stop_pct:.1%}")
        print(f"{'청산 규칙':22} {'거래':>6} {'승률%':>6} {'PF':>5} {'거래당%':>8} "
              f"{'t':>6} {'누적%':>8} {'MDD%p':>7}")
        base = None
        for label, use_sig, mult in VARIANTS:
            allv: list[float] = []
            for sym in C.VALIDATED_MARKETS:
                if sym in C.STRATEGY_BLACKLIST.get(name, set()):
                    continue                              # 5분봉 금지 종목 제외(§3.2-h)
                allv += run(sym, tf, spec, use_sig, mult)
            s = stats(allv)
            if not s["n"]:
                print(f"{label:22} 거래 없음"); continue
            if base is None:
                base = s
            mark = ""
            if base is not s:
                d = s["exp"] - base["exp"]
                mark = f"  ({d:+.3f}%p)"
            print(f"{label:22} {s['n']:6d} {s['wr']:6.1f} {s['pf']:5.2f} {s['exp']:+8.3f} "
                  f"{s['t']:+6.2f} {s['total']:+8.1f} {s['mdd']:7.1f}{mark}")
        print("  → '성급하다'가 사실이라면 B·C·D 가 A 보다 거래당 기댓값이 높아야 한다.")


if __name__ == "__main__":
    main()

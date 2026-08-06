"""
14차 — **H4(변동성 압축 후 돌파) 도입 가능성 검토**.

## 배경
12차 사전등록 12셀 중 유일하게 눈에 띈 것이 **H4 숏**(변동성 압축 후 **하방** 돌파):
net +0.571% · 드리프트 초과 +0.750%p · 양수월 10/16 — 그러나 **월클러스터 t +1.26** 으로
보정 임계값(3.0)은커녕 무보정 2.0 에도 못 미쳤다.

## ★ 도입 전에 먼저 답해야 할 질문 — 애초에 거래 가능한가
H4 에서 좋았던 것은 **숏**이다. 그런데 현재 봇은 **업비트 현물 · 롱 전용**이다.
즉 좋았던 쪽은 **현재 스택에서 실행 자체가 불가능**하다. 이걸 먼저 못 박지 않으면
"신호가 좋으니 넣어보자"로 흘러가 버린다.

그래서 이 실험이 답하는 것은 셋이다:
  ① **롱 쪽(상방 돌파)이 업비트 현물에서 성립하는가** — 실제 거래 가능한 유일한 형태
  ② **§11 게이트를 통과하는가** — 표본 100 · 승률 55% · PF 1.5 · MDD 20% · exp>0 · t>2
  ③ **rsi2 와 병행할 가치가 있는가** — 상관이 낮아야 포트폴리오에 기여한다.
     신호가 겹치면 자본만 나눠 쓰고 분산 효과는 없다.

실행: .venv/bin/python backtesting/research/lab_h4.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from config import charter as C
from indicators import ta
from backtesting.research.data_cache import load as load_spot
from backtesting.research import lab
from backtesting.research.lab_kelly import collect as rsi2_trades

TF = "minute15"          # 12차는 1시간봉이었다. 업비트 현물 검증 유니버스는 15분봉이 기준이다
HOLD = 24                # 12차와 동일 (사후 조정 금지)
SQUEEZE_Q = 0.2          # ATR/가격이 하위 20% = '압축'
SQUEEZE_LOOKBACK = 60 * 4    # 압축 판정 창
BREAK_BARS = 24          # 돌파 판정: 직전 24봉 고/저 갱신


def spot_panel(tf: str = TF):
    out = {}
    for sym in C.VALIDATED_MARKETS:
        d5 = load_spot(f"KRW-{sym}", "minute5")
        if d5 is None:
            continue
        df = d5.resample("15min" if tf == "minute15" else "1h").agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"}).dropna()
        if len(df) > 3000:
            out[sym] = df
    return out


def h4_signal(df: pd.DataFrame, up: bool):
    """변동성 압축 후 돌파. up=True 면 상방(롱), False 면 하방."""
    atr = (df["high"] - df["low"]).rolling(14).mean() / df["close"]
    comp = atr <= atr.rolling(SQUEEZE_LOOKBACK).quantile(SQUEEZE_Q)
    if up:
        brk = df["close"] > df["high"].rolling(BREAK_BARS).max().shift(1)
    else:
        brk = df["close"] < df["low"].rolling(BREAK_BARS).min().shift(1)
    return (comp & brk).to_numpy()


def eval_long(panel, cost_map):
    """롱 쪽만 — 업비트 현물에서 실행 가능한 유일한 형태."""
    rows = []
    for sym, df in panel.items():
        s = h4_signal(df, up=True)
        o, c = df["open"].to_numpy(), df["close"].to_numpy()
        n = len(df)
        cost = cost_map(sym) * 100
        busy = -1
        for i in np.nonzero(s)[0]:
            if i <= busy or i + 1 + HOLD >= n:
                continue
            g = (c[i + 1 + HOLD] / o[i + 1] - 1) * 100
            rows.append({"ts": df.index[i], "sym": sym, "gross": g, "net": g - cost})
            busy = i + HOLD
    return pd.DataFrame(rows)


def drift(panel):
    acc = []
    for _sym, df in panel.items():
        o, c = df["open"].to_numpy(), df["close"].to_numpy()
        n = len(df)
        i = np.arange(200, n - HOLD - 2)
        acc.append((c[i + 1 + HOLD] / o[i + 1] - 1) * 100)
    return float(np.concatenate(acc).mean())


def cl_t(d, col):
    g = d.groupby(d["ts"].dt.to_period("M"))[col].mean()
    return float(g.mean() / (g.std(ddof=1) / math.sqrt(len(g)))) if len(g) > 1 else 0.0, \
        int((g > 0).sum()), len(g)


def main() -> None:
    panel = spot_panel()
    cost_map = lambda s: C.FEE_ROUNDTRIP + C.VALIDATED_MARKETS.get(s, 0.0025)
    print(f"[H4 도입 검토 · 업비트 현물 {len(panel)}종목 · {TF} · 보유 {HOLD}봉]")

    print(f"\n■ 질문 ① — 롱 쪽(상방 돌파)이 실제 거래소에서 성립하는가")
    d = eval_long(panel, cost_map)
    b = drift(panel)
    if d.empty:
        print("  신호 0건"); return
    net = d["net"].to_numpy(); g = d["gross"].to_numpy()
    ct, pos, nm = cl_t(d, "net")
    t = float(net.mean() / (net.std(ddof=1) / math.sqrt(len(net))))
    wins, losses = net[net > 0], net[net <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() else math.inf
    eq = np.cumsum(net)
    mdd = float(np.max(np.maximum.accumulate(eq) - eq))
    per = d.groupby("sym")["net"].mean()
    print(f"  거래 {len(d):,} · 승률 {(net > 0).mean()*100:.1f}% · PF {pf:.2f}")
    print(f"  gross {g.mean():+.4f}% · 드리프트 {b:+.4f}% · **초과 {g.mean()-b:+.4f}%p**")
    print(f"  net {net.mean():+.4f}% · 거래t {t:+.2f} · **월클러스터t {ct:+.2f}** · "
          f"양수월 {pos}/{nm} · 종목양수 {(per > 0).sum()}/{len(per)}")

    print(f"\n■ 질문 ② — 헌장 §11 게이트")
    checks = [
        (f"표본 ≥ {C.BACKTEST_MIN_TRADES}거래", len(d) >= C.BACKTEST_MIN_TRADES, f"{len(d)}건"),
        (f"승률 > {C.BACKTEST_MIN_WINRATE*100:.0f}%", (net > 0).mean() > C.BACKTEST_MIN_WINRATE,
         f"{(net > 0).mean()*100:.1f}%"),
        (f"PF > {C.BACKTEST_MIN_PROFIT_FACTOR}", pf > C.BACKTEST_MIN_PROFIT_FACTOR, f"{pf:.2f}"),
        ("거래당 기댓값 > 0", net.mean() > 0, f"{net.mean():+.4f}%"),
        ("t > 2 (월클러스터)", ct > 2.0, f"{ct:+.2f}"),
    ]
    for label, ok, val in checks:
        print(f"    {'✅' if ok else '❌'} {label:24} {val}")
    print(f"  §11 판정: **{'통과' if all(o for _, o, _ in checks) else '미통과'}**")

    print(f"\n■ 질문 ③ — rsi2 와 병행할 가치가 있는가 (상관·신호 중복)")
    r2, _ = rsi2_trades("15m")
    if r2.empty:
        print("  rsi2 거래 없음"); return
    # 월별 수익률 상관 — 낮아야 분산 효과가 있다
    a = d.groupby(d["ts"].dt.to_period("M"))["net"].mean()
    bb = r2.groupby(r2["in"].dt.to_period("M"))["pnl"].mean() * 100
    j = pd.concat([a.rename("h4"), bb.rename("rsi2")], axis=1).dropna()
    corr = j["h4"].corr(j["rsi2"]) if len(j) > 3 else float("nan")
    print(f"  월 수익률 상관 {corr:+.3f} ({len(j)}개월 겹침)")
    # 진입 시각 중복률 — 같은 순간에 같이 켜지면 자본만 나눠 쓴다
    h4_t = set(d["ts"].dt.floor("h"))
    r2_t = set(r2["in"].dt.floor("h"))
    ov = len(h4_t & r2_t) / max(len(h4_t), 1)
    print(f"  같은 시각(시간 단위) 신호 중복률 {ov*100:.1f}%")
    print(f"  rsi2 월평균 {bb.mean():+.4f}% vs H4 월평균 {a.mean():+.4f}%")


if __name__ == "__main__":
    main()

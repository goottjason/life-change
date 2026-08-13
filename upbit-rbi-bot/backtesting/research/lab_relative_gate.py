"""
16차 — **변동성 게이트: 절대 임계값 vs 상대(백분위) 게이트**.

## 계기
2026-08-13, 운영자가 "11일째 거래가 0건"이라고 알렸다. 봇은 정상이었다
(LIVE · last_error None · can_enter ok · entry_skip 0건 = 신호 자체가 안 났다).
실제 시세를 라이브 조건에 흘려보내 확인한 결과:
  RSI2 조건은 통과했다 (5분 BTC 179봉 · XRP 232봉 / 15분 39~69봉)
  **ATR 게이트를 통과한 봉이 0개** — 11일 최대 변동성이 게이트보다 낮았다.

## 진짜 문제 — 게이트가 분포의 95~99 백분위에 있다
2년 캐시 기준 게이트 통과 비율: 5분 0.6% → BTC 0.6% · XRP 7.3% · ETH 2.8% · SOL 5.2%
                                15분 1.0% → 0.4~2.7%
즉 **상위 1~7% 변동성일 때만** 거래한다. 평균하면 연 250건이지만, 조용한 국면에는
**전면 정지**가 된다(최근 30일 통과 비율 ≈ 0%).

절대 임계값은 시장 전체의 변동성 레짐이 이동하면 같이 이동하지 못한다.
**상대 게이트**(그 종목의 최근 ATR 분포 백분위)는 레짐을 따라간다 — 선택성은 유지하면서
전면 정지를 피할 수 있는지가 이 실험의 질문이다.

## 통제
- 미래참조 금지: 백분위는 **직전까지의** 롤링 창에서만 계산(shift(1)).
- 판정은 월클러스터 t · 연 기여 · **빈도 일관성**(거래 있는 월 비율 · 최장 무거래 공백).
  빈도 일관성이 이번 문제의 핵심이므로 반드시 같이 본다.
- 스프레드는 봇이 실측한 중앙값을 쓴다.

실행: .venv/bin/python backtesting/research/lab_relative_gate.py
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from config import charter as C
from indicators import ta
from backtesting.research import lab
from backtesting.research.data_cache import load
from backtesting.research.fastsim import ExitCfg, Precomp, simulate_arrays
from backtesting.research.lab_screen import ENTRY_DELAY

UNI = ["BTC", "XRP", "ETH", "SOL"]          # 현재 rsi2(5분) 라이브 유니버스
WIN = 288 * 30                              # 상대 백분위 창 = 30일(5분봉)


def spreads() -> dict[str, float]:
    """
    봇이 실측한 스프레드 중앙값. **없으면 조용히 기본값으로 넘어가지 않고 멈춘다** —
    2026-08-13 에 이 파일이 없어 전 종목이 기본 0.25% 로 계산되면서 결론이 뒤집힌 적이 있다
    (gross +0.277% 인데 net −0.073% 로 나와 '라이브 설정이 음수'라는 잘못된 결론이 될 뻔했다).
    생성: 서버 /api/status 의 spread_stats 중앙값을 market,spread(비율) 로 저장.
    """
    p = Path("/tmp/spreads_med.csv")
    if not p.exists():
        raise SystemExit(f"스프레드 실측 파일이 없다: {p}\n"
                         "  서버 /api/status 의 spread_stats 로 먼저 생성할 것 "
                         "(조용한 기본값으로 계산하면 결론이 틀린다)")
    out = {r["market"].replace("KRW-", ""): float(r["spread"])
           for r in csv.DictReader(open(p))}
    missing = [s for s in UNI if s not in out]
    if missing:
        raise SystemExit(f"스프레드 실측값 없는 종목: {missing}")
    return out


def run(gate_kind: str, level: float, spd: dict) -> pd.DataFrame:
    """gate_kind: 'abs' = ATR% 절대값 / 'pct' = 롤링 백분위(0~1)."""
    S = C.STRATEGY_SPECS["rsi2"]
    rows = []
    for s in UNI:
        df = load(f"KRW-{s}", "minute5")
        if df is None or len(df) < 4000:
            continue
        atr_pct = ta.atr(df) / df["close"]
        if gate_kind == "abs":
            gate_ok = atr_pct >= level
        else:
            # ★ 미래참조 방지: 직전까지의 창에서만 분위 계산
            thr = atr_pct.rolling(WIN, min_periods=WIN // 3).quantile(level).shift(1)
            gate_ok = atr_pct >= thr
        r2 = ta.rsi(df["close"], 2)
        trend = df["close"] > ta.ema(df["close"], 2400)
        enter = (r2 <= S.entry_level) & trend & gate_ok.fillna(False)
        pre = Precomp(enter.to_numpy(), (r2 >= 70.0).to_numpy(),
                      df["close"].to_numpy(float), df["high"].to_numpy(float),
                      df["low"].to_numpy(float), ta.atr(df).to_numpy(float),
                      df["open"].to_numpy(float))
        cfg = ExitCfg("pct", S.stop_pct, 1.0, time_stop_bars=S.time_stop_bars,
                      use_exit_signal=True)
        cost = (C.FEE_ROUNDTRIP + spd.get(s, 0.0025)) * 100
        for t in simulate_arrays(pre, cfg, start=2500, end=len(df),
                                 slippage=0.0, entry_delay=ENTRY_DELAY):
            rows.append({"ts": df.index[t.entry_i], "sym": s,
                         "gross": t.pnl * 100, "net": t.pnl * 100 - cost})
        yrs = (df.index[-1] - df.index[2500]).days / 365.25
    d = pd.DataFrame(rows)
    d.attrs["yrs"] = yrs
    return d


def describe(tag: str, d: pd.DataFrame) -> None:
    if d.empty or len(d) < 3:
        print(f"{tag:26} 거래 {len(d)}건 — 판정 불가")
        return
    yrs = d.attrs["yrs"]
    net = d["net"].to_numpy()
    mo = d.groupby(d["ts"].dt.to_period("M"))["net"].mean()
    ct = float(mo.mean() / (mo.std(ddof=1) / math.sqrt(len(mo)))) if len(mo) > 1 else 0.0
    per = len(d) / yrs
    # 빈도 일관성 — 이번 사건의 핵심
    ts = d["ts"].sort_values()
    gap = ts.diff().dt.days.max()
    months = pd.period_range(ts.min().to_period("M"), ts.max().to_period("M"), freq="M")
    active = len(set(ts.dt.to_period("M"))) / max(len(months), 1) * 100
    print(f"{tag:26} {len(d):5d} {per:6.0f} {(net > 0).mean()*100:5.1f}% "
          f"{d['gross'].mean():+7.3f} {net.mean():+7.3f} {ct:+6.2f} "
          f"{net.mean()*per/100:+7.1f}% {active:8.0f}% {gap:7.0f}일")


def main() -> None:
    spd = spreads()
    print(f"[변동성 게이트 비교 · rsi2 5분봉 · {len(UNI)}종목 · 상대창 30일]")
    print(f"{'게이트':26} {'거래':>5} {'연간':>6} {'승률':>6} {'gross%':>7} {'net%':>7} "
          f"{'월t':>6} {'연기여':>8} {'거래월':>9} {'최장공백':>8}")
    print("-" * 100)
    describe("절대 0.60% (현행)", run("abs", 0.006, spd))
    describe("절대 0.50%", run("abs", 0.005, spd))
    for q in (0.80, 0.85, 0.90, 0.95):
        describe(f"상대 {int(q*100)}분위(30일)", run("pct", q, spd))
    print("\n※ 거래월 = 거래가 1건 이상 있었던 달의 비율 · 최장공백 = 거래 사이 최대 간격")


if __name__ == "__main__":
    main()

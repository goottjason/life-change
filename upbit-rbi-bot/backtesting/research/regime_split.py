"""
음의 기댓값이 '하락장 표본' 때문인지, '레짐 필터 미적용' 때문인지 분리한다.

walk-forward 의 OOS 구간이 대체로 하락장이었으므로 두 가지를 확인해야 한다:
  A) 시장 국면별 분해 — BTC가 EMA200 위(상승)/아래(하락)일 때 진입한 거래를 나눠 본다.
     상승장에서도 음수면 '장세 탓'이 아니다.
  B) 레짐 필터(헌장 §8) 적용 효과 — 라이브는 ADX로 전략을 켜고 끄지만 백테스트 엔진은
     필터 없이 원엣지를 본다. 라이브와 동일하게 필터를 걸면 개선되는지 측정한다.
     (macd=추세장만, rsi=횡보장만, cvd=항상 — `strategies/regime.py` 와 동일)

실행: .venv/bin/python backtesting/research/regime_split.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from config import charter as C
from config.charter import Regime
from indicators import ta
from strategies.regime import is_strategy_active
from backtesting.research import data_cache
from backtesting.research.fastsim import Params, charter_pass, simulate, stats_chrono

INTERVALS = ["minute15", "minute60", "minute240", "day"]
SPAN = {"minute5": "5분", "minute15": "15분", "minute60": "1시간", "minute240": "4시간", "day": "일"}


def line(name: str, items: list[tuple]) -> str:
    s = stats_chrono(items)
    if s.trades == 0:
        return f"{name:28s}      0"
    pf = "  inf" if s.profit_factor == float("inf") else f"{s.profit_factor:5.2f}"
    flag = "  ✅§11" if charter_pass(s) else ("  +exp" if s.exp > 0 else "")
    return (f"{name:28s} {s.trades:6d} {s.win_rate:6.1%} {pf} {s.exp:+8.3%} "
            f"{s.total:+10.1%} {s.mdd:6.1%} {s.t_stat:+6.2f}{flag}")


HEAD = (f"{'구성':28s} {'거래':>6s} {'승률':>6s} {'PF':>5s} {'exp/거래':>8s} "
        f"{'누적수익':>10s} {'MDD':>6s} {'t':>6s}")


def main() -> None:
    print("국면·레짐 분해 · 현 헌장 v1.2 파라미터 · 수수료 0.1% · intrabar(손절우선)")
    print("상승/하락 = 진입 시점 BTC가 EMA200 위/아래 · 추세/횡보 = 해당 종목 ADX≥25/<25 (§8)\n")

    for interval in INTERVALS:
        btc = data_cache.load("KRW-BTC", interval)
        if btc is None:
            print(f"[{SPAN[interval]}봉] 캐시 없음\n")
            continue
        btc_bull = (btc["close"] > ta.ema(btc["close"], 200))
        print(f"── {SPAN[interval]}봉 · {btc.index[0].date()}~{btc.index[-1].date()} " + "─" * 30)
        print(HEAD)

        for strategy in C.STRATEGY_SPECS:
            spec = C.STRATEGY_SPECS[strategy]
            p = Params(strategy=strategy, atr_mult=spec.atr_stop_mult, rr=spec.rr)
            buckets: dict[str, list[tuple]] = defaultdict(list)
            for m in data_cache.MARKETS:
                df = data_cache.load(m, interval)
                if df is None or len(df) < 400:
                    continue
                adx = ta.adx(df)
                trend_mask = (adx >= C.ADX_TREND_THRESHOLD).to_numpy(dtype=bool)
                idx = df.index
                for t in simulate(df, p, dfkey=f"{m}_{interval}"):
                    ts = idx[t.entry_i]
                    b = int(btc_bull.to_numpy()[min(btc.index.searchsorted(ts), len(btc) - 1)])
                    in_trend = bool(trend_mask[t.entry_i])
                    item = (ts, t.pnl)
                    buckets["전체"].append(item)
                    buckets["상승장" if b else "하락장"].append(item)
                    # 라이브 레짐 필터(§8) 재현: 현재 레짐에서 이 전략이 켜지는 거래만
                    cur = Regime.TREND if in_trend else Regime.RANGE
                    if is_strategy_active(spec.regime, cur):
                        buckets["레짐필터§8"].append(item)
                        buckets["레짐필터§8·" + ("상승장" if b else "하락장")].append(item)
            for key in ("전체", "상승장", "하락장", "레짐필터§8",
                        "레짐필터§8·상승장", "레짐필터§8·하락장"):
                if buckets[key]:
                    print(line(f"{strategy} {key}", buckets[key]))
            print()
        print()


if __name__ == "__main__":
    main()

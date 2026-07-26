"""
현 전략/원칙(v1.2: ATR 정규화 SL/TP + 1% 하한 + 수수료 0.1%)으로 백테스트.
- 더 긴 5분봉 히스토리를 pyupbit로 수집(§11 표본 확보).
- 현 동적 유니버스(스크리너) + BTC/ETH.
- 전략×마켓 개별 + 전략별 전체 집계(표본 합산).
주의: 백테스트 엔진은 레짐 필터를 적용하지 않음(전략 원엣지 검증). 라이브는 레짐 필터로 진입 제한.
"""
import sys
from pathlib import Path
ROOT = "/Users/jasonair/Projects/life-change/upbit-rbi-bot"
sys.path.insert(0, ROOT)

import pyupbit
from config import charter as C
from bot.trader import build_strategies
from backtesting import engine
from backtesting.metrics import compute
from indicators import ta
from strategies.base import Action

CANDLES = int(sys.argv[1]) if len(sys.argv) > 1 else 6000   # 5분봉 개수(~20일 @6000)

# 대상: 현 동적 유니버스 + majors (중복 제거)
try:
    from data.screener import Screener
    dyn = Screener(refresh_sec=0).eligible()
except Exception as e:
    dyn = []
    print(f"[screener 조회 실패 → majors만] {e}")
markets = []
for m in dyn + ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]:
    if m not in markets:
        markets.append(m)

print(f"백테스트 파라미터: 5분봉 {CANDLES}개(~{CANDLES*5/60/24:.0f}일), 수수료 왕복 {C.FEE_ROUNDTRIP:.1%}, "
      f"ATR배수 macd1.5/rsi1.2/cvd1.3, 손절하한 {C.MIN_STOP_RATIO:.0%}, 시간손절 {C.TIME_STOP_BARS}봉")
print(f"통과기준(§11): 승률>{C.BACKTEST_MIN_WINRATE:.0%}, PF>{C.BACKTEST_MIN_PROFIT_FACTOR}, "
      f"MDD<{C.BACKTEST_MAX_DRAWDOWN:.0%}, 거래≥{C.BACKTEST_MIN_TRADES}")
print(f"대상 {len(markets)}종목: {', '.join(markets)}\n")

strategies = build_strategies()
agg = {name: [] for name in strategies}   # 전략별 전체 pnl 합산

# 데이터 수집
data = {}
for m in markets:
    try:
        df = pyupbit.get_ohlcv(m, interval="minute5", count=CANDLES)
        if df is None or len(df) < 100:
            print(f"  [skip] {m}: 데이터 부족")
            continue
        data[m] = df.rename(columns=str.lower)
    except Exception as e:
        print(f"  [skip] {m}: {e}")

print(f"\n{'전략/마켓':22s} {'거래':>5s} {'승률':>6s} {'PF':>6s} {'MDD':>6s} {'수익률':>8s}  판정")
print("-" * 72)

# engine.run 은 pnl 리스트를 직접 안 주므로, 집계를 위해 여기서 pnl을 재수집(엔진 로직 복제 최소화 위해 재구현)
def run_collect(strat, df, warmup=30):
    spec = strat.spec
    pnls = []
    in_pos = False; entry_price = 0.0; entry_i = 0; sl = 0.0; tp = 0.0
    for i in range(warmup, len(df)):
        w = df.iloc[:i+1]; price = w["close"].iloc[-1]
        if not in_pos:
            if strat.signal(w).action == Action.ENTER_LONG:
                atr = float(ta.atr(w).iloc[-1]) if len(w) >= 14 else 0.0
                sl = C.stop_ratio_from_atr(spec.atr_stop_mult, atr, price)
                tp = spec.rr * sl
                in_pos, entry_price, entry_i = True, price, i
            continue
        gross = (price - entry_price) / entry_price
        ex = gross >= tp or gross <= -sl or (i - entry_i) >= C.TIME_STOP_BARS
        if not ex and strat.signal(w).action == Action.EXIT:
            ex = True
        if ex:
            pnls.append(gross - C.FEE_ROUNDTRIP)
            in_pos = False
    return pnls

for name, strat in strategies.items():
    for m, df in data.items():
        pnls = run_collect(strat, df)
        agg[name].extend(pnls)
        r = compute(pnls)
        mark = "PASS" if r.passes() else "fail"
        print(f"  {name:4s} {m:15s} {r.trades:5d} {r.win_rate:6.1%} {r.profit_factor:6.2f} "
              f"{r.max_drawdown:6.1%} {r.total_return:8.1%}  {mark}")

print("-" * 72)
print("전략별 전체 집계(전 종목 합산):")
for name in strategies:
    r = compute(agg[name])
    mark = "✅ PASS" if r.passes() else "❌ FAIL"
    print(f"  {name:4s} {'(합산)':15s} {r.trades:5d} {r.win_rate:6.1%} {r.profit_factor:6.2f} "
          f"{r.max_drawdown:6.1%} {r.total_return:8.1%}  {mark}")

"""
타임프레임 스윕: 각 전략을 5m/15m/1h/4h 에서 백테스트해 양의 기댓값이 나오는 조합을 찾는다.
- 대상: BTC, ETH, XRP (고유동성 majors — 알트 노이즈/과최적화 회피)
- 지표: 거래당 기댓값(mean pnl), 총수익, 승률, PF, MDD (수수료 0.1% 반영, 현 ATR규칙+1%하한)
주의: 이건 '가설 생성'이다. 통과 조합이 나와도 반드시 out-of-sample 로 재검증해야 함(과최적화 경계).
"""
import sys
from pathlib import Path
sys.path.insert(0, "/Users/jasonair/Projects/life-change/upbit-rbi-bot")
import statistics
import pyupbit
from config import charter as C
from bot.trader import build_strategies
from backtesting.metrics import compute
from indicators import ta
from strategies.base import Action

MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]
TFS = [("minute5", 3000), ("minute15", 3000), ("minute60", 3000), ("minute240", 2000)]

def run_collect(strat, df, warmup=30):
    spec = strat.spec; pnls = []
    in_pos=False; ep=0.0; ei=0; sl=0.0; tp=0.0
    n = len(df)
    for i in range(warmup, n):
        w = df.iloc[:i+1]; price = w["close"].iloc[-1]
        if not in_pos:
            if strat.signal(w).action == Action.ENTER_LONG:
                atr = float(ta.atr(w).iloc[-1]) if len(w)>=14 else 0.0
                sl = C.stop_ratio_from_atr(spec.atr_stop_mult, atr, price); tp = spec.rr*sl
                in_pos, ep, ei = True, price, i
            continue
        g = (price-ep)/ep
        ex = g>=tp or g<=-sl or (i-ei)>=C.TIME_STOP_BARS
        if not ex and strat.signal(w).action == Action.EXIT: ex=True
        if ex:
            pnls.append(g - C.FEE_ROUNDTRIP); in_pos=False
    return pnls

strategies = build_strategies()
print(f"타임프레임 스윕 · majors {MARKETS} · 수수료 {C.FEE_ROUNDTRIP:.1%} · 손절하한 {C.MIN_STOP_RATIO:.0%} · 시간손절 {C.TIME_STOP_BARS}봉")
print("기댓값(exp) = 거래당 평균 손익률. >0 이어야 장기 +.\n")
print(f"{'전략':5s} {'TF':9s} {'거래':>6s} {'승률':>6s} {'PF':>6s} {'exp/거래':>9s} {'총수익':>9s} {'MDD':>6s}")
print("-"*66)

for name, strat in strategies.items():
    for tf, cnt in TFS:
        allpnl = []
        for m in MARKETS:
            try:
                df = pyupbit.get_ohlcv(m, interval=tf, count=cnt)
                if df is None or len(df)<100: continue
                allpnl += run_collect(strat, df.rename(columns=str.lower))
            except Exception as e:
                pass
        if not allpnl:
            print(f"{name:5s} {tf:9s}   (데이터 없음)"); continue
        r = compute(allpnl)
        exp = statistics.mean(allpnl)
        flag = "  <== +exp" if exp>0 else ""
        print(f"{name:5s} {tf:9s} {r.trades:6d} {r.win_rate:6.1%} {r.profit_factor:6.2f} "
              f"{exp:+9.3%} {r.total_return:+9.1%} {r.max_drawdown:6.1%}{flag}")
    print()

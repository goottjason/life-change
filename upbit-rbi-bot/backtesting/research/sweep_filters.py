"""
원칙적 개선 실험(가설 생성): 추세 필터 + 역방향청산 제거가 음의 엣지를 뒤집는가?
- 추세필터: close > EMA200 일 때만 진입(추세추종은 추세에서).
- no-reverse: 역방향 신호 청산 제거 → 과매매/수수료 churn 감소, ATR SL/TP+시간손절만.
대상 BTC/ETH/XRP, 15m/60m. 수수료 0.1%, 현 ATR규칙+1%하한.
※ 통과해도 반드시 out-of-sample 재검증(과최적화 경계).
"""
import sys, statistics
sys.path.insert(0, "/Users/jasonair/Projects/life-change/upbit-rbi-bot")
import pyupbit
from config import charter as C
from bot.trader import build_strategies
from backtesting.metrics import compute
from indicators import ta
from strategies.base import Action

MARKETS = ["KRW-BTC","KRW-ETH","KRW-XRP"]
TFS = [("minute15",3000),("minute60",3000)]

def run_collect(strat, df, trend=False, use_reverse=True, warmup=210):
    spec=strat.spec; pnls=[]; in_pos=False; ep=0.0; ei=0; sl=0.0; tp=0.0
    close=df["close"]; ema200=ta.ema(close,200)
    for i in range(warmup,len(df)):
        w=df.iloc[:i+1]; price=close.iloc[i]
        if not in_pos:
            if strat.signal(w).action==Action.ENTER_LONG:
                if trend and not (price>ema200.iloc[i]): continue
                atr=float(ta.atr(w).iloc[-1]) if len(w)>=14 else 0.0
                sl=C.stop_ratio_from_atr(spec.atr_stop_mult,atr,price); tp=spec.rr*sl
                in_pos,ep,ei=True,price,i
            continue
        g=(price-ep)/ep
        ex=g>=tp or g<=-sl or (i-ei)>=C.TIME_STOP_BARS
        if not ex and use_reverse and strat.signal(w).action==Action.EXIT: ex=True
        if ex: pnls.append(g-C.FEE_ROUNDTRIP); in_pos=False
    return pnls

def agg(strat, tf, cnt, **kw):
    out=[]
    for m in MARKETS:
        try:
            df=pyupbit.get_ohlcv(m,interval=tf,count=cnt)
            if df is None or len(df)<250: continue
            out+=run_collect(strat, df.rename(columns=str.lower), **kw)
        except Exception: pass
    return out

strategies=build_strategies()
CONFIGS=[("baseline",{}),("+trend",{"trend":True}),("+trend+noRev",{"trend":True,"use_reverse":False}),("noRev",{"use_reverse":False})]
print("원칙적 개선 실험 · majors · 수수료 0.1% · exp=거래당 평균손익률(>0 목표)\n")
print(f"{'전략':5s} {'TF':8s} {'config':14s} {'거래':>5s} {'승률':>6s} {'PF':>5s} {'exp':>8s} {'총수익':>8s}")
print("-"*66)
for name,strat in strategies.items():
    for tf,cnt in TFS:
        for cfg,kw in CONFIGS:
            p=agg(strat,tf,cnt,**kw)
            if not p:
                print(f"{name:5s} {tf:8s} {cfg:14s}   (없음)"); continue
            r=compute(p); e=statistics.mean(p); flag="  <== +" if e>0 else ""
            print(f"{name:5s} {tf:8s} {cfg:14s} {r.trades:5d} {r.win_rate:6.1%} {r.profit_factor:5.2f} {e:+8.3%} {r.total_return:+8.1%}{flag}")
    print()

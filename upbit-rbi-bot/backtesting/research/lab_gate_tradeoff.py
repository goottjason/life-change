import sys, math, csv; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from config import charter as C
from indicators import ta
from backtesting.research import lab
from backtesting.research.data_cache import load
from backtesting.research.fastsim import ExitCfg, Precomp, simulate_arrays
from backtesting.research.lab_screen import ENTRY_DELAY
SPLIT=pd.Timestamp("2025-12-29")
med={r['market'].replace('KRW-',''):float(r['spread']) for r in csv.DictReader(open('/tmp/spreads_med.csv'))}
LIVE=['XRP','BTC','ETH','SOL','LINK','BCH']
S=C.STRATEGY_SPECS['rsi2']
print("[ATR 게이트 vs 빈도·기댓값] rsi2 5분봉 · 현재 가동 6종목 · 스프레드 실측 반영")
print(f"{'게이트':>7} {'연간거래':>8} {'승률':>6} {'gross%':>8} {'net%':>8} {'월t':>6} {'연기여':>9} {'홀드아웃net':>11}")
print("-"*72)
for gate in (0.002,0.003,0.004,0.005,0.006,0.008,0.010):
    rows=[]
    for s in LIVE:
        df=load(f'KRW-{s}','minute5')
        if df is None or len(df)<4000: continue
        e,x=lab.sig_rsi2(df,df,f'KRW-{s}',th=S.entry_level,gate=gate)
        pre=Precomp(e,x,df['close'].to_numpy(float),df['high'].to_numpy(float),df['low'].to_numpy(float),
                    ta.atr(df).to_numpy(float),df['open'].to_numpy(float))
        cfg=ExitCfg('pct',S.stop_pct,1.0,time_stop_bars=S.time_stop_bars,use_exit_signal=True)
        sp=med.get(s,0.0025)
        for t in simulate_arrays(pre,cfg,start=2500,end=len(df),slippage=0.0,entry_delay=ENTRY_DELAY):
            rows.append({'ts':df.index[t.entry_i],'gross':t.pnl*100,
                         'net':t.pnl*100-(C.FEE_ROUNDTRIP+sp)*100})
        yrs=(df.index[-1]-df.index[2500]).days/365.25
    d=pd.DataFrame(rows)
    if d.empty: continue
    mo=d.groupby(d['ts'].dt.to_period('M'))['net'].mean()
    ct=mo.mean()/(mo.std(ddof=1)/math.sqrt(len(mo)))
    ho=d[d.ts>=SPLIT]['net']
    per_yr=len(d)/yrs
    mark=' ← 현행' if abs(gate-S.min_atr_ratio)<1e-9 else ''
    print(f"{gate*100:6.1f}% {per_yr:8.0f} {(d['net']>0).mean()*100:5.1f}% {d['gross'].mean():+8.3f} "
          f"{d['net'].mean():+8.3f} {ct:+6.2f} {d['net'].mean()*per_yr/100*100:+8.1f}% "
          f"{ho.mean():+10.3f}%{mark}")

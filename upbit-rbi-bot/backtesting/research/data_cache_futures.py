"""
Binance USDT-M 무기한 선물 캔들 + 펀딩비 캐시.

## 왜 필요한가
1~7차 검증은 전부 **업비트 현물·롱 전용**이었다. 3차부터 반복해서 적어 둔 가설이 있다 —
"원저자의 환경은 **레버리지 선물·저수수료·양방향**이다. 업비트 현물 롱 전용에 그대로
옮기면 비용은 그대로 물고 하락 국면 기회는 통째로 버린다."
7차까지 이 가설은 **한 번도 직접 검정된 적이 없다.** 선물 데이터가 없었기 때문이다.

선물로 오면 두 가지가 동시에 바뀐다:
  ① **비용**: 업비트 현물 왕복 0.20~0.25% → 바이낸스 선물 왕복 taker 0.10% (+펀딩)
  ② **방향**: 숏이 열린다 → 원문 규칙의 나머지 절반을 잴 수 있다
7차까지의 최대 엣지가 **+0.16%p** 였으므로 ①만으로도 부호가 바뀔 수 있는 크기다.

## 펀딩비도 받는다
선물은 8시간마다 펀딩을 주고받는다. 상승장에서는 보통 롱이 숏에게 낸다 — 즉
**롱/숏 비교에서 체계적 편향**이 되므로 비용에 반드시 넣어야 한다. 추정하지 않고 실측을 쓴다.

실행: .venv/bin/python backtesting/research/data_cache_futures.py [--force]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import urllib.request
import json

DATA_DIR = Path(__file__).resolve().parent / "data" / "futures"
BASE = "https://fapi.binance.com"

# 업비트 검증 유니버스와 대응되는 선물 심볼. ORCA 는 바이낸스 선물에 없어 제외한다.
SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "BCHUSDT", "LINKUSDT",
           "ATOMUSDT", "AVAXUSDT", "ETCUSDT", "DOTUSDT", "NEARUSDT", "SUIUSDT",
           "ENSUSDT"]
INTERVALS = ["1h", "15m"]
START = "2024-07-25"
END = "2026-07-27"


def _get(url: str, tries: int = 5):
    for k in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:                       # 레이트리밋·일시 오류는 물러섰다 재시도
            if k == tries - 1:
                raise
            time.sleep(1.5 * (k + 1))
    return None


def fetch_klines(symbol: str, interval: str) -> pd.DataFrame:
    start = int(pd.Timestamp(START, tz="UTC").timestamp() * 1000)
    end = int(pd.Timestamp(END, tz="UTC").timestamp() * 1000)
    rows = []
    while start < end:
        url = (f"{BASE}/fapi/v1/klines?symbol={symbol}&interval={interval}"
               f"&startTime={start}&limit=1500")
        batch = _get(url)
        if not batch:
            break
        rows += batch
        nxt = batch[-1][0] + 1
        if nxt <= start:
            break
        start = nxt
        time.sleep(0.12)                             # 가중치 10 × ~8req/s → 여유 있게
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "ot", "open", "high", "low", "close", "volume", "ct", "qv", "n",
        "tb", "tq", "ig"])
    df = df[["ot", "open", "high", "low", "close", "volume"]].astype(
        {"open": float, "high": float, "low": float, "close": float, "volume": float})
    # KST 로 맞춘다 — 업비트 실험과 같은 시간축이라야 비교가 성립한다
    df.index = pd.to_datetime(df["ot"], unit="ms", utc=True).dt.tz_convert(
        "Asia/Seoul").dt.tz_localize(None)
    return df.drop(columns=["ot"])[~df.index.duplicated()].sort_index()


def fetch_funding(symbol: str) -> pd.DataFrame:
    """펀딩비 이력(8시간마다). 롱은 양수일 때 지불, 숏은 수취."""
    start = int(pd.Timestamp(START, tz="UTC").timestamp() * 1000)
    end = int(pd.Timestamp(END, tz="UTC").timestamp() * 1000)
    rows = []
    while start < end:
        url = (f"{BASE}/fapi/v1/fundingRate?symbol={symbol}"
               f"&startTime={start}&limit=1000")
        batch = _get(url)
        if not batch:
            break
        rows += batch
        nxt = batch[-1]["fundingTime"] + 1
        if nxt <= start:
            break
        start = nxt
        time.sleep(0.12)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["rate"] = df["fundingRate"].astype(float)
    df.index = pd.to_datetime(df["fundingTime"], unit="ms", utc=True).dt.tz_convert(
        "Asia/Seoul").dt.tz_localize(None)
    return df[["rate"]][~df.index.duplicated()].sort_index()


def load(symbol: str, interval: str) -> pd.DataFrame | None:
    p = DATA_DIR / f"{symbol}_{interval}.csv"
    if not p.exists():
        return None
    return pd.read_csv(p, index_col=0, parse_dates=True)


def load_funding(symbol: str) -> pd.DataFrame | None:
    p = DATA_DIR / f"{symbol}_funding.csv"
    if not p.exists():
        return None
    return pd.read_csv(p, index_col=0, parse_dates=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for sym in SYMBOLS:
        for iv in INTERVALS:
            p = DATA_DIR / f"{sym}_{iv}.csv"
            if p.exists() and not args.force:
                d = pd.read_csv(p, index_col=0, parse_dates=True)
                print(f"  캐시 {sym:10} {iv:4} {len(d):7,}봉 "
                      f"{d.index[0].date()}~{d.index[-1].date()}")
                continue
            d = fetch_klines(sym, iv)
            if d.empty:
                print(f"  ✗ {sym} {iv} 데이터 없음")
                continue
            d.to_csv(p)
            print(f"  받음 {sym:10} {iv:4} {len(d):7,}봉 "
                  f"{d.index[0].date()}~{d.index[-1].date()}")
        pf = DATA_DIR / f"{sym}_funding.csv"
        if not pf.exists() or args.force:
            f = fetch_funding(sym)
            if not f.empty:
                f.to_csv(pf)
                print(f"       펀딩 {len(f):,}건 평균 {f['rate'].mean()*100:+.4f}%/8h")


if __name__ == "__main__":
    main()

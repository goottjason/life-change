"""
MA200 이중 타임프레임 전략(2026-06-11 영상) 검정용 데이터 캐시.

## 두 개의 유니버스
① **미국 주식(본 검정)** — 영상이 명시한 도메인이다: "나스닥 100에 들어 있는 종목이라던가
   시총 상위 50위 안에 들어 있는 종목… 급등주·작전주에는 안 통합니다."
   yfinance 일봉(1990~). ⚠ **시간봉은 60일치만 제공**되므로 4시간 MA200 은 근사해야 한다.
   미국 정규장은 6.5시간 → 트레이딩뷰 4시간봉은 하루 약 2개 → **MA200(4h) ≈ MA100(일봉)**.
② **암호화폐(보조)** — 이 리포의 도메인. Binance 현물 1일·4시간봉(2017~).
   24시간 시장이라 **4시간봉 200개 = 33.3일** 이고 실제 4시간봉으로 **정확히** 계산된다.

## ⚠ 생존 편향 (결과 해석에 반드시 반영할 것)
'현재의' 나스닥100으로 과거를 테스트하면 **살아남아 오른 종목만** 고르는 셈이다.
롱 전용 추세추종 전략에는 이 편향이 **결정적으로 유리하게** 작용한다.
그래서 유니버스에 **당시 대형주였다가 무너진 종목**(INTC·PYPL·ZM·PTON·DOCU·ROKU·
RIVN·LCID·NIO·BABA·BA·F·WBA·MRNA…)을 의도적으로 섞어 편향을 줄인다.
완전히 제거되지는 않는다 — 상장폐지된 종목은 애초에 데이터가 없다.

실행: .venv312/bin/python backtesting/research/data_cache_ma200.py [--force]
      (yfinance 가 필요하므로 python3.12 venv 로 돌린다)
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

DIR = Path(__file__).resolve().parent / "data" / "ma200"

# ── ① 미국 주식 ──────────────────────────────────────────────
# 현재 나스닥100/대형주 + **무너진 대형주**를 의도적으로 섞었다(생존 편향 완화).
STOCKS = [
    # 메가캡·나스닥100 핵심
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "NFLX",
    "AMD", "PEP", "ADBE", "CSCO", "TMUS", "INTU", "QCOM", "TXN", "AMAT", "BKNG",
    "ISRG", "AMGN", "HON", "VRTX", "ADP", "PANW", "REGN", "MU", "LRCX", "ADI",
    "KLAC", "SBUX", "MDLZ", "SNPS", "CDNS", "MELI", "CRWD", "MAR", "ORLY", "CTAS",
    "ABNB", "FTNT", "DASH", "ADSK", "NXPI", "PCAR", "PAYX", "MNST", "ROP", "CPRT",
    "ODFL", "WDAY", "CHTR", "ROST", "KDP", "FAST", "EA", "DDOG", "TEAM", "IDXX",
    # S&P 대형주(비기술 포함)
    "BRK-B", "JPM", "V", "MA", "UNH", "XOM", "JNJ", "PG", "HD", "CVX",
    "LLY", "ABBV", "MRK", "KO", "WMT", "BAC", "CRM", "ACN", "MCD", "DIS",
    "LIN", "TMO", "ABT", "DHR", "NKE", "PM", "NEE", "UPS", "RTX", "CAT",
    "GS", "SPGI", "BLK", "AXP", "DE", "LMT", "SCHW", "T", "VZ", "CMCSA",
    # 영상에 나온 종목
    "COIN", "HOOD", "PLTR", "MSTR", "ORCL",
    # ★ 무너진/부진했던 대형주 — 생존 편향 완화용
    "INTC", "PYPL", "ZM", "PTON", "DOCU", "ROKU", "SQ", "SHOP", "SNAP", "UBER",
    "LYFT", "RIVN", "LCID", "NIO", "BABA", "BA", "F", "GM", "WBA", "MRNA",
    "PFE", "CVS", "TGT", "NKE", "SBUX", "EBAY", "TWLO", "ZS", "OKTA", "U",
    "RBLX", "AFRM", "SOFI", "CHWY", "W", "ETSY", "MTCH", "PINS", "SPOT", "NET",
]

# ── ② 암호화폐 (Binance 현물) ────────────────────────────────
COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "SOLUSDT",
         "DOGEUSDT", "DOTUSDT", "LINKUSDT", "LTCUSDT", "BCHUSDT", "ATOMUSDT",
         "AVAXUSDT", "UNIUSDT", "ETCUSDT", "XLMUSDT", "NEARUSDT", "ALGOUSDT",
         "VETUSDT", "FILUSDT", "ICPUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
         "SUIUSDT", "AAVEUSDT", "MKRUSDT", "GRTUSDT"]
BINANCE = "https://api.binance.com"


def _get(url, tries=5):
    for k in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read())
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1.5 * (k + 1))
    return None


def fetch_binance(symbol: str, interval: str) -> pd.DataFrame:
    start = int(pd.Timestamp("2017-01-01", tz="UTC").timestamp() * 1000)
    end = int(pd.Timestamp("2026-08-01", tz="UTC").timestamp() * 1000)
    rows = []
    while start < end:
        b = _get(f"{BINANCE}/api/v3/klines?symbol={symbol}&interval={interval}"
                 f"&startTime={start}&limit=1000")
        if not b:
            break
        rows += b
        nxt = b[-1][0] + 1
        if nxt <= start:
            break
        start = nxt
        time.sleep(0.1)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ot", "open", "high", "low", "close", "volume",
                                     "ct", "qv", "n", "tb", "tq", "ig"])
    df = df[["ot", "open", "high", "low", "close", "volume"]].astype(
        {"open": float, "high": float, "low": float, "close": float, "volume": float})
    df.index = pd.to_datetime(df["ot"], unit="ms", utc=True).dt.tz_localize(None)
    return df.drop(columns=["ot"])[~df.index.duplicated()].sort_index()


def load_stock(t: str):
    p = DIR / "stocks" / f"{t}.csv"
    return pd.read_csv(p, index_col=0, parse_dates=True) if p.exists() else None


def load_coin(s: str, iv: str):
    p = DIR / "coins" / f"{s}_{iv}.csv"
    return pd.read_csv(p, index_col=0, parse_dates=True) if p.exists() else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", default="both", choices=["both", "stocks", "coins"])
    a = ap.parse_args()
    (DIR / "stocks").mkdir(parents=True, exist_ok=True)
    (DIR / "coins").mkdir(parents=True, exist_ok=True)

    if a.only in ("both", "stocks"):
        import yfinance as yf
        todo = [t for t in dict.fromkeys(STOCKS)
                if a.force or not (DIR / "stocks" / f"{t}.csv").exists()]
        print(f"[미국 주식] 신규 {len(todo)} / 전체 {len(set(STOCKS))}")
        for i in range(0, len(todo), 20):
            chunk = todo[i:i + 20]
            d = yf.download(chunk, start="1995-01-01", interval="1d",
                            progress=False, auto_adjust=True, group_by="ticker",
                            threads=False)
            for t in chunk:
                try:
                    x = d[t].dropna() if len(chunk) > 1 else d.dropna()
                except Exception:
                    continue
                if x is None or len(x) < 400:
                    continue
                x.columns = [c.lower() for c in x.columns]
                x[["open", "high", "low", "close", "volume"]].to_csv(
                    DIR / "stocks" / f"{t}.csv")
            print(f"  {i + len(chunk)}/{len(todo)}")
            time.sleep(1)

    if a.only in ("both", "coins"):
        print(f"[암호화폐] {len(COINS)}종목 × 1d/4h")
        for s in COINS:
            for iv in ("1d", "4h"):
                p = DIR / "coins" / f"{s}_{iv}.csv"
                if p.exists() and not a.force:
                    continue
                d = fetch_binance(s, iv)
                if not d.empty:
                    d.to_csv(p)
            d = load_coin(s, "1d")
            if d is not None:
                print(f"  {s:10} {len(d):5,}일 {d.index[0].date()}~{d.index[-1].date()}")


if __name__ == "__main__":
    main()

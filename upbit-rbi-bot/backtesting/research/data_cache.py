"""
장기 히스토리 캔들 캐시 (pyupbit → CSV).

백테스트 재검증은 같은 데이터로 여러 번(스윕·walk-forward) 돌려야 하므로
네트워크에서 한 번만 받아 로컬에 저장한다. 이후 스크립트는 load()만 쓴다.

실행:  .venv/bin/python backtesting/research/data_cache.py [--force]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent / "data"

# 장기 히스토리가 실제로 존재하는 고유동성 종목 (신규상장 알트는 히스토리 부족 → 제외)
MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE", "KRW-ADA"]

# 5분봉 단타 연구용 확장 유니버스 (횡단면 신호에는 종목 수가 필요)
MARKETS_LAB = MARKETS + ["KRW-TRX", "KRW-LINK", "KRW-AVAX", "KRW-DOT", "KRW-BCH", "KRW-ETC"]
SPECS_LAB = [
    ("minute5", 60_000),    # ≈208일 (단타 walk-forward 검정력 확보)
    ("minute15", 20_000),   # ≈208일
]

# 단타 엣지의 통계적 입증에는 표본이 더 필요하다(거래 240건+ 필요, 208일로는 부족)
SPECS_LAB2 = [("minute5", 210_000)]     # ≈2년

# 유니버스 확대 검증용 중형 종목 (스프레드 ≤0.1% 통과했으나 아직 검증 안 된 종목들)
# 스프레드 중앙값 ≤0.1%를 통과한 종목만 (LPT 0.245%·AXS 0.153%·ICP 0.190%는 비용 초과로 제외)
MARKETS_MID = ["KRW-SUI", "KRW-ATOM", "KRW-ENS", "KRW-GAS", "KRW-KAITO"]
SPECS_MID = [("minute5", 210_000)]


def specs_for(mode: str):
    return {"lab": SPECS_LAB, "lab2": SPECS_LAB2, "mid": SPECS_MID}.get(mode, SPECS)

# (interval, 봉수) — 봉수 × 봉길이 ≈ 커버 기간
SPECS = [
    ("minute5", 20_000),    # ≈ 69일  (라이브 기준봉)
    ("minute15", 20_000),   # ≈ 208일
    ("minute60", 17_000),   # ≈ 708일 (약 2년)
    ("minute240", 4_400),   # ≈ 733일
    ("day", 1_200),         # ≈ 3.3년
]


def path_for(market: str, interval: str) -> Path:
    return DATA_DIR / f"{market}_{interval}.csv"


def load(market: str, interval: str) -> pd.DataFrame | None:
    """캐시된 캔들 로드. 없으면 None. columns: open/high/low/close/volume, index=datetime."""
    p = path_for(market, interval)
    if not p.exists():
        return None
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    return df.rename(columns=str.lower)


def fetch(market: str, interval: str, count: int) -> pd.DataFrame | None:
    """pyupbit로 count봉 수집(200봉 초과는 pyupbit가 내부 페이지네이션)."""
    import pyupbit
    df = pyupbit.get_ohlcv(market, interval=interval, count=count, period=0.12)
    if df is None or df.empty:
        return None
    return df.rename(columns=str.lower)


def main(force: bool = False, mode: str = "") -> None:
    """mode='lab'(208일 5분봉·12종목) / 'lab2'(2년 5분봉) / 기본(장기 다중 TF)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    specs = specs_for(mode)
    markets = (MARKETS_MID if mode == "mid"
               else MARKETS_LAB if mode.startswith("lab") else MARKETS)
    for interval, count in specs:
        for market in markets:
            p = path_for(market, interval)
            if p.exists() and not force:
                df = load(market, interval)
                if df is not None and len(df) >= count * 0.9:
                    print(f"skip  {market:9s} {interval:9s} {len(df):6d}봉 "
                          f"{df.index[0].date()}~{df.index[-1].date()} (캐시)")
                    continue
                print(f"확장  {market:9s} {interval:9s} "
                      f"{0 if df is None else len(df)}봉 → {count}봉 재수집", flush=True)
            t0 = time.time()
            try:
                df = fetch(market, interval, count)
            except Exception as e:  # 네트워크/레이트리밋
                print(f"FAIL  {market:9s} {interval:9s} {e}")
                continue
            if df is None or len(df) < 300:
                print(f"FAIL  {market:9s} {interval:9s} 데이터 부족({0 if df is None else len(df)}봉)")
                continue
            df.to_csv(p)
            print(f"save  {market:9s} {interval:9s} {len(df):6d}봉 "
                  f"{df.index[0].date()}~{df.index[-1].date()} {time.time()-t0:5.1f}s", flush=True)


if __name__ == "__main__":
    mode = next((m for m in ("lab2", "lab", "mid") if m in sys.argv), "")
    main(force="--force" in sys.argv, mode=mode)

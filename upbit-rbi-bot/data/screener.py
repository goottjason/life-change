"""
종목 스크리너 (헌장 §3, v1.2).
업비트 KRW 마켓 중 24h 거래대금 상위 N개(하한·스테이블·블랙리스트 제외)를 반환한다.
매 tick 호출해도 안전하도록 UNIVERSE_REFRESH_SEC 동안 캐시한다.
조회 실패 시 직전 캐시 → 없으면 폴백(settings.universe).
"""
from __future__ import annotations

import time

try:
    import requests
except ImportError:
    requests = None

from config import charter as C
from config.settings import settings

UPBIT_MARKET_ALL = "https://api.upbit.com/v1/market/all"
UPBIT_TICKER = "https://api.upbit.com/v1/ticker"


def select_universe(tickers: list[dict], top_n: int,
                    min_turnover: float, exclude: set[str]) -> list[str]:
    """티커 목록에서 KRW·거래대금 하한·제외 조건을 적용해 거래대금 상위 top_n 마켓 반환."""
    rows: list[tuple[str, float]] = []
    for t in tickers:
        market = t.get("market", "")
        if not market.startswith("KRW-"):
            continue
        symbol = market.split("-", 1)[1]
        if symbol in exclude:
            continue
        turnover = float(t.get("acc_trade_price_24h", 0) or 0)
        if turnover < min_turnover:
            continue
        rows.append((market, turnover))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [m for m, _ in rows[:top_n]]


class Screener:
    def __init__(self, fallback=None, top_n=None, min_turnover=None,
                 refresh_sec=None, exclude=None):
        self.fallback = list(fallback if fallback is not None else settings.universe)
        self.top_n = top_n if top_n is not None else C.UNIVERSE_TOP_N
        self.min_turnover = min_turnover if min_turnover is not None else C.MIN_TURNOVER_24H_KRW
        self.refresh_sec = refresh_sec if refresh_sec is not None else C.UNIVERSE_REFRESH_SEC
        self.exclude = exclude if exclude is not None else (C.STABLECOINS | C.UNIVERSE_BLACKLIST)
        self._cache: list[str] = []
        self._last = 0.0

    def eligible(self) -> list[str]:
        now = time.monotonic()
        if self._cache and (now - self._last) < self.refresh_sec:
            return self._cache
        try:
            picked = select_universe(self._fetch_tickers(), self.top_n,
                                     self.min_turnover, self.exclude)
        except Exception:
            picked = []
        if picked:
            self._cache = picked
            self._last = now
            return picked
        return self._cache or self.fallback

    def _fetch_tickers(self) -> list[dict]:
        if requests is None:
            raise RuntimeError("requests 미설치")
        r1 = requests.get(UPBIT_MARKET_ALL,
                          params={"isDetails": "false"}, timeout=5)
        r1.raise_for_status()
        markets = r1.json()
        krw = [m["market"] for m in markets
               if str(m.get("market", "")).startswith("KRW-")]
        r2 = requests.get(UPBIT_TICKER,
                          params={"markets": ",".join(krw)}, timeout=5)
        r2.raise_for_status()
        return r2.json()

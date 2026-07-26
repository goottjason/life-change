"""
종목 스크리너 (헌장 §3, v1.2 + 스프레드 필터 v1.3).

업비트 KRW 마켓 중 24h 거래대금 상위 N개(하한·스테이블·블랙리스트 제외)를 반환하고,
**호가 스프레드가 넓은 종목을 제외**한다.

스프레드 필터가 필요한 이유 (2026-07-26 백테스트 연구 결과):
 업비트 KRW는 가격대별 호가 단위(tick)가 고정이라 가격이 낮은 코인은 한 틱이 이미 0.2~0.9%다.
 실측 스프레드: DOGE 0.930%, ADA 0.412%, TRX 0.206% vs BTC 0.067%, BCH 0.033%.
 거래대금만 보고 뽑으면 이런 저가 코인이 상위에 오는데, 왕복 스프레드가 거래당 기댓값(≈0.18%)을
 넘으므로 **어떤 신호를 써도 구조적으로 손실**이다. 실제로 12종목 전체에 실측 스프레드를 적용하면
 검증된 전략조차 계좌 −64%가 된다.

매 tick 호출해도 안전하도록 UNIVERSE_REFRESH_SEC 동안 캐시한다.
조회 실패 시 직전 캐시(이미 스프레드 검증됨) → 없으면 폴백(settings.universe).
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
UPBIT_ORDERBOOK = "https://api.upbit.com/v1/orderbook"


def spread_ratio(unit: dict) -> float | None:
    """최우선 호가의 (매도−매수)/중간가. 계산 불가면 None."""
    try:
        bid = float(unit["bid_price"])
        ask = float(unit["ask_price"])
    except (KeyError, TypeError, ValueError):
        return None
    mid = (bid + ask) / 2
    if mid <= 0 or ask < bid:
        return None
    return (ask - bid) / mid


def top_depth_krw(unit: dict) -> float:
    """최우선 매도호가에 걸린 금액(원). 이 금액보다 큰 주문은 호가를 타고 올라간다."""
    try:
        return float(unit["ask_price"]) * float(unit["ask_size"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def filter_by_spread(books: list[dict], max_spread: float,
                     min_depth_krw: float = 0.0) -> tuple[list[str], dict[str, float]]:
    """
    오더북 응답에서 스프레드 상한(+최우선 호가 잔량 하한)을 통과한 마켓과 측정값을 반환한다.

    잔량까지 보는 이유: 스프레드가 좁아도 최우선 호가에 걸린 금액이 주문금액보다 작으면
    다음 호가까지 먹으며 체결돼 실제 비용이 측정값보다 커진다.
    """
    passed: list[str] = []
    measured: dict[str, float] = {}
    for b in books or []:
        market = b.get("market", "")
        units = b.get("orderbook_units") or []
        if not market or not units:
            continue
        s = spread_ratio(units[0])
        if s is None:
            continue
        measured[market] = s
        if s > max_spread:
            continue
        if min_depth_krw and top_depth_krw(units[0]) < min_depth_krw:
            continue
        passed.append(market)
    return passed, measured


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
                 refresh_sec=None, exclude=None, notifier=None, max_spread=None):
        self.fallback = list(fallback if fallback is not None else settings.universe)
        self.top_n = top_n if top_n is not None else C.UNIVERSE_TOP_N
        self.min_turnover = min_turnover if min_turnover is not None else C.MIN_TURNOVER_24H_KRW
        self.refresh_sec = refresh_sec if refresh_sec is not None else C.UNIVERSE_REFRESH_SEC
        self.exclude = exclude if exclude is not None else (C.STABLECOINS | C.UNIVERSE_BLACKLIST)
        self.max_spread = max_spread if max_spread is not None else C.MAX_SPREAD_RATIO
        self.min_depth_krw = C.MIN_TOP_DEPTH_KRW
        self.notifier = notifier
        self._cache: list[str] = []
        self._last = None
        self._in_fallback = False
        self.spreads: dict[str, float] = {}      # 대시보드/로그용 최근 측정값
        self.rejected: dict[str, float] = {}     # 스프레드 초과로 탈락한 종목

    def _notify(self, msg):
        self.notifier.send(msg) if self.notifier else print(msg)

    def eligible(self) -> list[str]:
        now = time.monotonic()
        if self._last is not None and (now - self._last) < self.refresh_sec:
            return self._cache or self.fallback
        try:
            # 스프레드 필터로 탈락할 것을 감안해 후보를 넉넉히 뽑은 뒤 좁힌다 (§3, v1.3)
            candidates = select_universe(self._fetch_tickers(),
                                         self.top_n * C.SPREAD_CANDIDATE_MULT,
                                         self.min_turnover, self.exclude)
            picked = self._apply_spread_filter(candidates)[:self.top_n]
        except Exception:
            picked = []
        self._last = now   # throttle regardless of success/failure (outage-safe)
        if picked:
            if self._in_fallback:
                self._notify("✅ 스크리너 복구 — 동적 유니버스 재개")
                self._in_fallback = False
            self._cache = picked
            return picked
        if not self._in_fallback:
            self._notify("⚠️ 스크리너 조회 실패 — 폴백 유니버스 사용 (§3)")
            self._in_fallback = True
        return self._cache or self.fallback

    def _apply_spread_filter(self, candidates: list[str]) -> list[str]:
        """
        거래대금 순서를 유지하면서 스프레드 상한을 통과한 종목만 남긴다.
        오더북 조회가 실패하면 필터를 '통과 처리'하지 않고 **빈 목록**을 반환한다
        → 상위 호출부가 직전 캐시(이미 검증된 유니버스)나 폴백을 쓰게 되므로,
          스프레드를 확인하지 못한 종목으로 실거래하는 상황을 만들지 않는다.
        """
        if not candidates:
            return []
        try:
            books = self._fetch_orderbooks(candidates)
        except Exception:
            self._notify("⚠️ 오더북 조회 실패 — 스프레드 미확인 종목 거래 금지, 직전 유니버스 유지 (§3)")
            return []
        passed, measured = filter_by_spread(books, self.max_spread, self.min_depth_krw)
        self.spreads = measured
        ok = set(passed)
        # 탈락 사유 구분: 스프레드 초과 vs 잔량 부족
        self.rejected = {m: s for m, s in measured.items()
                         if s > self.max_spread or m not in ok}
        ordered = [m for m in candidates if m in ok]
        if self.rejected:
            worst = sorted(self.rejected.items(), key=lambda kv: -kv[1])[:3]
            detail = ", ".join(f"{m.replace('KRW-', '')} {s:.2%}" for m, s in worst)
            print(f"[screener] 스프레드 초과 제외 {len(self.rejected)}종목 (상한 "
                  f"{self.max_spread:.2%}): {detail}")
        return ordered

    def _fetch_orderbooks(self, markets: list[str]) -> list[dict]:
        if requests is None:
            raise RuntimeError("requests 미설치")
        r = requests.get(UPBIT_ORDERBOOK, params={"markets": ",".join(markets)}, timeout=5)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else [data]

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

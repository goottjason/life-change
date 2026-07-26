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

import statistics
import time
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    requests = None

from config import charter as C
from config.settings import settings

UPBIT_MARKET_ALL = "https://api.upbit.com/v1/market/all"
UPBIT_TICKER = "https://api.upbit.com/v1/ticker"
UPBIT_ORDERBOOK = "https://api.upbit.com/v1/orderbook"
UPBIT_DAY_CANDLES = "https://api.upbit.com/v1/candles/days"


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


def cap_for(market: str, default_cap: float) -> float:
    """종목별 스프레드 상한 (§3.2-h, v2.1).

    15분봉 검증을 통과한 종목은 완화된 상한을 쓴다(그 종목은 5분봉 진입이 금지된다).
    미검증 종목에는 기본 상한을 그대로 적용한다.
    """
    return C.WIDE_SPREAD_ALLOWED.get(market.split("-", 1)[-1], default_cap)


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
        if s > cap_for(market, max_spread):
            continue
        if min_depth_krw and top_depth_krw(units[0]) < min_depth_krw:
            continue
        passed.append(market)
    return passed, measured


def flagged_markets(markets_detail: list[dict]) -> set[str]:
    """
    투자경고·투자주의 종목 집합 (§3, v2.0). `market/all?isDetails=true` 응답을 받는다.
    경고(warning)=상장폐지 검토 등, 주의(caution)=가격급등락·거래량급증·소수계정 집중 등 조작 징후.
    거래대금 하한이 어설프게 대리하던 위험을 여기서 직접 차단한다.
    """
    out: set[str] = set()
    for m in markets_detail or []:
        ev = m.get("market_event") or {}
        if C.EXCLUDE_MARKET_WARNING and ev.get("warning"):
            out.add(m.get("market", ""))
            continue
        if C.EXCLUDE_MARKET_CAUTION and any((ev.get("caution") or {}).values()):
            out.add(m.get("market", ""))
    return {m for m in out if m}


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
        self.min_listing_days = C.MIN_LISTING_DAYS
        self._history_ok: dict[str, bool] = {}   # 상장 경과일 판정 캐시(상장일은 불변)
        self.rejected_new: list[str] = []        # 신규 상장으로 제외된 종목
        self.notifier = notifier
        self._cache: list[str] = []
        self._last = None
        self._in_fallback = False
        self.spreads: dict[str, float] = {}      # 대시보드/로그용 최근 측정값
        self.rejected: dict[str, float] = {}     # 스프레드 초과로 탈락한 종목
        self._spread_hist: dict[str, list[float]] = {}   # 종목별 스프레드 관측 이력 (v1.7)
        self.rejected_unstable: list[str] = []   # 스프레드 변동이 큰 종목
        self.rejected_flagged: list[str] = []    # 투자경고/주의로 제외된 종목
        self.skipped_unvalidated = 0             # 미검증으로 제외된 후보 수

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
            flagged = self._flagged()                              # 투자경고/주의 (v2.0)
            if flagged:
                dropped = [m for m in candidates if m in flagged]
                if dropped:
                    self.rejected_flagged = dropped
                    print("[screener] 투자경고/주의 제외: " + ", ".join(
                        m.replace("KRW-", "") for m in dropped))
                candidates = [m for m in candidates if m not in flagged]
            candidates = self._validated_only(candidates)         # 검증 종목만 (§3.2-i, v2.1)
            candidates = self._apply_history_filter(candidates)   # 신규 상장 배제 (v1.5)
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

    def _validated_only(self, candidates: list[str]) -> list[str]:
        """
        헌장 §0.3 "검증 없이 투입 없음" — 개별 백테스트를 통과한 종목만 남긴다 (§3.2-i, v2.1).
        스프레드가 좁아도 신호가 안 먹히는 종목이 있다(KAITO: 0.084%인데 양쪽 타임프레임 음수).
        새 종목은 `lab_universe.py` 로 검증한 뒤 charter.VALIDATED_MARKETS 에 등록한다.
        """
        if not (C.REQUIRE_VALIDATED_MARKET and getattr(self, "_require_validated", True)):
            return candidates
        kept = [m for m in candidates if m.split("-", 1)[-1] in C.VALIDATED_MARKETS]
        self.skipped_unvalidated = len(candidates) - len(kept)
        return kept

    # ── 상장 경과일 필터 (§3, v1.5) ─────────────────────────
    def _has_min_history(self, market: str) -> bool | None:
        """
        MIN_LISTING_DAYS 이전에도 일봉이 존재하는지 = 그때 이미 상장돼 있었는지.
        True(충분)/False(신규 상장)/None(조회 실패 — 판정 불가).

        **조회 실패를 False로 캐시하면 안 된다**: 레이트리밋 한 번에 ETH 같은 오래된 종목이
        영구 제외돼 유니버스가 비정상적으로 줄어든다(실제로 그 버그를 겪었다).
        확정 응답만 메모이즈하고(상장일은 불변), 실패는 다음 주기에 다시 시도한다.
        """
        if market in self._history_ok:
            return self._history_ok[market]
        try:
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(days=self.min_listing_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            r = requests.get(UPBIT_DAY_CANDLES,
                             params={"market": market, "count": 1, "to": cutoff}, timeout=5)
            r.raise_for_status()
            ok = bool(r.json())
        except Exception:
            return None                      # 판정 불가 — 캐시하지 않음
        self._history_ok[market] = ok
        return ok

    def _apply_history_filter(self, markets: list[str]) -> list[str]:
        """상장 경과일이 확인된 종목만 남긴다. 판정 불가(API 실패)는 이번 주기에서만 보류."""
        kept, new_listings, unknown = [], [], []
        for m in markets:
            if m in self._history_ok:                 # 캐시 히트는 요청하지 않는다
                (kept if self._history_ok[m] else new_listings).append(m)
                continue
            time.sleep(0.15)                          # 레이트리밋 회피 (≈6.7 req/s)
            res = self._has_min_history(m)
            if res is True:
                kept.append(m)
            elif res is False:
                new_listings.append(m)
            else:
                unknown.append(m)
        self.rejected_new = new_listings
        if new_listings:
            print(f"[screener] 상장 {self.min_listing_days}일 미만 제외: "
                  f"{', '.join(m.replace('KRW-', '') for m in new_listings)}")
        if unknown:
            print(f"[screener] 상장일 확인 실패(다음 주기 재시도): "
                  f"{', '.join(m.replace('KRW-', '') for m in unknown)}")
        return kept

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
        # 스프레드 안정성 판정 (v1.7): 관측 이력을 쌓아 '가끔만 좁아지는 종목'을 걸러낸다.
        for m, sp in measured.items():
            hist = self._spread_hist.setdefault(m, [])
            hist.append(sp)
            del hist[:-C.SPREAD_HISTORY_LEN]
        unstable = {m for m in list(ok) if not self._spread_stable(m)}
        if unstable:
            ok -= unstable
            self.rejected_unstable = sorted(unstable)
            print(f"[screener] 스프레드 불안정 제외: " + ", ".join(
                f"{m.replace('KRW-','')} 중앙 {statistics.median(self._spread_hist[m]):.3%}"
                f"/최대 {max(self._spread_hist[m]):.3%}" for m in self.rejected_unstable))
        else:
            self.rejected_unstable = []
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

    def _spread_stable(self, market: str) -> bool:
        """
        최근 관측 이력으로 '스프레드가 꾸준히 좁은가'를 본다 (v1.7).
        표본이 SPREAD_HISTORY_MIN_SAMPLES 미만이면 판정하지 않는다(관측을 더 모은다).
        기준: 중앙값 ≤ 상한 AND 최댓값 ≤ 상한 × SPREAD_MAX_MULT.
        이유: 진입 직전 확인은 진입 쪽만 보장하고, 청산 시점 스프레드는 진입할 때 알 수 없다.
        """
        hist = self._spread_hist.get(market, [])
        if len(hist) < C.SPREAD_HISTORY_MIN_SAMPLES:
            return True
        cap = cap_for(market, self.max_spread)
        return (statistics.median(hist) <= cap
                and max(hist) <= cap * C.SPREAD_MAX_MULT)

    def spread_stats(self) -> dict[str, dict]:
        """대시보드용: 종목별 스프레드 중앙값·최댓값·관측수."""
        return {m: {"median": round(statistics.median(h) * 100, 3),
                    "max": round(max(h) * 100, 3), "n": len(h)}
                for m, h in self._spread_hist.items() if h}

    # ── 진입 직전 스프레드 재확인 (§6, v1.6) ─────────────────
    def spread_now(self, market: str) -> float | None:
        """해당 종목의 현재 스프레드. 조회 실패 시 None."""
        try:
            books = self._fetch_orderbooks([market])
        except Exception:
            return None
        for b in books or []:
            if b.get("market") == market and b.get("orderbook_units"):
                return spread_ratio(b["orderbook_units"][0])
        return None

    def tradable_now(self, market: str, cap: float | None = None) -> tuple[bool, str]:
        """
        주문 직전 호출용. 스프레드가 상한 이내인지 지금 다시 확인한다.
        유니버스는 10분 주기로 갱신되므로 스크리닝 시점 값은 최신이 아니다.
        조회 실패는 '확인 불가 → 진입 금지'로 처리한다(미확인 비용으로 실거래 금지).
        """
        limit = cap if cap is not None else cap_for(market, self.max_spread)
        s = self.spread_now(market)
        if s is None:
            return False, "스프레드 확인 실패"
        self.spreads[market] = s
        if s > limit:
            return False, f"스프레드 {s:.3%} > 상한 {limit:.2%}"
        return True, f"스프레드 {s:.3%}"

    def _fetch_orderbooks(self, markets: list[str]) -> list[dict]:
        if requests is None:
            raise RuntimeError("requests 미설치")
        r = requests.get(UPBIT_ORDERBOOK, params={"markets": ",".join(markets)}, timeout=5)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else [data]

    def _flagged(self) -> set[str]:
        """투자경고/주의 종목. 조회 실패 시 빈 집합(다른 필터가 계속 작동한다)."""
        try:
            return flagged_markets(self._fetch_markets_detail())
        except Exception:
            return set()

    def _fetch_markets_detail(self) -> list[dict]:
        if requests is None:
            raise RuntimeError("requests 미설치")
        r = requests.get(UPBIT_MARKET_ALL, params={"isDetails": "true"}, timeout=5)
        r.raise_for_status()
        return r.json()

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

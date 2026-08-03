"""
업비트 Open API 래퍼 (pyupbit 기반).
헌장 §12: py-clob-client(Polymarket) 폐기 → 업비트로 교체.
DRY_RUN 모드에서는 실제 주문을 내지 않고 로깅만 한다(헌장 §11 인큐베이션 안전).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd

try:
    import pyupbit
except ImportError:  # 설계 단계에서 미설치여도 import 실패하지 않게
    pyupbit = None

from config.settings import settings
from config.charter import BASE_TIMEFRAME


# ── 시세 조회 스로틀 (v1.4) ──────────────────────────────────
# 업비트 시세 API는 초당 요청 수 제한이 있다. v1.4에서 전략이 5분봉·15분봉을 함께 쓰면서
# tick당 요청이 (종목수 × 타임프레임수 + 추세조회)로 늘어 순간적으로 한도를 넘겨
# 캔들 조회가 실패했다(= 진입 기회 상실). 호출 간 최소 간격을 두고, 실패 시 1회 재시도한다.
_QUOTE_MIN_INTERVAL = 0.15      # 초 (≈6.7 req/s)
_last_quote_at = 0.0


def _throttle() -> None:
    global _last_quote_at
    wait = _QUOTE_MIN_INTERVAL - (time.monotonic() - _last_quote_at)
    if wait > 0:
        time.sleep(wait)
    _last_quote_at = time.monotonic()


@dataclass
class OrderResult:
    ok: bool
    order_id: str = ""
    filled_volume: float = 0.0
    avg_price: float = 0.0
    raw: dict | None = None
    error: str = ""


class UpbitClient:
    def __init__(self):
        self._upbit = None
        if not settings.dry_run and pyupbit is not None:
            self._upbit = pyupbit.Upbit(settings.upbit_access_key, settings.upbit_secret_key)

    # ── 시세/캔들 (인증 불필요) ──────────────────────────────
    def get_candles(self, market: str, interval: str = BASE_TIMEFRAME,
                    count: int = 200) -> pd.DataFrame:
        """OHLCV 캔들. columns: open/high/low/close/volume, index=datetime."""
        if pyupbit is None:
            raise RuntimeError("pyupbit 미설치 — pip install pyupbit")
        for attempt in (1, 2):                  # 레이트리밋 등 일시 실패 1회 재시도 (v1.4)
            _throttle()
            df = pyupbit.get_ohlcv(market, interval=interval, count=count)
            if df is not None and not df.empty:
                return df.rename(columns=str.lower)
            if attempt == 1:
                time.sleep(0.4)
        raise RuntimeError(f"candle fetch failed: {market} ({interval})")

    def get_price(self, market: str) -> float:
        return float(pyupbit.get_current_price(market))

    # ── 주문 (인증 필요) — 헌장 §6 ───────────────────────────
    #
    # ⚠ pyupbit 의 주문 메서드(buy_limit_order/sell_limit_order/sell_market_order)는 쓰지
    #   않는다. 세 메서드 모두 본문이 `except Exception as x: print(x.__class__.__name__);
    #   return None` 이라 **실패 원인을 통째로 삼키고 None 을 돌려준다**.
    #   실매매에서 이 때문에 `unexpected response: None` 만 2,854건 쌓였고(2026-07-31,
    #   10시간 42분) 원인 파악이 불가능했다. 실제 원인은 5,000원 미만 먼지 잔량이었는데
    #   pyupbit 가 raise 한 typed exception 이 지워져 그 사실이 로그에 남지 않았다.
    #   그래서 인증 헤더 생성만 빌려 쓰고 POST 는 직접 호출한다 → 429/400/타임아웃이
    #   원문 그대로 reason 에 남는다.
    def _place_order(self, data: dict) -> OrderResult:
        try:
            from pyupbit.request_api import _send_post_request
            headers = self._upbit._request_headers(data)
            resp, _ = _send_post_request("https://api.upbit.com/v1/orders",
                                         headers=headers, data=data)
        except Exception as e:
            return OrderResult(ok=False, error=f"{type(e).__name__}: {e}".strip(": "))
        return self._parse(resp)

    def buy_limit(self, market: str, price: float, krw: float) -> OrderResult:
        volume = krw / price
        if settings.dry_run:
            return OrderResult(ok=True, order_id="DRY", filled_volume=volume, avg_price=price)
        return self._place_order({
            "market": market, "side": "bid", "ord_type": "limit",
            "price": str(pyupbit.get_tick_size(price)), "volume": f"{volume:.8f}",
        })

    def sell_limit(self, market: str, price: float, volume: float) -> OrderResult:
        if settings.dry_run:
            return OrderResult(ok=True, order_id="DRY", filled_volume=volume, avg_price=price)
        return self._place_order({
            "market": market, "side": "ask", "ord_type": "limit",
            "price": str(pyupbit.get_tick_size(price)), "volume": f"{volume:.8f}",
        })

    def sell_market(self, market: str, volume: float) -> OrderResult:
        """손절 전용 — 체결 확실성 우선 (§6.2)."""
        if settings.dry_run:
            price = self.get_price(market)
            return OrderResult(ok=True, order_id="DRY", filled_volume=volume, avg_price=price)
        return self._place_order({
            "market": market, "side": "ask", "ord_type": "market",
            "volume": f"{volume:.8f}",
        })

    def cancel(self, order_id: str) -> bool:
        if settings.dry_run:
            return True
        self._upbit.cancel_order(order_id)
        return True

    def get_open_orders(self, market: str) -> list[dict]:
        if settings.dry_run or self._upbit is None:
            return []
        return self._upbit.get_order(market, state="wait") or []

    def get_order_detail(self, uuid: str) -> dict | None:
        """단일 주문 상세(체결 확인용, §6.3). state/executed_volume/trades 포함."""
        if settings.dry_run or self._upbit is None or not uuid or uuid == "DRY":
            return None
        try:
            return self._upbit.get_order(uuid)
        except Exception:
            return None

    def get_last_buy_time(self, market: str) -> float | None:
        """
        해당 마켓의 가장 최근 '체결된 매수' 시각(epoch초). 오펀 포지션 복구용(§9.3).
        조회 실패/없음이면 None(호출측이 현재시각으로 폴백).
        """
        if settings.dry_run or self._upbit is None:
            return None
        try:
            orders = self._upbit.get_order(market, state="done") or []
        except Exception:
            return None
        buys = [o for o in orders if o.get("side") == "bid" and o.get("created_at")]
        if not buys:
            return None
        buys.sort(key=lambda o: o["created_at"], reverse=True)
        try:
            return pd.Timestamp(buys[0]["created_at"]).timestamp()
        except Exception:
            return None

    def get_balances(self) -> list[dict]:
        """상태 복구용 (§9.3)."""
        if settings.dry_run or self._upbit is None:
            return []
        return self._upbit.get_balances() or []

    # ── 자본/잔고 (동적 포지션 사이징용, 헌장 §7.1) ──────────────
    def get_account_equity(self, price_lookup) -> tuple[float, float]:
        """
        (주문가능 원화, 총 자산) 반환.
          - 총 자산 = 원화(가용+잠금) + Σ(보유코인 수량 × 현재가)
          - price_lookup(market) → 현재가(없으면 None). 캐시에 없으면 get_price 로 조회.
        DRY_RUN(모의)에서는 계좌가 없으므로 폴백 자본을 그대로 사용한다.
        """
        if settings.dry_run or self._upbit is None:
            cap = settings.paper_capital_krw
            return cap, cap
        try:
            bals = self._upbit.get_balances() or []
        except Exception:
            cap = settings.paper_capital_krw
            return cap, cap
        available_krw = 0.0
        krw_total = 0.0
        coin_value = 0.0
        for b in bals:
            cur = b.get("currency")
            bal = float(b.get("balance", 0) or 0)
            locked = float(b.get("locked", 0) or 0)
            if cur == "KRW":
                available_krw += bal
                krw_total += bal + locked
                continue
            qty = bal + locked
            if qty <= 0:
                continue
            market = f"KRW-{cur}"
            px = price_lookup(market) if price_lookup else None
            if not px:
                try:
                    px = self.get_price(market)
                except Exception:
                    px = 0.0
            coin_value += qty * (px or 0.0)
        total_equity = krw_total + coin_value
        # 계좌가 비어 있으면(0원) 폴백으로 대체해 서킷/사이징이 0으로 붕괴하지 않게 함
        if total_equity <= 0:
            cap = settings.paper_capital_krw
            return 0.0, cap
        return available_krw, total_equity

    @staticmethod
    def _parse(resp: dict | None) -> OrderResult:
        # 업비트 에러 응답: {'error': {'name': ..., 'message': ...}} → 원인을 reason 에 담는다.
        if isinstance(resp, dict) and resp.get("error"):
            err = resp["error"]
            if isinstance(err, dict):
                name = err.get("name", "")
                msg = err.get("message", "")
                return OrderResult(ok=False, error=f"{name}: {msg}".strip(" :"))
            return OrderResult(ok=False, error=str(err))
        if not resp or "uuid" not in resp:
            return OrderResult(ok=False, error=f"unexpected response: {resp}")
        # 주문 접수 응답엔 보통 체결정보가 없다(0). 실제 체결량은 get_order_detail 로 확인한다.
        vol, avg = avg_from_order(resp)
        return OrderResult(ok=True, order_id=resp["uuid"],
                           filled_volume=vol, avg_price=avg, raw=resp)


def avg_from_order(detail: dict) -> tuple[float, float]:
    """주문 상세에서 (체결수량, 평단가) 계산. trades[].funds/volume 우선."""
    executed = float(detail.get("executed_volume", 0) or 0)
    trades = detail.get("trades") or []
    funds = 0.0
    tvol = 0.0
    for t in trades:
        funds += float(t.get("funds", 0) or 0)
        tvol += float(t.get("volume", 0) or 0)
    avg = (funds / tvol) if tvol > 0 else 0.0
    vol = executed or tvol
    return vol, avg

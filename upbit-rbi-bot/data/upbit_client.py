"""
업비트 Open API 래퍼 (pyupbit 기반).
헌장 §12: py-clob-client(Polymarket) 폐기 → 업비트로 교체.
DRY_RUN 모드에서는 실제 주문을 내지 않고 로깅만 한다(헌장 §11 인큐베이션 안전).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

try:
    import pyupbit
except ImportError:  # 설계 단계에서 미설치여도 import 실패하지 않게
    pyupbit = None

from config.settings import settings
from config.charter import BASE_TIMEFRAME


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
        df = pyupbit.get_ohlcv(market, interval=interval, count=count)
        if df is None:
            raise RuntimeError(f"candle fetch failed: {market}")
        return df.rename(columns=str.lower)

    def get_price(self, market: str) -> float:
        return float(pyupbit.get_current_price(market))

    # ── 주문 (인증 필요) — 헌장 §6 ───────────────────────────
    def buy_limit(self, market: str, price: float, krw: float) -> OrderResult:
        volume = krw / price
        if settings.dry_run:
            return OrderResult(ok=True, order_id="DRY", filled_volume=volume, avg_price=price)
        resp = self._upbit.buy_limit_order(market, price, volume)
        return self._parse(resp)

    def sell_limit(self, market: str, price: float, volume: float) -> OrderResult:
        if settings.dry_run:
            return OrderResult(ok=True, order_id="DRY", filled_volume=volume, avg_price=price)
        resp = self._upbit.sell_limit_order(market, price, volume)
        return self._parse(resp)

    def sell_market(self, market: str, volume: float) -> OrderResult:
        """손절 전용 — 체결 확실성 우선 (§6.2)."""
        if settings.dry_run:
            price = self.get_price(market)
            return OrderResult(ok=True, order_id="DRY", filled_volume=volume, avg_price=price)
        resp = self._upbit.sell_market_order(market, volume)
        return self._parse(resp)

    def cancel(self, order_id: str) -> bool:
        if settings.dry_run:
            return True
        self._upbit.cancel_order(order_id)
        return True

    def get_open_orders(self, market: str) -> list[dict]:
        if settings.dry_run or self._upbit is None:
            return []
        return self._upbit.get_order(market, state="wait") or []

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
        if not resp or "uuid" not in resp:
            return OrderResult(ok=False, error=str(resp))
        return OrderResult(ok=True, order_id=resp["uuid"], raw=resp)

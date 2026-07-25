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

    @staticmethod
    def _parse(resp: dict | None) -> OrderResult:
        if not resp or "uuid" not in resp:
            return OrderResult(ok=False, error=str(resp))
        return OrderResult(ok=True, order_id=resp["uuid"], raw=resp)

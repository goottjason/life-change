"""
주문 집행 (헌장 §6).
- 진입·익절·죽은포지션 청산 → 지정가 우선
- 손절 → 시장가 허용 (체결 확실성)
- 지정가 미체결 30초 → 취소 → 1회 재주문 → 포기 (추격매수 금지)
- 신규 주문 전 미체결 정리(중복 방지)
"""
from __future__ import annotations

import time

from config.charter import (
    LIMIT_UNFILLED_TIMEOUT_SEC, LIMIT_REORDER_MAX, MIN_ORDER_KRW,
)
from data.upbit_client import UpbitClient, OrderResult
from bot.position import Position, ExitReason


class OrderManager:
    def __init__(self, client: UpbitClient):
        self.client = client

    # ── 진입 (§6.1, §6.3) ────────────────────────────────────
    def enter_long(self, market: str, price: float, krw: float) -> OrderResult:
        if krw < MIN_ORDER_KRW:
            return OrderResult(ok=False, error=f"below min order {MIN_ORDER_KRW}")
        self._clear_open_orders(market)  # 중복 방지 (§6.6)

        attempts = 0
        while attempts <= LIMIT_REORDER_MAX:
            res = self.client.buy_limit(market, price, krw)
            if not res.ok:
                return res
            if self._wait_fill(market, res):
                return res
            self.client.cancel(res.order_id)  # 미체결 → 취소 (§6.3)
            attempts += 1
        return OrderResult(ok=False, error="entry unfilled — skip bar (§6.3)")

    # ── 청산 (§6.1, §6.2) ────────────────────────────────────
    def exit_position(self, pos: Position, price: float, reason: ExitReason) -> OrderResult:
        if reason == ExitReason.STOP_LOSS or reason == ExitReason.KILL_SWITCH:
            # 반드시 나가야 함 → 시장가 (§6.2, §9.1)
            return self.client.sell_market(pos.market, pos.volume)
        # 익절/역방향/죽은포지션 → 지정가 우선 (§6.1)
        res = self.client.sell_limit(pos.market, price, pos.volume)
        if res.ok and not self._wait_fill(pos.market, res):
            self.client.cancel(res.order_id)
            return self.client.sell_market(pos.market, pos.volume)  # 폴백: 확실히 청산
        return res

    # ── 내부 헬퍼 ────────────────────────────────────────────
    def _wait_fill(self, market: str, res: OrderResult) -> bool:
        """지정가 체결 대기 (§6.3). DRY_RUN이면 즉시 체결로 간주."""
        if res.order_id == "DRY":
            return True
        deadline = time.monotonic() + LIMIT_UNFILLED_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if not self.client.get_open_orders(market):
                return True
            time.sleep(1)
        return False

    def _clear_open_orders(self, market: str) -> None:
        for o in self.client.get_open_orders(market):
            self.client.cancel(o.get("uuid", ""))

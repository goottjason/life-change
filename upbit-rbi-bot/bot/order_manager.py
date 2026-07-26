"""
주문 집행 (헌장 §6).
- 진입·익절·죽은포지션 청산 → 지정가 우선
- 손절 → 시장가 허용 (체결 확실성)
- 지정가 미체결 30초 → 취소 → 시장가 강제청산 (오펀 포지션 방지)
- 신규 주문 전 미체결 정리(중복 방지)
- 모든 주문은 '실제 체결량(executed_volume)'을 조회해 확인한다.
  체결 0이면 성공으로 취급하지 않는다(상태 desync 방지).
"""
from __future__ import annotations

import time

from config.charter import (
    LIMIT_UNFILLED_TIMEOUT_SEC, LIMIT_REORDER_MAX, MIN_ORDER_KRW,
)
from data.upbit_client import UpbitClient, OrderResult, avg_from_order
from bot.position import Position, ExitReason


class OrderManager:
    def __init__(self, client: UpbitClient,
                 poll_timeout_sec: int = LIMIT_UNFILLED_TIMEOUT_SEC,
                 poll_interval_sec: float = 1.0):
        self.client = client
        self.poll_timeout_sec = poll_timeout_sec
        self.poll_interval_sec = poll_interval_sec

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
            self._wait_fill(market, res)
            res = self._confirm_fill(res, expected_volume=krw / price)
            if res.filled_volume > 0:
                return res
            self.client.cancel(res.order_id)  # 미체결 → 취소 (§6.3)
            attempts += 1
        return OrderResult(ok=False, error="entry unfilled — skip bar (§6.3)")

    # ── 청산 (§6.1, §6.2) ────────────────────────────────────
    def exit_position(self, pos: Position, price: float, reason: ExitReason) -> OrderResult:
        if reason == ExitReason.STOP_LOSS or reason == ExitReason.KILL_SWITCH:
            # 반드시 나가야 함 → 시장가 (§6.2, §9.1)
            return self._sell_market_confirmed(pos.market, pos.volume)

        # 익절/역방향/죽은포지션 → 지정가 우선 (§6.1)
        res = self.client.sell_limit(pos.market, price, pos.volume)
        filled = 0.0
        if res.ok:
            self._wait_fill(pos.market, res)
            res = self._confirm_fill(res, expected_volume=pos.volume)
            filled = res.filled_volume
            if filled >= pos.volume * (1 - 1e-9):
                return res                     # 전량 체결
            self.client.cancel(res.order_id)   # 부분/미체결 → 취소

        # 미체결/부분체결/주문실패 → 남은 수량 시장가 강제청산 (오펀 방지, 사용자 정책)
        remaining = max(0.0, pos.volume - filled)
        mkt = self._sell_market_confirmed(pos.market, remaining)
        mkt.filled_volume += filled            # 지정가에서 판 부분 합산
        return mkt

    # ── 내부 헬퍼 ────────────────────────────────────────────
    def _sell_market_confirmed(self, market: str, volume: float) -> OrderResult:
        if volume <= 0:
            return OrderResult(ok=False, error="nothing to sell (volume<=0)")
        res = self.client.sell_market(market, volume)
        if not res.ok:
            return res
        return self._confirm_fill(res, expected_volume=volume)

    def _confirm_fill(self, res: OrderResult, expected_volume: float) -> OrderResult:
        """
        주문의 '실제 체결량/평단'을 조회해 OrderResult 에 채운다 (§6.3).
        - DRY: filled_volume 없으면 expected 로 간주(모의 즉시체결).
        - 실주문: get_order_detail 로 executed_volume 확인. 0 이면 미체결로 남긴다.
        """
        if not res.ok:
            return res
        if res.order_id == "DRY":
            if res.filled_volume <= 0:
                res.filled_volume = expected_volume
            return res
        if res.filled_volume > 0:  # 접수 응답에 이미 체결정보가 있으면 그대로
            return res
        deadline = time.monotonic() + self.poll_timeout_sec
        detail = None
        while True:
            detail = self.client.get_order_detail(res.order_id)
            if detail:
                executed = float(detail.get("executed_volume", 0) or 0)
                state = detail.get("state")
                if executed > 0 or state == "done" or state == "cancel":
                    break
            if time.monotonic() >= deadline:
                break
            time.sleep(self.poll_interval_sec)
        if detail:
            vol, avg = avg_from_order(detail)
            res.filled_volume = vol
            if avg > 0:
                res.avg_price = avg
        res.ok = res.filled_volume > 0
        if not res.ok and not res.error:
            res.error = "no fill confirmed"
        return res

    def _wait_fill(self, market: str, res: OrderResult) -> bool:
        """지정가 체결 대기 (§6.3). DRY_RUN이면 즉시 체결로 간주."""
        if res.order_id == "DRY":
            return True
        deadline = time.monotonic() + self.poll_timeout_sec
        while True:
            if not self.client.get_open_orders(market):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.poll_interval_sec)

    def _clear_open_orders(self, market: str) -> None:
        for o in self.client.get_open_orders(market):
            self.client.cancel(o.get("uuid", ""))

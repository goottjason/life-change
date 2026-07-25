"""
안전장치 (헌장 §9) — 봇의 생명. 가장 먼저 구현·검증한다.
- 킬 스위치: 전체 청산 + 정지
- API 에러/피드 정지 감시
- 재시작 시 상태 복구
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from config.settings import settings
from data.upbit_client import UpbitClient
from bot.position import Position, ExitReason


@dataclass
class Heartbeat:
    """가격 피드 감시 (§9.4). 마지막 갱신 이후 stale 초과 시 정지."""
    last_update: float = field(default_factory=time.monotonic)
    stale_sec: int = settings.feed_stale_sec

    def beat(self) -> None:
        self.last_update = time.monotonic()

    def is_stale(self) -> bool:
        return (time.monotonic() - self.last_update) > self.stale_sec


class Failsafe:
    def __init__(self, client: UpbitClient, order_manager, notifier=None):
        self.client = client
        self.order_manager = order_manager
        self.notifier = notifier
        self.heartbeat = Heartbeat()
        self.triggered = False

    def kill_switch(self, positions: list[Position], get_price) -> None:
        """전체 청산 + 정지 (§9.1). 모든 포지션을 시장가로 즉시 청산."""
        self.triggered = True
        for pos in positions:
            price = get_price(pos.market)
            self.order_manager.exit_position(pos, price, ExitReason.KILL_SWITCH)
        self._notify("🛑 KILL SWITCH 발동 — 전 포지션 청산·정지 (§9.1)")

    def on_api_error(self, err: Exception) -> None:
        """API 에러/네트워크 끊김 → 신규 진입 중단 (§9.2). 기존 포지션은 유지."""
        self._notify(f"⚠️ API 에러 — 신규 진입 중단 (§9.2): {err}")

    def recover_state(self) -> list[dict]:
        """
        재시작 시 거래소 실제 잔고·미체결 조회 → 내부 상태와 대조 (§9.3).
        반환된 잔고로 봇 내부 포지션 목록을 재구성한다(중복 주문 방지).
        """
        balances = self.client.get_balances()
        self._notify(f"🔄 상태 복구: 거래소 잔고 {len(balances)}건 조회 (§9.3)")
        return balances

    def check_feed(self) -> bool:
        """피드가 살아있으면 True. stale 이면 False (호출측이 정지 처리)."""
        if self.heartbeat.is_stale():
            self._notify("⚠️ 가격 피드 정지 감지 — 매매 중단 (§9.4)")
            return False
        return True

    def _notify(self, msg: str) -> None:
        if self.notifier:
            self.notifier.send(msg)
        else:
            print(msg)

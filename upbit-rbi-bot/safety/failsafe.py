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
from config.charter import MIN_ORDER_KRW
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

    def recover_state(self, known_markets: set[str] | None = None) -> list[dict]:
        """
        재시작 시 거래소 실제 잔고 조회 → '봇이 모르는 보유 코인(오펀)'을 찾아 반환 (§9.3).
        반환: [{market, volume, avg_price, entry_time}] — 호출측(Trader)이 포지션으로 복원.
          - known_markets: 이미 봇이 추적 중인 마켓(제외).
          - avg_buy_price × 수량이 최소주문금액 미만이면 '먼지'로 보고 제외.
          - entry_time: 최근 매수체결 시각(epoch초). 조회 불가면 None(호출측이 현재로 폴백).
        """
        known = known_markets or set()
        balances = self.client.get_balances()
        orphans: list[dict] = []
        for b in balances:
            cur = b.get("currency")
            if not cur or cur == "KRW":
                continue
            qty = float(b.get("balance", 0) or 0) + float(b.get("locked", 0) or 0)
            if qty <= 0:
                continue
            market = f"KRW-{cur}"
            if market in known:
                continue
            avg_price = float(b.get("avg_buy_price", 0) or 0)
            if avg_price * qty < MIN_ORDER_KRW:   # 먼지 제외
                continue
            entry_time = None
            if hasattr(self.client, "get_last_buy_time"):
                entry_time = self.client.get_last_buy_time(market)
            orphans.append({"market": market, "volume": qty,
                            "avg_price": avg_price, "entry_time": entry_time})
        self._notify(
            f"🔄 상태 복구: 잔고 {len(balances)}건 중 오펀 {len(orphans)}건 발견 (§9.3)")
        return orphans

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

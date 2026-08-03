"""
리스크 관리 = 서킷 브레이커(§5) + 포지션 사이징(§7).
봇 루프는 신규 진입 전 반드시 can_enter() 를 통과해야 한다.

자본(capital)은 고정값이 아니라 매 tick 실계좌 잔고(총 자산)로 갱신된다 (헌장 v1.1 §7.1).
→ 입금하면 포지션 크기·일일한도가 자동으로 커지고, 손실/출금이면 작아진다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from config import charter as C
from config.settings import settings


@dataclass
class RiskState:
    """세션 동안의 리스크 상태 추적."""
    capital: float = settings.paper_capital_krw       # 현재 총 자산(원화+코인) — 매 tick 갱신
    available_krw: float = settings.paper_capital_krw  # 주문 가능 원화
    equity_high: float = 0.0                           # 자본 고점 (MDD 계산 §5.4)
    daily_pnl: float = 0.0                             # 당일 누적 손익 (§5.2)
    consecutive_losses: int = 0                        # 연속 손절 (§5.3)
    open_positions: int = 0                            # 동시 보유 (§5.5)
    halted: bool = False                               # 전면 정지 여부 (§5.4)
    halt_reason: str = ""


class RiskManager:
    def __init__(self, state: RiskState | None = None):
        self.s = state or RiskState()

    # ── 자본 갱신 (매 tick, 헌장 §7.1) ───────────────────────
    def update_capital(self, available_krw: float, total_equity: float) -> None:
        """실계좌 잔고를 반영. 고점 갱신 + 라이브 MDD 체크."""
        self.s.available_krw = available_krw
        self.s.capital = total_equity
        if total_equity > self.s.equity_high:
            self.s.equity_high = total_equity
        self._check_drawdown(total_equity)

    # ── 진입 가능 여부 (헌장 §3.6, §5) ───────────────────────
    def can_enter(self) -> tuple[bool, str]:
        if self.s.halted:
            return False, f"halted: {self.s.halt_reason}"
        if self.s.daily_pnl <= -C.daily_loss_limit_krw(self.s.capital):
            return False, "daily loss limit hit (§5.2)"
        if self.s.consecutive_losses >= C.MAX_CONSECUTIVE_LOSSES:
            return False, "consecutive loss circuit (§5.3)"
        if self.s.open_positions >= C.MAX_CONCURRENT_POSITIONS:
            return False, "max concurrent positions (§5.5)"
        if self.s.available_krw < C.MIN_ORDER_KRW:
            return False, "insufficient KRW balance (잔고 부족)"
        return True, "ok"

    # ── 포지션 사이징 (헌장 §7) ──────────────────────────────
    def size_for(self, stop_ratio: float) -> float:
        """1거래 리스크 = 자본 1%를 만족하는 포지션 크기 (§7.2, ATR 정규화 v1.2)."""
        return C.position_size_krw(stop_ratio, self.s.capital, self.s.available_krw)

    # ── 체결 결과 반영 ───────────────────────────────────────
    def on_open(self) -> None:
        self.s.open_positions += 1

    def on_partial_close(self, pnl_krw: float) -> None:
        """
        반익절 반영 (§4.1-A, v2.7). 포지션은 아직 살아 있으므로 open_positions 는 줄이지
        않고, 연속손실(§5.3)도 건드리지 않는다 — 승패 판정은 최종 청산에서 한 번만 한다.
        당일 손익(§5.2)에는 실현된 만큼 즉시 반영한다.
        """
        self.s.daily_pnl += pnl_krw

    def on_close(self, pnl_krw: float) -> None:
        self.s.open_positions = max(0, self.s.open_positions - 1)
        self.s.daily_pnl += pnl_krw
        if pnl_krw < 0:
            self.s.consecutive_losses += 1
        else:
            self.s.consecutive_losses = 0
        # 실현 손익이 자본에 반영되는 것은 다음 update_capital(잔고 재조회)에서 처리됨

    def _check_drawdown(self, equity: float) -> None:
        """MDD 전면정지 (§5.4). 라이브 총 자산 기준."""
        if self.s.equity_high <= 0:
            return
        drawdown = (self.s.equity_high - equity) / self.s.equity_high
        if drawdown >= C.MAX_DRAWDOWN_RATIO:
            self.s.halted = True
            self.s.halt_reason = f"MDD {drawdown:.1%} >= {C.MAX_DRAWDOWN_RATIO:.0%} (§5.4)"

    def reset_daily(self) -> None:
        """일일 리셋(세션/날짜 경계). 연속손실·MDD 는 유지."""
        self.s.daily_pnl = 0.0

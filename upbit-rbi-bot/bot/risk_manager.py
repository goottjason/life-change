"""
리스크 관리 = 서킷 브레이커(§5) + 포지션 사이징(§7).
봇 루프는 신규 진입 전 반드시 can_enter() 를 통과해야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from config import charter as C


@dataclass
class RiskState:
    """세션 동안의 리스크 상태 추적."""
    starting_capital: float = C.TOTAL_CAPITAL_KRW
    equity_high: float = C.TOTAL_CAPITAL_KRW      # 자본 고점 (MDD 계산 §5.4)
    daily_pnl: float = 0.0                        # 당일 누적 손익 (§5.2)
    consecutive_losses: int = 0                   # 연속 손절 (§5.3)
    open_positions: int = 0                       # 동시 보유 (§5.5)
    halted: bool = False                          # 전면 정지 여부 (§5.4)
    halt_reason: str = ""


class RiskManager:
    def __init__(self, state: RiskState | None = None):
        self.s = state or RiskState()

    # ── 진입 가능 여부 (헌장 §3.6, §5) ───────────────────────
    def can_enter(self) -> tuple[bool, str]:
        if self.s.halted:
            return False, f"halted: {self.s.halt_reason}"
        if self.s.daily_pnl <= -C.daily_loss_limit_krw():
            return False, "daily loss limit hit (§5.2)"
        if self.s.consecutive_losses >= C.MAX_CONSECUTIVE_LOSSES:
            return False, "consecutive loss circuit (§5.3)"
        if self.s.open_positions >= C.MAX_CONCURRENT_POSITIONS:
            return False, "max concurrent positions (§5.5)"
        return True, "ok"

    # ── 포지션 사이징 (헌장 §7) ──────────────────────────────
    def size_for(self, stop_loss_pct: float) -> float:
        """1거래 리스크 = 자본 1% 를 만족하는 포지션 크기 (§7.2)."""
        return C.position_size_krw(stop_loss_pct)

    # ── 체결 결과 반영 ───────────────────────────────────────
    def on_open(self) -> None:
        self.s.open_positions += 1

    def on_close(self, pnl_krw: float) -> None:
        self.s.open_positions = max(0, self.s.open_positions - 1)
        self.s.daily_pnl += pnl_krw
        equity = self.s.equity_high + self.s.daily_pnl  # 근사(세션 기준)
        self.s.equity_high = max(self.s.equity_high, equity)

        if pnl_krw < 0:
            self.s.consecutive_losses += 1
        else:
            self.s.consecutive_losses = 0

        self._check_drawdown(equity)

    def _check_drawdown(self, equity: float) -> None:
        """MDD 전면정지 (§5.4)."""
        drawdown = (self.s.equity_high - equity) / self.s.equity_high
        if drawdown >= C.MAX_DRAWDOWN_RATIO:
            self.s.halted = True
            self.s.halt_reason = f"MDD {drawdown:.1%} >= {C.MAX_DRAWDOWN_RATIO:.0%} (§5.4)"

    def reset_daily(self) -> None:
        """일일 리셋(세션/날짜 경계). 연속손실·MDD 는 유지."""
        self.s.daily_pnl = 0.0

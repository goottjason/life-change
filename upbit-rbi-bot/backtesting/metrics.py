"""백테스트 성과 지표 (헌장 §11 통과 기준)."""
from __future__ import annotations

from dataclasses import dataclass

from config import charter as C


@dataclass
class BacktestResult:
    trades: int
    wins: int
    win_rate: float
    profit_factor: float
    max_drawdown: float
    total_return: float

    def passes(self) -> bool:
        """헌장 §11 통과 기준."""
        return (
            self.win_rate > C.BACKTEST_MIN_WINRATE
            and self.profit_factor > C.BACKTEST_MIN_PROFIT_FACTOR
            and self.max_drawdown < C.BACKTEST_MAX_DRAWDOWN
            and self.trades >= C.BACKTEST_MIN_TRADES
        )

    def summary(self) -> str:
        mark = "✅ PASS" if self.passes() else "❌ FAIL"
        return (
            f"{mark} | trades={self.trades} winrate={self.win_rate:.1%} "
            f"PF={self.profit_factor:.2f} MDD={self.max_drawdown:.1%} "
            f"ret={self.total_return:.1%}"
        )


def compute(pnls: list[float]) -> BacktestResult:
    """거래별 손익률 리스트로 지표 계산."""
    if not pnls:
        return BacktestResult(0, 0, 0, 0, 0, 0)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses)) or 1e-9
    # 누적 자본 곡선으로 MDD
    equity, peak, mdd = 1.0, 1.0, 0.0
    for p in pnls:
        equity *= (1 + p)
        peak = max(peak, equity)
        mdd = max(mdd, (peak - equity) / peak)
    return BacktestResult(
        trades=len(pnls),
        wins=len(wins),
        win_rate=len(wins) / len(pnls),
        profit_factor=gross_win / gross_loss,
        max_drawdown=mdd,
        total_return=equity - 1,
    )

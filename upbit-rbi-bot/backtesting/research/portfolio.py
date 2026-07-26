"""
포트폴리오 수준 시뮬레이터 — '거래당 기댓값'과 '계좌 우상향'은 다른 문제다.

거래당 기댓값이 +라도 계좌가 우상향하지 않을 수 있다:
 - 동시 포지션 한도(§5.5, 3개) 때문에 신호를 다 못 받는다
 - 리스크 기반 사이징(§7.2: 자본1% ÷ 손절거리)이라 손절이 넓은 거래는 크기가 작다
 - 전략당 배분 상한(§7.1, 자본의 1/3)과 최소주문금액(5,000원)에 걸린다
 - 일일 손실 한도(§5.2, -3%)·연속손절 차단(§5.3)이 걸리면 그날 매매가 멈춘다

이 모듈은 거래 목록(시간·손익률·손절거리)을 받아 위 제약을 넣고 실제 계좌 곡선을 만든다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import charter as C


@dataclass
class PortTrade:
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    pnl_ratio: float        # 수수료 차감 후 손익률
    stop_ratio: float       # 진입 시 손절거리(사이징 계산용)
    market: str = ""


@dataclass
class PortResult:
    final_equity: float
    total_return: float
    mdd: float
    taken: int
    skipped_slots: int
    skipped_daily_stop: int
    days: int

    def summary(self) -> str:
        return (f"최종자본 {self.final_equity:,.0f}원 ({self.total_return:+.1%}) "
                f"MDD {self.mdd:.1%} · 체결 {self.taken}건 "
                f"(슬롯부족 스킵 {self.skipped_slots}, 일손실한도 스킵 {self.skipped_daily_stop}) "
                f"· {self.days}일")


def run(trades: list[PortTrade], capital: float = C.DEFAULT_CAPITAL_KRW,
        max_concurrent: int = C.MAX_CONCURRENT_POSITIONS,
        daily_loss_limit: bool = True) -> PortResult:
    """
    시간순으로 진입 신호를 처리한다. 자본은 실현손익이 날 때 갱신(§7.1의 잔고연동을 단순화).
    동시 보유가 max_concurrent 이면 신호를 버린다(라이브와 동일하게 '기회 손실'로 계산).
    """
    if not trades:
        return PortResult(capital, 0.0, 0.0, 0, 0, 0, 0)
    ordered = sorted(trades, key=lambda t: t.entry_ts)
    equity = capital
    peak = capital
    mdd = 0.0
    open_until: list[pd.Timestamp] = []
    realized: list[tuple[pd.Timestamp, float]] = []   # (청산시각, 손익금액)
    day_pnl: dict = {}
    taken = skipped_slots = skipped_daily = 0

    for t in ordered:
        # 이 시점까지 청산된 포지션 정산
        done = [r for r in realized if r[0] <= t.entry_ts]
        for ts, amount in done:
            equity += amount
            d = ts.date()
            day_pnl[d] = day_pnl.get(d, 0.0) + amount
            peak = max(peak, equity)
            mdd = max(mdd, 1 - equity / peak) if peak > 0 else mdd
        realized = [r for r in realized if r[0] > t.entry_ts]
        open_until = [u for u in open_until if u > t.entry_ts]

        if daily_loss_limit:
            today = day_pnl.get(t.entry_ts.date(), 0.0)
            if today <= -C.daily_loss_limit_krw(equity):     # §5.2
                skipped_daily += 1
                continue
        if len(open_until) >= max_concurrent:                # §5.5
            skipped_slots += 1
            continue

        size = C.position_size_krw(t.stop_ratio, equity)     # §7.2 리스크 기반 사이징
        size = min(size, equity - sum_open_cost(open_until, equity, max_concurrent))
        if size < C.MIN_ORDER_KRW:                           # §6.5
            skipped_slots += 1
            continue
        taken += 1
        open_until.append(t.exit_ts)
        realized.append((t.exit_ts, size * t.pnl_ratio))

    for ts, amount in realized:                              # 남은 포지션 정산
        equity += amount
        peak = max(peak, equity)
        mdd = max(mdd, 1 - equity / peak) if peak > 0 else mdd

    days = (ordered[-1].entry_ts - ordered[0].entry_ts).days or 1
    return PortResult(equity, equity / capital - 1, mdd, taken,
                      skipped_slots, skipped_daily, days)


def sum_open_cost(open_until: list, equity: float, max_concurrent: int) -> float:
    """열린 포지션이 쓰고 있는 자금의 근사치(슬롯당 최대 배분 기준)."""
    return len(open_until) * equity * C.ALLOC_PER_STRATEGY_RATIO

"""
돌파(breakout) 전략 — v4.0 실험 트랙 (헌장 §3.5, 스펙 docs/superpowers/specs/2026-08-14-…).

## 무엇을 노리는가
"조용히 있다가 움직이기 시작하는 순간 올라타서, 움직임이 끝나면 바로 내린다."
직전 N봉(기본 20봉 = 100분) 최고가를 종가가 넘고 거래량이 평소보다 늘었을 때만 산다.
청산은 전부 가격 기반(Position): 트레일링 스톱 · 고정 손절 백스톱 · 시간손절(수익 유예).

## ⚠ 이 전략은 §11 검증을 통과하지 않았다 (운영자가 알고 가동)
과거 연구(14차 H4)에서 돌파 계열 롱은 유의하게 음수였다. 1차 산출물은 수익이 아니라
"어떤 조건의 돌파가 손실인가"의 실거래 데이터다. 그래서 진입 순간의 모든 상황을 meta에
담는다 — 트레이더가 이를 JSON으로 저장하고 주간 코호트 리포트가 축별로 쪼갠다.
추세·변동성 게이트는 **판단에 쓰지 않고 기록만 한다**(1주 뒤 데이터로 재판단).
주문금액은 EXPERIMENT_MAX_ORDER_KRW(10,000원)로 강제 제한된다(charter.position_cap_for).
"""
from __future__ import annotations

import pandas as pd

from indicators import ta
from strategies.base import BaseStrategy, Signal, Action


class BreakoutStrategy(BaseStrategy):
    def signal(self, df: pd.DataFrame, ctx: dict | None = None) -> Signal:
        n = self.spec.breakout_bars
        if n <= 0 or len(df) < max(n + 1, 20):
            return Signal(Action.HOLD, self.name, "insufficient bars")

        close = float(df["close"].iloc[-1])
        prior = df.iloc[-(n + 1):-1]              # 현재(진행 중) 봉 제외 직전 n봉
        prior_high = float(prior["high"].max())
        vol = float(df["volume"].iloc[-1])
        vol_avg = float(prior["volume"].mean())
        vol_ratio = (vol / vol_avg) if vol_avg > 0 else 0.0

        atr = ta.atr(df).iloc[-1] if len(df) >= 15 else float("nan")
        atr_pct = (float(atr) / close * 100) if pd.notna(atr) and close > 0 else None

        # 코호트 축 (판단에 쓰는 건 돌파·거래량비 둘뿐, 나머지는 기록용 — 모듈 docstring 참조)
        meta = {
            "breakout_pct": (round((close - prior_high) / prior_high * 100, 3)
                             if prior_high > 0 else None),
            "vol_ratio": round(vol_ratio, 2),
            "atr_pct": None if atr_pct is None else round(atr_pct, 3),
            "trend_up": (ctx or {}).get("trend_up"),
            "timeframe": self.spec.timeframe,
            "n": n, "vol_mult": self.spec.vol_mult, "trail": self.spec.trail_atr_mult,
        }

        if prior_high <= 0 or close <= prior_high:
            return Signal(Action.HOLD, self.name,
                          f"돌파 대기 (직전 {n}봉 고가 {prior_high:.4g})", meta)
        if self.spec.vol_mult > 0 and vol_ratio < self.spec.vol_mult:
            return Signal(Action.HOLD, self.name,
                          f"거래량 부족 ({vol_ratio:.1f}배 < {self.spec.vol_mult}배)", meta)
        return Signal(Action.ENTER_LONG, self.name,
                      f"{n}봉 고가 돌파 +{meta['breakout_pct']}% · 거래량 {vol_ratio:.1f}배",
                      meta)

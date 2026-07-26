"""
RSI(2) 눌림목 되돌림 전략 (헌장 §2, v1.3) — 백테스트로 검증된 유일한 전략.

## 무엇을 노리는가
상승 추세인 코인이 **아주 짧게 과매도로 밀렸을 때** 사서, 되돌림이 끝나면 판다.
"오르는 중인 종목의 일시적 급락은 대체로 되돌아온다"는 성질을 이용한다.

## 세 가지 조건이 동시에 맞아야만 진입한다 (하나라도 어긋나면 안 산다)
1. **RSI(2) ≤ 3** — 2봉 기준 RSI가 3 이하. 0~100 척도에서 극단적 과매도로,
   최근 10분이 거의 전부 하락이었다는 뜻이다. 흔하지 않다(하루 1회 미만).
2. **1시간봉 EMA200 위** — 큰 흐름이 상승일 때만. 하락 추세의 급락을 사면
   그냥 계속 떨어진다(백테스트에서 하락장 진입은 −0.25%/거래).
3. **ATR(14)/가격 ≥ 0.6%** — 변동성 게이트. 지금 이 코인이 하루에도 별로 안 움직이면
   되돌림 폭이 수수료(왕복 0.1%)+스프레드보다 작아서 이겨도 남는 게 없다.
   **이 조건이 없으면 전략 전체가 음의 기댓값이 된다**(연구 결과).

## 청산
- **RSI(2) ≥ 70** — 되돌림 완료. 주 청산 경로다(백테스트에서 대부분).
- 익절 +2.5% / 손절 −2.5% (고정. ATR 기반이 아니다 — 검증된 값)
- 시간손절 96봉(8시간) — 되돌아오지 않으면 자본을 회수한다.

## 검증 성적 (2년 5분봉, 저스프레드 6종목, 홀드아웃 17개월)
553거래 · 승률 69.1% · PF 1.49 · 거래당 +0.180%(수수료+실측 스프레드 차감) · t+3.48
계좌 +32.7% · MDD 5.9% · 하루 약 0.7회 거래
상세: backtesting/research/README.md
"""
from __future__ import annotations

import pandas as pd

from indicators import ta
from strategies.base import BaseStrategy, Signal, Action

RSI_PERIOD = 2
ENTRY_LEVEL = 3.0        # RSI(2) 이 값 이하에서 진입 (검증값)
EXIT_LEVEL = 70.0        # RSI(2) 이 값 이상에서 청산 (검증값)
TREND_EMA_BARS = 2400    # ctx 없이 기준봉으로 추세를 볼 때 필요한 봉 수 (5분×2400 = 200시간)


class Rsi2PullbackStrategy(BaseStrategy):
    """
    ctx["trend_up"] 으로 상위 추세를 받는다. ctx가 없으면 기준봉이 TREND_EMA_BARS 이상일 때만
    자체 계산한다(백테스트 경로). 둘 다 불가하면 진입하지 않는다 — 추세 필터 없는 진입은
    검증되지 않은 다른 전략이기 때문이다.
    """

    def signal(self, df: pd.DataFrame, ctx: dict | None = None) -> Signal:
        if len(df) < 20:
            return Signal(Action.HOLD, self.name, "insufficient bars")

        rsi2 = ta.rsi(df["close"], RSI_PERIOD).iloc[-1]
        if pd.isna(rsi2):
            return Signal(Action.HOLD, self.name, "rsi nan")

        # 관찰용 수치 (대시보드에서 '어느 조건에 막혔는지' 보여준다 — 판단에는 쓰지 않음)
        trend_up = self._trend_up(df, ctx)
        atr_ratio = self._atr_ratio(df)
        gate = self.spec.min_atr_ratio
        meta = {"rsi2": round(float(rsi2), 1), "trend_up": trend_up, "gate": gate,
                "atr_pct": None if atr_ratio is None else round(atr_ratio * 100, 3),
                "timeframe": self.spec.timeframe}

        # 청산 판정을 먼저 하지 않는다: 진입 조건과 청산 조건은 겹치지 않으므로 순서 무관.
        if rsi2 >= EXIT_LEVEL:
            return Signal(Action.EXIT, self.name,
                          f"rsi2 {rsi2:.1f} >= {EXIT_LEVEL:.0f} 되돌림 완료", meta)

        if rsi2 > ENTRY_LEVEL:
            return Signal(Action.HOLD, self.name,
                          f"rsi2 {rsi2:.1f} (진입 {ENTRY_LEVEL:.0f} 이하 대기)", meta)

        if trend_up is None:
            return Signal(Action.HOLD, self.name, "추세 판정 불가(1시간봉 미확보) — 진입 보류", meta)
        if not trend_up:
            return Signal(Action.HOLD, self.name, "1시간봉 EMA200 아래 — 하락 추세 진입 금지", meta)

        if atr_ratio is None:
            return Signal(Action.HOLD, self.name, "atr 계산 불가", meta)
        if atr_ratio < gate:
            return Signal(Action.HOLD, self.name,
                          f"변동성 부족 atr {atr_ratio:.3%} < {gate:.1%} (수수료 못 넘김)", meta)

        return Signal(Action.ENTER_LONG, self.name,
                      f"rsi2 {rsi2:.1f}<={ENTRY_LEVEL:.0f} · 추세상승 · atr {atr_ratio:.2%}", meta)

    # ── 내부 ────────────────────────────────────────────────
    @staticmethod
    def _trend_up(df: pd.DataFrame, ctx: dict | None) -> bool | None:
        if ctx is not None and ctx.get("trend_up") is not None:
            return bool(ctx["trend_up"])
        if len(df) >= TREND_EMA_BARS:       # 백테스트 등 장기 캔들이 있는 경로
            ema = ta.ema(df["close"], TREND_EMA_BARS).iloc[-1]
            return bool(df["close"].iloc[-1] > ema)
        return None

    @staticmethod
    def _atr_ratio(df: pd.DataFrame) -> float | None:
        if len(df) < 15:
            return None
        atr = ta.atr(df).iloc[-1]
        price = df["close"].iloc[-1]
        if pd.isna(atr) or price <= 0:
            return None
        return float(atr / price)


def trend_up_from_hourly(hourly: pd.DataFrame) -> bool | None:
    """
    1시간봉 캔들로 추세 판정 — 라이브에서 ctx["trend_up"] 을 만들 때 쓴다.

    **직전에 완성된 1시간봉**으로 판정한다(진행 중인 봉을 쓰면 판정이 tick마다 흔들린다).
    백테스트도 같은 방식으로 검증했다(backtesting/research/lab_live_variant.py).
    """
    if hourly is None or len(hourly) < 201:
        return None
    closed = hourly.iloc[:-1]                     # 마지막 봉은 진행 중 → 제외
    ema = ta.ema(closed["close"], 200).iloc[-1]
    if pd.isna(ema):
        return None
    return bool(closed["close"].iloc[-1] > ema)

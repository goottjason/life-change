"""
유튜버 '이지티칭' 매매법 (오더블록 + FVG 다중 근거).
원문: docs/strategies/easy-teaching-man/

v2.7 전면 재작성. 2026-07-30~08-01 실매매(16진입/15청산/1승, 거래당 −0.379%) 분석 결과를
반영했다. 바뀐 것과 그 근거:

① **확정봉으로만 판단한다.** 이전 구현은 미완성 봉의 실시간 가격으로 판정해 진입 시각이
   15분 경계와 무관했고(19:44, 20:17, 23:36 …), 백테스트로 재현할 수 없었다.
② **반등을 확인하고 들어간다.** 이전 구현은 존에 닿는 즉시 진입했다. bullish FVG 안으로
   되돌아오는 순간의 매수는 '떨어지는 칼 받기'다 — 실측 MFE +0.44% vs MAE −0.53% 로
   비용을 빼기도 전에 아래로 기울어 있었다. 이제 마지막 확정봉이 존을 건드리고 **양봉으로
   마감**해야 진입한다.
③ **상위 추세를 필수 조건으로 둔다.** 이전 구현은 ctx 를 받고도 쓰지 않아 1시간봉 추세가
   꺾인 종목(SUI/SOL/ENS)에 롱을 걸었다.
④ **손절·목표 가격을 직접 넘긴다(Signal.stop_price/target_price).** 원문의 손절은
   '오더블록이 생성된 캔들의 최저점', 익절은 '직전 고점'이다. 자리와 무관한 ATR 배수·고정
   손익비로는 원문의 손익비가 성립하지 않는다.
⑤ **무상태(stateless)로 만들었다.** 이전 구현은 mitigated_ob_idx 를 인스턴스 필드로 들고
   있었는데, 전략 인스턴스는 build_strategies() 가 **전 종목에 하나만** 만들어 공유한다.
   모든 종목이 같은 15분봉 타임스탬프를 쓰므로 BTC 에서 소진된 오더블록이 XRP 의 같은 시각
   오더블록을 막았다. 이제 '소진 여부'를 df 에서 매번 계산한다 → 종목 간 오염이 불가능하다.
⑥ **존을 리스트로 관리하고 무효화한다.** 이전 구현은 last_valid_index() 로 가장 최근 1개만
   추적해서, 가격이 이미 관통해버린 위쪽 존이 유효한 아래쪽 존을 영구히 가렸다.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategies.base import BaseStrategy, Action, Signal
from config.charter import StrategySpec
from indicators import ta

# FVG 최소 크기 = ATR 의 이 배수. 너무 작은 갭은 노이즈다.
MIN_FVG_ATR_MULT = 0.2
# 오더블록과 FVG 를 '같은 자리'로 볼 허용오차 = ATR 의 이 배수.
# ⚠ 백테스트로 정한 값이 아니다 — 구조상 두 근거가 인접하게 생기기 때문에 둔 값이다.
#   §11 백테스트에서 이 값의 민감도를 반드시 확인할 것.
CONFLUENCE_ATR_MULT = 0.5


@dataclass
class Zone:
    """가격 구간 근거 하나 (오더블록 또는 FVG)."""
    kind: str        # "ob" | "fvg"
    bottom: float
    top: float
    formed_at: int   # 존을 만든 캔들의 위치(iloc)
    low: float       # 존을 만든 캔들 무리의 최저점 — 원문의 손절 기준

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


class EasyTeachingStrategy(BaseStrategy):
    """
    상승형 오더블록(OB) + 페어밸류 갭(FVG) 이 겹치는 구간에서, **반등을 확인한 뒤** 롱 진입.
    손절은 근거가 깨지는 지점(존을 만든 캔들의 저점), 1차 목표는 직전 스윙 고점이다.
    """

    def __init__(self, spec: StrategySpec):
        super().__init__(spec)
        self.lookback = 50   # 과거 50봉 이내의 OB/FVG 탐색

    # ── 근거 탐색 ────────────────────────────────────────────
    def _find_order_blocks(self, c: pd.DataFrame) -> list[Zone]:
        """
        상승형 오더블록: 직전 음봉의 **몸통을 완전히 감싸는** 양봉.
        매수 존 = 감싸진 음봉의 몸통. 손절 기준 = 두 캔들의 최저점(구조적 무효화 지점).
        """
        zones: list[Zone] = []
        o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
        for i in range(1, len(c)):
            prev_bear = o.iat[i - 1] > cl.iat[i - 1]
            curr_bull = cl.iat[i] > o.iat[i]
            if not (prev_bear and curr_bull):
                continue
            if not (cl.iat[i] >= o.iat[i - 1] and o.iat[i] <= cl.iat[i - 1]):
                continue      # 몸통을 완전히 감싸지 못함
            zones.append(Zone(
                kind="ob",
                bottom=float(cl.iat[i - 1]),      # 음봉이므로 close 가 몸통 하단
                top=float(o.iat[i - 1]),
                formed_at=i,
                low=float(min(l.iat[i - 1], l.iat[i])),
            ))
        return zones

    def _find_fvgs(self, c: pd.DataFrame, atr: float) -> list[Zone]:
        """
        Bullish FVG: n-2 캔들의 고가 < n 캔들의 저가 → 그 사이가 빈 공간.
        ATR 대비 너무 작은 갭은 노이즈이므로 버린다.
        """
        zones: list[Zone] = []
        h, l = c["high"], c["low"]
        for i in range(2, len(c)):
            bottom, top = float(h.iat[i - 2]), float(l.iat[i])
            if top <= bottom:
                continue
            if atr > 0 and (top - bottom) < atr * MIN_FVG_ATR_MULT:
                continue
            zones.append(Zone(kind="fvg", bottom=bottom, top=top, formed_at=i,
                              low=bottom))
        return zones

    # ── 유효성 판정 ──────────────────────────────────────────
    # 아래 둘 다 df 에서 매번 계산한다(인스턴스 상태 없음 → 종목 간 오염이 불가능하다).
    @staticmethod
    def _invalidated(zone: Zone, c: pd.DataFrame, confirm_at: int) -> bool:
        """형성 이후 확인봉 전에 저점이 존의 손절선 아래로 내려갔으면 근거는 죽었다."""
        between = c.iloc[zone.formed_at + 1: confirm_at]
        return bool(not between.empty and (between["low"] < zone.low).any())

    @staticmethod
    def _touched(zone: Zone, c: pd.DataFrame, confirm_at: int) -> bool:
        """확인봉 전에 이미 존 안으로 들어온 적이 있으면 첫 터치가 아니다(소진)."""
        between = c.iloc[zone.formed_at + 1: confirm_at]
        return bool(not between.empty and (between["low"] <= zone.top).any())

    # ── 신호 ─────────────────────────────────────────────────
    def signal(self, df: pd.DataFrame, ctx: dict | None = None) -> Signal:
        meta = {"ob": False, "fvg": False, "ob_range": "-", "fvg_range": "-",
                "confluence": 0, "trend_up": None}

        # 마지막 봉은 미완성일 수 있다 → 확정봉으로만 판단한다 (백테스트 재현성)
        c = df.iloc[:-1]
        if len(c) < self.lookback:
            return Signal(Action.HOLD, self.name, reason="insufficient_data", meta=meta)

        # 상위 타임프레임 추세는 필수 조건이다. 판정 불가면 진입 보류(rsi2 와 같은 원칙).
        trend_up = (ctx or {}).get("trend_up")
        meta["trend_up"] = trend_up
        if trend_up is None:
            return Signal(Action.HOLD, self.name, reason="추세 판정 불가 — 진입 보류", meta=meta)
        if not trend_up:
            return Signal(Action.HOLD, self.name, reason="1시간봉 추세 하락 — 진입 금지", meta=meta)

        atr = float(ta.atr(c).iloc[-1]) if len(c) >= 14 else 0.0
        window = c.iloc[-self.lookback:]
        confirm_at = len(window) - 1              # 마지막 확정봉 = 반등 확인봉
        confirm = window.iloc[confirm_at]

        # 오더블록 = **매수 타점**(원문: 매수 대기는 음봉 몸통 구간). 첫 터치가 매매이므로
        # 아직 소진되지 않고 무효화되지도 않은 것만 남긴다.
        obs = [z for z in self._find_order_blocks(window)
               if z.formed_at < confirm_at
               and not self._invalidated(z, window, confirm_at)
               and not self._touched(z, window, confirm_at)]
        # FVG = **두 번째 근거**(맥락). 소진·무효화를 따지지 않는다 — 같은 상승 임펄스에서
        # 만들어진 FVG 는 오더블록보다 항상 위에 있고(FVG 하단 = 음봉 고가 ≥ 오더블록 몸통
        # 상단), 되돌아온 가격은 **FVG 를 지나쳐야만** 오더블록에 닿는다. FVG 에도 미터치·
        # 미관통을 요구하면 진입이 성립할 수 없다. FVG 의 역할은 '이 자리가 임펄스의 출발점'
        # 임을 표시하는 것이고, 진입·손절·소진 판정은 전부 오더블록이 담당한다.
        fvgs = [z for z in self._find_fvgs(window, atr) if z.formed_at < confirm_at]
        if obs:
            meta["ob_range"] = f"{obs[-1].bottom:.4g}~{obs[-1].top:.4g}"
        if fvgs:
            meta["fvg_range"] = f"{fvgs[-1].bottom:.4g}~{fvgs[-1].top:.4g}"

        # '근거가 겹치는 자리'(원문) = 오더블록 구간에 FVG 가 맞닿아 있는 자리.
        # 위 이유로 두 구간이 엄밀히 교차하는 일은 드물다 → ATR 비례 허용오차로 인접을 인정한다.
        # 매수 존은 어디까지나 오더블록이고, FVG 는 그 자리를 보강하는 두 번째 근거다.
        tol = atr * CONFLUENCE_ATR_MULT
        best = None
        for ob in obs:
            near = [f for f in fvgs
                    if f.bottom <= ob.top + tol and f.top >= ob.bottom - tol]
            if not near:
                continue
            if best is None or ob.top > best.top:    # 더 높은(=가까운) 오더블록을 고른다
                best = ob
        if best is None:
            return Signal(Action.HOLD, self.name, reason="waiting_confluence", meta=meta)

        bottom, top, zone_low, formed_at = best.bottom, best.top, best.low, best.formed_at
        meta["ob"] = meta["fvg"] = True
        meta["confluence"] = 2

        # 반등 확인: 확인봉이 겹침 구간을 건드리고 **양봉으로**, 구간 하단 위에서 마감해야 한다.
        # (존에 닿는 즉시 진입하면 떨어지는 칼을 받는다 — 실매매에서 확인된 실패 원인)
        touched = float(confirm["low"]) <= top
        bullish = float(confirm["close"]) > float(confirm["open"])
        held = float(confirm["close"]) >= bottom
        if not (touched and bullish and held):
            why = ("존 미터치" if not touched else
                   "확인봉 음봉 — 반등 미확인" if not bullish else "존 하단 이탈")
            return Signal(Action.HOLD, self.name, reason=f"근거 중첩, {why}", meta=meta)

        # 손절 = 근거가 깨지는 지점(존을 만든 캔들 무리의 저점, 확인봉 저점과 비교해 낮은 쪽)
        stop_price = min(zone_low, float(confirm["low"]))
        # 1차 목표 = 직전 스윙 고점 (되돌림이 되돌리고 있는 그 상승 구간의 고점)
        target_price = float(window["high"].iloc[formed_at:].max())

        meta["stop"] = round(stop_price, 6)
        meta["target"] = round(target_price, 6)
        return Signal(Action.ENTER_LONG, self.name,
                      reason="OB_and_FVG_confluence + 반등확인 + 추세상승",
                      meta=meta, stop_price=stop_price, target_price=target_price)

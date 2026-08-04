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

import numpy as np
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

# ── 페이크아웃/트랩 (원문 ⑤, "★가장 중요★") ─────────────────
# 원문: "하락 채널의 하단이 강하게 뚫리면 대중은 공포에 손절한다. 세력은 이를 받아먹고 다시
#        채널 안으로 말아 올린다. 가격이 뚫린 후 **다시 구조물 안으로 진입할 때** 공격적으로
#        매수. 뚫고 내려갔던 **최저점이 매우 명확한 손절 라인**이 되어 손익비가 극대화된다."
#   - 페이크아웃 = 단일 바닥(V자), 트랩 = 이중 바닥(W자)
# 구현에서 '구조물'은 확정된 스윙 저점(피벗)으로 잡는다 — 추세선·채널의 프록시다.
PIVOT_K = 3              # 좌우 K봉 중 최저면 스윙 저점(지지 레벨)로 인정
MAX_SWEEP_BARS = 5       # 레벨 이탈이 이보다 오래 지속되면 '함정'이 아니라 '추세 이탈'이다
MIN_SWEEP_ATR_MULT = 0.1  # 레벨을 이 정도는 넘겨 뚫어야 '손절 유도'로 본다(노이즈 배제)


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
        o = c["open"].to_numpy(); h = c["high"].to_numpy()
        l = c["low"].to_numpy(); cl = c["close"].to_numpy()
        if len(c) < 2:
            return []
        engulf = ((o[:-1] > cl[:-1])            # 직전 음봉
                  & (cl[1:] > o[1:])            # 현재 양봉
                  & (cl[1:] >= o[:-1])          # 몸통 상단을 덮고
                  & (o[1:] <= cl[:-1]))         # 몸통 하단도 덮는다
        # 신뢰도 필터 (강의): "각 캔들의 몸통 크기가 2배 이상 차이 날 경우 신뢰할 만한
        # 오더블록으로 식별된다." 감싸는 양봉이 감싸인 음봉보다 그만큼 커야 한다.
        mult = self.spec.min_ob_body_mult
        if mult > 0:
            prev_body = np.abs(o[:-1] - cl[:-1])
            curr_body = np.abs(cl[1:] - o[1:])
            engulf &= curr_body >= prev_body * mult
        return [Zone(kind="ob",
                     bottom=float(cl[i - 1]),   # 음봉이므로 close 가 몸통 하단
                     top=float(o[i - 1]),
                     formed_at=int(i),
                     low=float(min(l[i - 1], l[i])))
                for i in (np.nonzero(engulf)[0] + 1)]

    def _find_fvgs(self, c: pd.DataFrame, atr: float) -> list[Zone]:
        """
        Bullish FVG: n-2 캔들의 고가 < n 캔들의 저가 → 그 사이가 빈 공간.
        ATR 대비 너무 작은 갭은 노이즈이므로 버린다.
        """
        if len(c) < 3:
            return []
        h = c["high"].to_numpy(); l = c["low"].to_numpy()
        bottom, top = h[:-2], l[2:]
        ok = top > bottom
        if atr > 0:
            ok &= (top - bottom) >= atr * MIN_FVG_ATR_MULT
        return [Zone(kind="fvg", bottom=float(h[i - 2]), top=float(l[i]),
                     formed_at=int(i), low=float(h[i - 2]))
                for i in (np.nonzero(ok)[0] + 2)]

    # ── 페이크아웃 / 트랩 (원문 ⑤) ───────────────────────────
    @staticmethod
    def _find_swing_lows(c: pd.DataFrame, k: int = PIVOT_K) -> list[tuple[int, float]]:
        """
        확정된 스윙 저점(피벗) = 지지 구조물. 좌우 k봉보다 낮으면 지지 레벨로 본다.
        추세선·채널을 직접 작도하는 대신 쓰는 프록시다(같은 역할: '대중의 손절이 쌓인 선').
        """
        lows = c["low"].to_numpy()
        out: list[tuple[int, float]] = []
        for j in range(k, len(c) - k):
            if lows[j] <= lows[j - k:j].min() and lows[j] <= lows[j + 1:j + k + 1].min():
                out.append((j, float(lows[j])))
        return out

    def _find_fakeout(self, c: pd.DataFrame, confirm_at: int, atr: float) -> dict | None:
        """
        확인봉에서 '지지 스윕 후 되찾기'가 완성됐는가.

        성립 조건 (원문 그대로):
          ① 확정된 스윙 저점(지지 레벨)이 있다
          ② 그 아래로 저점이 뚫렸다 — 노이즈가 아니라 손절을 유도할 만큼(≥0.1×ATR)
          ③ 이탈이 MAX_SWEEP_BARS 이내로 짧다 (길면 함정이 아니라 진짜 하락 이탈이다)
          ④ **확인봉이 레벨 위에서 양봉으로 마감** = 구조물 안으로 되돌아왔다
        반환: 손절(스윕 최저점)·목표(레벨 형성 이후 고점)·형태(fakeout=V / trap=W).
        """
        c_close = float(c["close"].to_numpy()[confirm_at])
        c_open = float(c["open"].to_numpy()[confirm_at])
        if c_close <= c_open:
            return None                                   # 양봉이 아니면 되찾기가 아니다

        lows = c["low"].to_numpy()
        highs = c["high"].to_numpy()
        best = None
        for j, level in reversed(self._find_swing_lows(c, self.spec.pivot_k)):
            if j >= confirm_at - 1 or c_close <= level:
                continue                                  # ④ 레벨 위 마감 실패
            seg_low = lows[j + 1: confirm_at + 1]
            below = seg_low < level
            if not below.any():
                continue
            first = int(below.argmax())
            if (len(below) - 1) - first > MAX_SWEEP_BARS:  # ③ 이탈이 너무 길다
                continue
            sweep_low = float(seg_low[first:].min())
            if atr > 0 and (level - sweep_low) < atr * MIN_SWEEP_ATR_MULT:
                continue                                  # ② 뚫은 깊이가 노이즈 수준
            # 이탈 구간이 몇 덩어리인가 — 1덩어리면 V자(페이크아웃), 2덩어리 이상이면 W자(트랩)
            legs = int(((below[1:].astype(int) - below[:-1].astype(int)) == 1).sum()
                       + int(below[0]))
            target = float(highs[j:confirm_at + 1].max())
            cand = {"kind": "trap" if legs >= 2 else "fakeout", "level": level,
                    "stop": sweep_low, "target": target, "formed_at": j, "legs": legs}
            if best is None or cand["level"] > best["level"]:   # 더 가까운(높은) 지지 우선
                best = cand
        return best

    # ── 유효성 판정 ──────────────────────────────────────────
    # 아래 둘 다 df 에서 매번 계산한다(인스턴스 상태 없음 → 종목 간 오염이 불가능하다).
    @staticmethod
    def _invalidated(zone: Zone, lows: np.ndarray, confirm_at: int) -> bool:
        """형성 이후 확인봉 전에 저점이 존의 손절선 아래로 내려갔으면 근거는 죽었다."""
        between = lows[zone.formed_at + 1: confirm_at]
        return bool(between.size and (between < zone.low).any())

    @staticmethod
    def _touched(zone: Zone, lows: np.ndarray, confirm_at: int) -> bool:
        """확인봉 전에 이미 존 안으로 들어온 적이 있으면 첫 터치가 아니다(소진)."""
        between = lows[zone.formed_at + 1: confirm_at]
        return bool(between.size and (between <= zone.top).any())

    # ── 신호 ─────────────────────────────────────────────────
    def signal(self, df: pd.DataFrame, ctx: dict | None = None) -> Signal:
        meta = {"ob": False, "fvg": False, "ob_range": "-", "fvg_range": "-",
                "confluence": 0, "trend_up": None}

        # 마지막 봉은 미완성일 수 있다 → 확정봉으로만 판단한다 (백테스트 재현성)
        c = df.iloc[:-1]
        if len(c) < self.lookback:
            return Signal(Action.HOLD, self.name, reason="insufficient_data", meta=meta)

        # 상위 타임프레임 추세. require_trend 인 스펙에서만 필수 조건이다 — 원문의 페이크아웃
        # 예시는 '하락 채널 하단'이라 추세 상승을 요구하면 원문 취지와 어긋날 수 있어 축으로 뒀다.
        trend_up = (ctx or {}).get("trend_up")
        meta["trend_up"] = trend_up
        if self.spec.require_trend:
            if trend_up is None:
                return Signal(Action.HOLD, self.name, reason="추세 판정 불가 — 진입 보류", meta=meta)
            if not trend_up:
                return Signal(Action.HOLD, self.name, reason="1시간봉 추세 하락 — 진입 금지",
                              meta=meta)

        atr = float(ta.atr(c).iloc[-1]) if len(c) >= 14 else 0.0
        window = c.iloc[-self.lookback:]
        confirm_at = len(window) - 1              # 마지막 확정봉 = 반등 확인봉
        confirm = window.iloc[confirm_at]
        win_lows = window["low"].to_numpy()
        mode = self.spec.confluence_mode

        # 필요한 근거만 계산한다 — fakeout 모드에서 오더블록/FVG 스캔은 순수 낭비다.
        need_zones = mode in ("ob_fvg", "fakeout_zone", "any")
        obs: list[Zone] = []
        fvgs: list[Zone] = []
        if not need_zones:
            pass
        else:
            obs = [z for z in self._find_order_blocks(window)
                   if z.formed_at < confirm_at
                   and not self._invalidated(z, win_lows, confirm_at)
                   and not self._touched(z, win_lows, confirm_at)]
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

        # ── 페이크아웃/트랩 경로 (원문 ⑤, "★가장 중요★") ──
        # 지지 스윕 후 되찾기. 손절이 스윕 최저점 바로 아래라 손익비가 구조적으로 크다.
        if mode in ("fakeout", "fakeout_zone", "any"):
            fk = self._find_fakeout(window, confirm_at, atr)
            if fk is not None:
                zone_ok = True
                if mode == "fakeout_zone":
                    # 세 번째 근거: 스윕 저점이 오더블록/FVG 구간과 겹칠 것
                    tol_z = atr * CONFLUENCE_ATR_MULT
                    zone_ok = any(z.bottom - tol_z <= fk["stop"] <= z.top + tol_z
                                  for z in (obs + fvgs))
                if zone_ok:
                    meta.update({"setup": fk["kind"], "confluence": 3 if mode == "fakeout_zone"
                                 else 2, "level": round(fk["level"], 6),
                                 "stop": round(fk["stop"], 6),
                                 "target": round(fk["target"], 6)})
                    return Signal(
                        Action.ENTER_LONG, self.name,
                        reason=f"{fk['kind']}(지지 {fk['level']:.4g} 스윕 후 되찾기)"
                               + (" + OB/FVG 겹침" if mode == "fakeout_zone" else ""),
                        meta=meta, stop_price=fk["stop"], target_price=fk["target"])
            if mode in ("fakeout", "fakeout_zone"):
                return Signal(Action.HOLD, self.name, reason="waiting_fakeout", meta=meta)

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

        # 멀티 타임프레임 겹침 (강의): 상위 봉의 오더블록과 겹치는 자리만 인정한다.
        if self.spec.require_mtf_overlap:
            htf = (ctx or {}).get("htf_obs") or []
            if not any(hb <= top and ht >= bottom for hb, ht in htf):
                meta["mtf"] = False
                return Signal(Action.HOLD, self.name,
                              reason="근거 중첩, 상위봉 오더블록과 겹치지 않음", meta=meta)
            meta["mtf"] = True
            meta["confluence"] = 3

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

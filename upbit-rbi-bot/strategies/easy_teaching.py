from __future__ import annotations
import pandas as pd

from strategies.base import BaseStrategy, Action, Signal
from config.charter import StrategySpec

class EasyTeachingStrategy(BaseStrategy):
    """
    유튜버 '이지티칭' 매매법 (오더블록 + FVG 다중 근거)
    - 상승형 오더블록(OB): 이전 음봉 몸통을 완전히 감싸는 양봉. 매수 타점은 이전 음봉 몸통 구간.
    - 페어밸류 갭(FVG): n-2 캔들 고가와 n 캔들 저가 사이의 빈 공간. 매수 타점은 해당 갭 구간.
    - 진입: OB와 FVG 근거가 겹치는 구간(Confluence)에 도달 시 롱 진입.
    """
    def __init__(self, spec: StrategySpec):
        super().__init__(spec)
        self.lookback = 50  # 과거 50봉 이내의 OB/FVG 탐색

    def signal(self, df: pd.DataFrame, ctx: dict | None = None) -> Signal:
        if len(df) < self.lookback:
            return Signal(Action.HOLD, self.name, reason="insufficient_data")

        # 1. 상승형 오더블록 탐색
        # 이전 음봉(Open > Close) 몸통을 완전히 감싸는 양봉(Close > Open, Close >= prev_Open, Open <= prev_Close)
        is_prev_bear = df['open'].shift(1) > df['close'].shift(1)
        is_curr_bull = df['close'] > df['open']
        engulfing_bull = is_prev_bear & is_curr_bull & \
                         (df['close'] >= df['open'].shift(1)) & \
                         (df['open'] <= df['close'].shift(1))
        
        # 가장 최근에 발생한 OB 탐색 (현재 봉 제외)
        # engulfing_bull 이 True인 인덱스 찾기
        recent_ob_idx = engulfing_bull.iloc[-self.lookback:-1].where(lambda x: x).last_valid_index()
        active_ob = None
        if recent_ob_idx is not None:
            # 음봉이었던 이전 캔들의 몸통이 OB 구간
            prev_idx = df.index.get_loc(recent_ob_idx) - 1
            if prev_idx >= 0:
                ob_top = df.iloc[prev_idx]['open']
                ob_bottom = df.iloc[prev_idx]['close']
                active_ob = (ob_bottom, ob_top) # 저가, 고가 (음봉이므로 close가 저가)

        # 2. Bullish FVG 탐색
        # n-2 고가 < n 저가
        gap_up = df['high'].shift(2) < df['low']
        recent_fvg_idx = gap_up.iloc[-self.lookback:-1].where(lambda x: x).last_valid_index()
        active_fvg = None
        if recent_fvg_idx is not None:
            fvg_top = df.loc[recent_fvg_idx, 'low']
            prev2_idx = df.index.get_loc(recent_fvg_idx) - 2
            fvg_bottom = df.iloc[prev2_idx]['high']
            active_fvg = (fvg_bottom, fvg_top)

        curr_close = df['close'].iloc[-1]
        curr_low = df['low'].iloc[-1]
        
        meta = {
            "ob": False,
            "fvg": False,
            "ob_range": "-",
            "fvg_range": "-",
            "confluence": 0
        }

        ob_score = 0
        if active_ob:
            meta["ob_range"] = f"{active_ob[0]:.1f}~{active_ob[1]:.1f}"
            if active_ob[0] <= curr_close <= active_ob[1] or active_ob[0] <= curr_low <= active_ob[1]:
                ob_score = 1
                meta["ob"] = True
                
        fvg_score = 0
        if active_fvg:
            meta["fvg_range"] = f"{active_fvg[0]:.1f}~{active_fvg[1]:.1f}"
            if active_fvg[0] <= curr_close <= active_fvg[1] or active_fvg[0] <= curr_low <= active_fvg[1]:
                fvg_score = 1
                meta["fvg"] = True

        confluence_score = ob_score + fvg_score
        meta["confluence"] = confluence_score

        # OB와 FVG 근거가 2개 겹칠 때 진입
        if confluence_score >= 2:
            return Signal(Action.ENTER_LONG, self.name, reason="OB_and_FVG_confluence", meta=meta)
        
        return Signal(Action.HOLD, self.name, reason="waiting_confluence", meta=meta)

"""
진입 진단(signal_view)은 '지금 이 tick의 판정'만 담아야 한다.

발견된 버그: signal_view 는 한 번 쓰이면 지워지지 않는 누적 dict 였다.
- 유니버스에서 빠진 종목(예: KRW-AAVE)의 행이 영원히 남아, 대시보드에서 15분이 지나도
  RSI2·ATR% 가 그대로인 '멈춘 행'으로 보였다. 실제 상태는 '더 이상 보지 않는 종목'이다.
- 같은 이유로, 이번 tick 에 판정을 건너뛴 전략(보유 중·리스크 한도)도 직전 tick 의
  숫자를 계속 보여줬다.

운영자가 이 표를 보고 '왜 안 사는가'를 판단하므로, 낡은 숫자는 잘못된 판단을 만든다.
"""
from __future__ import annotations

import pandas as pd

from tests.test_timeframe_rules import FRAMES, trader


class _Failsafe:
    class _HB:
        def beat(self): pass
    heartbeat = _HB()
    def check_feed(self): return True
    def on_api_error(self, e): pass


def tick_trader(eligible: list[str]):
    """tick() 을 돌릴 수 있는 최소 트레이더 (외부 I/O는 전부 스텁)."""
    t = trader()
    t.failsafe = _Failsafe()

    class Client:
        def get_account_equity(self, price_lookup): return 90_000.0, 90_000.0
        def get_candles(self, market, interval="minute5", count=200):
            return FRAMES[interval]
    t.client = Client()

    class Scr:
        spreads = {}
        def eligible(self): return list(eligible)
        def tradable_now(self, market, cap=None): return True, "스프레드 0.050%"
    t.screener = Scr()
    return t


def test_유니버스에서_빠진_종목의_행은_사라진다():
    """AAVE 가 유니버스에서 빠지면 진단표에서도 빠져야 한다 (멈춘 행 방지)."""
    t = tick_trader(["KRW-XRP"])
    t.signal_view["KRW-AAVE"] = {
        "rsi2_15m": {"action": "hold", "reason": "과매도 대기 (RSI2 7.0, 진입선 7 이하)",
                     "rsi2": 7.0, "atr_pct": 0.384, "gate": 0.004}}
    t.tick()
    assert "KRW-AAVE" not in t.signal_view, "유니버스에서 빠진 종목의 낡은 진단이 남아 있다"
    assert "KRW-XRP" in t.signal_view


def test_보유중인_전략은_낡은_숫자대신_보유중을_표시한다():
    """진입 판정을 건너뛴 전략이 직전 tick 의 RSI2·ATR% 를 계속 보여주면 안 된다."""
    t = trader()
    t._process_market("KRW-XRP", FRAMES)          # tick 1 — rsi2 진입
    assert "rsi2:KRW-XRP" in t.positions
    stale = dict(t.signal_view["KRW-XRP"]["rsi2"])

    t._process_market("KRW-XRP", FRAMES)          # tick 2 — rsi2 는 보유 중이라 건너뜀
    row = t.signal_view["KRW-XRP"]["rsi2"]
    assert row != stale, "보유 중인데 직전 tick 의 진단이 그대로 남아 있다"
    assert "보유" in row["reason"]
    assert "atr_pct" not in row, "판정하지 않은 tick 인데 낡은 ATR%를 들고 있다"


def test_리스크한도로_막히면_한도를_표시한다():
    """동시 포지션 한도로 전 종목이 막힐 때 표가 통째로 비면 원인을 알 수 없다."""
    t = trader()
    t.risk.can_enter = lambda name=None: (False, "동시 포지션 한도 도달")
    t._process_market("KRW-XRP", FRAMES)
    row = t.signal_view["KRW-XRP"]["rsi2"]
    assert "한도" in row["reason"]
    assert row["action"] == "hold"


def test_판정한_전략은_숫자가_매_tick_갱신된다():
    """정상 경로 회귀 방지 — 판정한 전략 행에는 이번 tick 의 RSI2·ATR%가 들어간다."""
    t = trader()
    t.risk.can_enter = lambda name=None: (False, "한도")     # 진입은 막고 판정만 시킨다
    t._process_market("KRW-XRP", FRAMES)
    t.risk.can_enter = lambda name=None: (True, "ok")
    t._process_market("KRW-XRP", FRAMES)
    row = t.signal_view["KRW-XRP"]["rsi2_15m"]
    assert row["atr_pct"] is not None and row["rsi2"] is not None


def test_영구제외_종목은_행을_만들지_않는다():
    """
    §3.2-h 블랙리스트(AVAX 5분봉 금지)는 tick 마다 바뀌는 상태가 아니라 고정 설정이다.
    '지금 왜 안 사는가' 표에 매 tick 띄우면 영구 잡음 행이 되고, 판정한 값이 없어서
    추세 칸이 '확인불가'로 보인다 — 1시간봉 조회 실패(진짜 판정 불가)와 구별되지 않는다.
    """
    t = trader()
    t._process_market("KRW-AVAX", FRAMES)
    assert "rsi2" not in t.signal_view["KRW-AVAX"], "영구 제외 조합이 진단표에 올라왔다"
    assert "rsi2_15m" in t.signal_view["KRW-AVAX"]


def test_판정한_행은_추세를_반드시_담는다():
    """
    화면의 추세 칸은 trend_up 이 없으면 '확인불가'로 표시된다. 판정한 행에 이 키가
    빠지면 시장 추세를 못 읽은 것처럼 보이므로, 판정 행에는 항상 들어 있어야 한다.
    """
    t = trader()
    t._process_market("KRW-XRP", FRAMES)
    for name, row in t.signal_view["KRW-XRP"].items():
        if "rsi2" in row:                      # 판정까지 간 행
            assert "trend_up" in row, name

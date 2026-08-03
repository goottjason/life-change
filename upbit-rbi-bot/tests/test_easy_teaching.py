"""
easy_teaching 진입 규칙 (v2.7 재작성분).

실매매 실패에서 확인된 진입 측 결함을 못 박는다:
  - 미완성 봉의 실시간 가격으로 판정 → 진입 시각이 봉 경계와 무관, 백테스트 재현 불가
  - 존에 닿는 즉시 진입 → 떨어지는 칼 받기 (MFE +0.44% vs MAE −0.53%)
  - ctx 를 받고도 안 써서 1시간봉 추세가 꺾인 종목에 롱
  - 인스턴스 상태(mitigated_*)가 전 종목 공유 → 종목 간 오염
  - 존을 '가장 최근 1개'만 추적 → 관통된 존이 유효한 존을 가림
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from config.charter import STRATEGY_SPECS
from strategies.base import Action
from strategies.easy_teaching import EasyTeachingStrategy


def make_df(bars: list[tuple[float, float, float, float]], start="2026-07-01") -> pd.DataFrame:
    """bars = [(open, high, low, close), ...] → 15분봉 DataFrame."""
    idx = pd.date_range(start, periods=len(bars), freq="15min")
    return pd.DataFrame(
        {"open": [b[0] for b in bars], "high": [b[1] for b in bars],
         "low": [b[2] for b in bars], "close": [b[3] for b in bars],
         "volume": [100.0] * len(bars)},
        index=idx)


def filler(n: int, price: float = 1000.0) -> list[tuple]:
    """근거를 만들지 않는 잔잔한 배경 봉. 양봉/음봉이 번갈아 나오되 감싸지 않는다."""
    out = []
    for i in range(n):
        if i % 2 == 0:                      # 음봉
            out.append((price + 2, price + 3, price - 3, price - 2))
        else:                               # 양봉 (직전 음봉 몸통을 감싸지 못함)
            out.append((price - 1, price + 3, price - 3, price + 1))
    return out


def setup_bars() -> list[tuple]:
    """
    원문 그대로의 자리를 만든다: 오더블록에서 임펄스가 출발하며 FVG 를 남기고, 상승 후
    되돌림이 오더블록으로 내려온다.
      - 오더블록: 음봉(1000→990) 을 감싸는 양봉(989→1001) → 매수 존 990~1000, 저점 988
      - FVG: 임펄스 중 high[i-2]=1002 < low[i]=1008 → 1002~1008 (오더블록 바로 위 = 인접)
      - 되돌림은 확인봉 전까지 오더블록을 건드리지 않는다(첫 터치가 매매이므로)
    """
    return filler(50) + [
        (1000.0, 1002.0, 989.0, 990.0),     # 음봉 — 몸통 990~1000
        (989.0, 1002.0, 988.0, 1001.0),     # 감싸는 양봉 → OB 990~1000, low 988
        (1001.0, 1012.0, 1001.0, 1011.0),   # 임펄스
        (1012.0, 1022.0, 1008.0, 1021.0),   # FVG 1002~1008
        (1021.0, 1025.0, 1015.0, 1017.0),   # 스윙 고점 1025
        (1017.0, 1018.0, 1004.0, 1006.0),   # 되돌림 — 아직 OB 미터치(low 1004 > 1000)
    ]


CONFIRM_BULL = (997.0, 1005.0, 993.0, 1003.0)     # 존 터치 + 양봉 마감
CONFIRM_BEAR = (1002.0, 1003.0, 993.0, 995.0)     # 존 터치했으나 음봉 마감
CONFIRM_MISS = (1006.0, 1008.0, 1002.0, 1007.0)   # 존까지 안 내려옴


def strat() -> EasyTeachingStrategy:
    return EasyTeachingStrategy(STRATEGY_SPECS["easy_teaching"])


INCOMPLETE = (1003.0, 1004.0, 1002.0, 1003.5)   # 미완성 봉 (라이브가 항상 마지막에 붙인다)


def run(bars, ctx={"trend_up": True}):
    return strat().signal(make_df(bars + [INCOMPLETE]), ctx)


# ── 기본 진입 ────────────────────────────────────────────────
def test_근거중첩_반등확인_추세상승이면_진입한다():
    sig = run(setup_bars() + [CONFIRM_BULL])
    assert sig.action == Action.ENTER_LONG
    assert sig.meta["confluence"] == 2


def test_진입시_손절과_목표_가격을_함께_넘긴다():
    sig = run(setup_bars() + [CONFIRM_BULL])
    assert sig.stop_price is not None and sig.target_price is not None
    assert sig.stop_price < 1003.0 < sig.target_price


def test_손절은_존을_만든_캔들의_저점이다():
    """원문: '오더블록이 생성된 캔들의 최저점이 뚫리면 손절'."""
    sig = run(setup_bars() + [CONFIRM_BULL])
    # 오더블록 형성 두 캔들의 저점은 988, 확인봉 저점은 993 → 낮은 쪽
    assert sig.stop_price == pytest.approx(988.0)


def test_목표는_직전_스윙고점이다():
    """되돌림이 되돌리고 있는 그 상승 구간의 고점 = 1025."""
    sig = run(setup_bars() + [CONFIRM_BULL])
    assert sig.target_price == pytest.approx(1025.0)


# ── 반등 확인 ────────────────────────────────────────────────
def test_확인봉이_음봉이면_진입하지_않는다():
    """존 안으로 되돌아오는 순간의 매수 = 떨어지는 칼 받기. 실매매 실패의 직접 원인."""
    sig = run(setup_bars() + [CONFIRM_BEAR])
    assert sig.action == Action.HOLD
    assert "반등 미확인" in sig.reason


def test_존까지_내려오지_않았으면_진입하지_않는다():
    sig = run(setup_bars() + [CONFIRM_MISS])
    assert sig.action == Action.HOLD
    assert "미터치" in sig.reason


# ── 상위 추세 ────────────────────────────────────────────────
def test_1시간봉_추세가_하락이면_진입하지_않는다():
    sig = run(setup_bars() + [CONFIRM_BULL], ctx={"trend_up": False})
    assert sig.action == Action.HOLD
    assert "추세 하락" in sig.reason


def test_추세_판정불가면_진입_보류():
    for ctx in ({}, None, {"trend_up": None}):
        sig = run(setup_bars() + [CONFIRM_BULL], ctx=ctx)
        assert sig.action == Action.HOLD
        assert "판정 불가" in sig.reason


# ── 확정봉만 사용 ────────────────────────────────────────────
def test_미완성_봉은_판단에_쓰지_않는다():
    """
    마지막 봉(미완성)을 어떻게 바꾸든 신호가 달라지면 안 된다.
    이전 구현은 미완성 봉의 실시간 close 로 판정해 봉 경계와 무관하게 진입했다.
    """
    bars = setup_bars() + [CONFIRM_BULL]
    a = strat().signal(make_df(bars + [INCOMPLETE]), {"trend_up": True})
    b = strat().signal(make_df(bars + [(900.0, 901.0, 899.0, 899.5)]), {"trend_up": True})
    assert a.action == b.action == Action.ENTER_LONG
    assert a.stop_price == b.stop_price and a.target_price == b.target_price


# ── 무효화 / 소진 ────────────────────────────────────────────
def test_존이_이미_한번_터치됐으면_다시_진입하지_않는다():
    """첫 터치가 매매다. 같은 자리에서 반복 진입하면 근거가 아니라 습관이다."""
    bars = setup_bars()
    bars.append(CONFIRM_BULL)                       # 1차 터치 — 여기가 매매였다
    bars.append((1000.0, 1006.0, 995.0, 1004.0))    # 2차 터치 (확인봉)
    sig = run(bars)
    assert sig.action == Action.HOLD


def test_손절선_아래로_뚫린_존은_죽는다():
    bars = setup_bars()
    bars.append((1006.0, 1007.0, 985.0, 987.0))     # 988 아래로 관통 → 오더블록 무효
    bars.append((987.0, 1005.0, 986.0, 1003.0))     # 되돌아와도 진입 금지
    sig = run(bars)
    assert sig.action == Action.HOLD


# ── 종목 간 오염 (이전 구현의 버그) ──────────────────────────
def test_전략_인스턴스는_무상태라_종목간_오염이_없다():
    """
    build_strategies() 는 전략 인스턴스를 **전 종목에 하나만** 만들어 공유한다.
    이전 구현은 mitigated_ob_idx 를 인스턴스에 들고 있어서, BTC 에서 소진된 오더블록이
    같은 타임스탬프를 쓰는 XRP 의 오더블록까지 막았다.
    """
    s = strat()
    df = make_df(setup_bars() + [CONFIRM_BULL, INCOMPLETE])

    first = s.signal(df, {"trend_up": True})        # 'BTC'
    second = s.signal(df, {"trend_up": True})       # 'XRP' — 같은 인스턴스
    assert first.action == second.action == Action.ENTER_LONG
    assert not [a for a in vars(s) if a.startswith("mitigated")]


# ── 존 리스트 (이전 구현은 최근 1개만 추적) ──────────────────
def test_관통된_최근존이_유효한_아래쪽_존을_가리지_않는다():
    """
    이전 구현은 last_valid_index() 로 가장 최근 OB 1개만 봤다. 가격이 이미 뚫고 지나간
    위쪽 OB 가 잡히면, 그 아래 살아 있는 OB 는 영원히 보이지 않았다.
    """
    bars = setup_bars()
    # 되돌림 도중에, 더 최근이지만 곧바로 소진되는 오더블록을 하나 더 만든다
    bars += [(1017.0, 1018.0, 1012.0, 1013.0),      # 음봉 — 몸통 1013~1017
             (1012.0, 1019.0, 1011.0, 1018.0)]      # 감싸는 양봉 → 존 1013~1017
    bars += [(1018.0, 1019.0, 1004.0, 1006.0)]      # 그 존을 지나쳐 내려간다(소진)
    bars.append(CONFIRM_BULL)
    sig = run(bars)
    # 위쪽 존이 소진됐어도 아래쪽 오더블록은 살아 있어야 한다
    assert sig.action == Action.ENTER_LONG


# ── 데이터 부족 ──────────────────────────────────────────────
def test_봉이_부족하면_보류():
    sig = strat().signal(make_df(filler(20)), {"trend_up": True})
    assert sig.action == Action.HOLD
    assert sig.reason == "insufficient_data"


# ── 전 전략 공통 가드 ────────────────────────────────────────
def test_어떤_전략도_종목간_공유상태를_두지_않는다():
    """
    build_strategies() 는 전략당 인스턴스를 하나만 만들어 **모든 종목이 공유**한다.
    전략이 tick 사이에 남는 가변 상태를 인스턴스에 두면 종목 A 의 상태가 종목 B 의
    판단을 바꾼다(easy_teaching 의 mitigated_ob_idx 가 정확히 그랬다).
    설정값(spec/lookback)이 아닌 필드가 새로 생기면 이 테스트가 막는다.
    """
    from bot.trader import build_strategies
    allowed = {"spec", "lookback"}
    for name, s in build_strategies(tuple(STRATEGY_SPECS)).items():
        extra = set(vars(s)) - allowed
        assert not extra, f"{name} 이 종목 간 공유되는 상태를 들고 있다: {extra}"


# ── 페이크아웃 / 트랩 (원문 ⑤ "★가장 중요★") ────────────────
from dataclasses import replace                                  # noqa: E402
from strategies.easy_teaching import PIVOT_K, MAX_SWEEP_BARS     # noqa: E402


def fk_strat(mode="fakeout", require_trend=True) -> EasyTeachingStrategy:
    spec = replace(STRATEGY_SPECS["easy_teaching"],
                   confluence_mode=mode, require_trend=require_trend)
    return EasyTeachingStrategy(spec)


def support_bars(sweep_depth=6.0, sweep_len=1, reclaim=True, bullish=True) -> list[tuple]:
    """
    지지 스윙 저점(990)을 만들고, 그 아래로 스윕한 뒤 되찾는 형태를 만든다.
      - 피벗 저점: 좌우 PIVOT_K 봉보다 낮은 저점 990
      - 스윕: 990 아래로 sweep_depth 만큼, sweep_len 봉 동안
      - 되찾기: 확인봉이 990 위에서 양봉 마감
    """
    bars = filler(50)
    bars += [(1005.0, 1010.0, 1000.0, 1004.0)] * PIVOT_K      # 피벗 좌측
    bars += [(1004.0, 1006.0, 990.0, 1000.0)]                 # ← 스윙 저점 990
    bars += [(1000.0, 1012.0, 998.0, 1010.0)] * PIVOT_K       # 피벗 우측(확정)
    bars += [(1010.0, 1015.0, 1005.0, 1008.0)]                # 반등 — 목표가 될 고점
    lo = 990.0 - sweep_depth
    bars += [(1000.0, 1002.0, lo, 992.0)] * sweep_len         # 스윕(레벨 이탈)
    if reclaim:
        close = 998.0 if bullish else 985.0
        open_ = 993.0 if bullish else 999.0
        bars += [(open_, 1000.0, 991.0, close)]               # 확인봉
    return bars


def fk_run(bars, mode="fakeout", ctx={"trend_up": True}, require_trend=True):
    return fk_strat(mode, require_trend).signal(make_df(bars + [INCOMPLETE]), ctx)


def test_지지_스윕후_되찾기면_진입한다():
    sig = fk_run(support_bars())
    assert sig.action == Action.ENTER_LONG
    assert "스윕 후 되찾기" in sig.reason


def test_손절은_뚫고내려간_최저점이다():
    """원문: '뚫고 내려갔던 최저점이 매우 명확한 손절 라인'."""
    sig = fk_run(support_bars(sweep_depth=6.0))
    assert sig.stop_price == pytest.approx(984.0)      # 990 − 6


def test_목표는_지지형성_이후의_고점이다():
    sig = fk_run(support_bars())
    assert sig.target_price == pytest.approx(1015.0)


def test_단일바닥은_페이크아웃_이중바닥은_트랩():
    v = fk_run(support_bars(sweep_len=1))
    assert v.meta["setup"] == "fakeout"
    bars = support_bars(sweep_len=1)
    bars.insert(-1, (992.0, 999.0, 991.0, 998.0))      # 잠깐 올라왔다가
    bars.insert(-1, (998.0, 999.0, 986.0, 993.0))      # 다시 뚫는다 → W자
    w = fk_run(bars)
    assert w.meta["setup"] == "trap"


def test_되찾지_못하면_진입하지_않는다():
    sig = fk_run(support_bars(reclaim=False))
    assert sig.action == Action.HOLD


def test_확인봉이_레벨_아래면_진입하지_않는다():
    """구조물 안으로 되돌아오지 못했으면 함정이 아니라 진짜 이탈이다."""
    sig = fk_run(support_bars(bullish=False))
    assert sig.action == Action.HOLD


def test_이탈이_오래_지속되면_함정이_아니다():
    sig = fk_run(support_bars(sweep_len=MAX_SWEEP_BARS + 2))
    assert sig.action == Action.HOLD


def test_노이즈_수준으로_살짝_뚫린_것은_스윕이_아니다():
    sig = fk_run(support_bars(sweep_depth=0.05))
    assert sig.action == Action.HOLD


def test_손익비가_구조적으로_크다():
    """손절이 스윕 저점 바로 아래라 목표까지의 거리가 손절거리보다 훨씬 크다."""
    sig = fk_run(support_bars())
    entry = 998.0
    rr = (sig.target_price - entry) / (entry - sig.stop_price)
    assert rr > 1.2, f"RR {rr:.2f}"


def test_추세필터를_끄면_하락추세에서도_진입한다():
    """원문의 페이크아웃 예시는 '하락 채널 하단'이다 — 축으로 검증할 수 있어야 한다."""
    on = fk_run(support_bars(), ctx={"trend_up": False}, require_trend=True)
    off = fk_run(support_bars(), ctx={"trend_up": False}, require_trend=False)
    assert on.action == Action.HOLD
    assert off.action == Action.ENTER_LONG


def test_ob_fvg_모드는_페이크아웃을_보지_않는다():
    """모드별로 근거 조합이 분리돼야 백테스트에서 기여도를 가를 수 있다."""
    sig = fk_run(support_bars(), mode="ob_fvg")
    assert sig.action == Action.HOLD


def test_fakeout_zone_모드는_존_겹침까지_요구한다():
    sig = fk_run(support_bars(), mode="fakeout_zone")
    assert sig.action == Action.HOLD          # 이 픽스처엔 겹치는 OB/FVG 가 없다

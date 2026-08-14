"""
구조 기반 청산 (반익반본) — easy_teaching 원문 매매법의 핵심.

실매매 실패 분석(2026-08-03)에서 확인된 것:
  청산 15건이 전부 dead(시간손절·횡보)였고 익절 도달은 0건이었다.
  익절선이 '최소 +1.5%'(rr 1.5 × 손절하한 1%)인데 실측 MFE 는 평균 +0.44%,
  최대 +1.42% 였다 → 이길 수 있는 경로가 산술적으로 존재하지 않았다.

원문(docs/strategies/easy-teaching-man)의 규칙:
  - 손절 = 오더블록 생성 캔들의 저점 (= 진입 근거가 깨지는 지점)
  - 익절 = 직전 고점에서 **절반 청산** → 나머지는 **진입가 본절 스탑**
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from config import charter as C
from bot.position import Position, ExitReason


def _pos(entry=1000.0, stop=990.0, target=1030.0, strategy="easy_teaching",
         volume=30.0, size=30_000.0) -> Position:
    return Position(
        strategy=strategy, market="KRW-TEST", entry_price=entry,
        size_krw=size, volume=volume,
        entry_time=pd.Timestamp("2026-08-03 00:00"), entry_atr=5.0,
        stop_price=stop, target_price=target,
    )


# ── 손절: 구조적 지점 ────────────────────────────────────────
def test_손절은_ATR비율이_아니라_OB캔들_저점에서_난다():
    pos = _pos(entry=1000.0, stop=990.0)      # 구조적 손절거리 1.0%
    assert pos.check_price_exit(991.0) == ExitReason.NONE
    assert pos.check_price_exit(990.0) == ExitReason.STOP_LOSS


def test_구조적_손절이_ATR손절보다_가까우면_구조가_이긴다():
    """
    ATR 스톱(1.5×ATR, 하한 1%)은 자리와 무관한 값이다. 근거가 깨지는 지점이
    더 가깝다면 거기서 나가야 원문의 손익비가 성립한다.
    """
    pos = _pos(entry=1000.0, stop=996.0)      # 0.4% — MIN_STOP_RATIO(1%)보다 가깝다
    assert pos.check_price_exit(996.0) == ExitReason.STOP_LOSS
    # 사이징은 하한을 적용하지만(리스크 과대 방지), 청산 트리거는 구조 그대로다
    assert pos.sizing_stop_ratio >= C.MIN_STOP_RATIO


# ── 익절: 반익반본 ───────────────────────────────────────────
def test_직전고점_도달시_전량이_아니라_부분익절_신호가_난다():
    pos = _pos(entry=1000.0, target=1030.0)
    assert pos.check_price_exit(1029.0) == ExitReason.NONE
    assert pos.check_price_exit(1030.0) == ExitReason.PARTIAL_TAKE_PROFIT


def test_부분익절_비중은_스펙의_partial_tp_ratio를_따른다():
    pos = _pos()
    assert pos.spec.partial_tp_ratio == 0.5
    assert pos.partial_exit_volume() == pytest.approx(15.0)


def test_반익_후에는_손절이_본절_진입가로_올라온다():
    pos = _pos(entry=1000.0, stop=990.0, target=1030.0)
    pos.take_partial(fill_price=1030.0)

    assert pos.half_taken is True
    assert pos.effective_stop_price == 1000.0           # 본절
    assert pos.check_price_exit(999.0) == ExitReason.STOP_LOSS
    assert pos.check_price_exit(995.0) == ExitReason.STOP_LOSS   # 원래 손절 위에서도 나간다


def test_반익_후_나머지는_목표없이_추세를_태운다():
    """원문: '강력한 추세가 터지면 남은 절반으로 수익을 극대화'. 2차 목표는 두지 않고
    시간손절·역신호가 정리한다."""
    pos = _pos(entry=1000.0, target=1030.0)
    pos.take_partial(fill_price=1030.0)
    assert pos.target_price is None
    assert pos.check_price_exit(1100.0) == ExitReason.NONE


def test_반익은_한_번만_난다():
    pos = _pos()
    pos.take_partial(fill_price=1030.0)
    assert pos.check_price_exit(1040.0) == ExitReason.NONE


def test_반익_후_잔량과_원금이_절반으로_줄어든다():
    pos = _pos(volume=30.0, size=30_000.0)
    pos.take_partial(fill_price=1030.0)
    assert pos.volume == pytest.approx(15.0)
    assert pos.size_krw == pytest.approx(15_000.0)


def test_반익_실현손익은_판_수량만큼만_잡힌다():
    pos = _pos(entry=1000.0, volume=30.0, size=30_000.0)
    pnl = pos.partial_pnl_krw(fill_price=1030.0)
    # 15,000원어치를 +3%에 팔았고 왕복수수료 0.1% 차감
    assert pnl == pytest.approx(15_000 * (0.03 - C.FEE_ROUNDTRIP))


# ── 잔량이 최소주문금액 미만이면 반익하지 않는다 ──────────────
def test_잔량이_최소주문금액_미만이면_반익하지_않고_전량익절한다():
    """
    5,000원 미만 잔량은 업비트에서 팔 수 없다(under_min_total_market_ask).
    실매매에서 0.5 AVAX 먼지가 영구 청산 불가로 남은 사고가 실제로 있었다.
    """
    pos = _pos(entry=1000.0, volume=8.0, size=8_000.0, target=1030.0)
    assert pos.check_price_exit(1030.0) == ExitReason.TAKE_PROFIT


# ── 레벨이 없는 전략은 기존 동작 그대로 ──────────────────────
def test_구조레벨이_없는_전략은_기존_비율청산_그대로():
    pos = Position(
        strategy="rsi2", market="KRW-TEST", entry_price=1000.0,
        size_krw=30_000.0, volume=30.0,
        entry_time=pd.Timestamp("2026-08-03 00:00"), entry_atr=5.0,
    )
    assert pos.stop_price is None and pos.target_price is None
    assert pos.sl_ratio == pytest.approx(0.025)          # rsi2 고정 손절 2.5%
    assert pos.check_price_exit(975.0) == ExitReason.STOP_LOSS
    assert pos.check_price_exit(1025.0) == ExitReason.TAKE_PROFIT
    assert pos.partial_exit_volume() == 0.0              # 반익절 미사용


# ── 진입 손익비 필터 ─────────────────────────────────────────
def test_손익비가_기준_미만이면_진입하지_않는다():
    # 손절 -1.0%, 목표 +1.0% → RR 1.0 < 1.2
    assert C.entry_rr(entry=1000.0, stop=990.0, target=1010.0) == pytest.approx(1.0)
    assert not C.entry_rr_ok(entry=1000.0, stop=990.0, target=1010.0)
    # 손절 -1.0%, 목표 +2.0% → RR 2.0
    assert C.entry_rr_ok(entry=1000.0, stop=990.0, target=1020.0)


def test_손절거리가_비정상이면_진입불가():
    assert not C.entry_rr_ok(entry=1000.0, stop=1000.0, target=1020.0)  # 거리 0
    assert not C.entry_rr_ok(entry=1000.0, stop=1010.0, target=1020.0)  # 손절이 위
    # 손절거리가 너무 멀면(>5%) 자리로 인정하지 않는다
    assert not C.entry_rr_ok(entry=1000.0, stop=940.0, target=1200.0)


# ── 스펙: 원문에 없는 청산 규칙을 껐는지 ─────────────────────
def test_easy_teaching은_횡보청산을_쓰지_않는다():
    """실매매 13건 중 4건을 죽인 규칙. 원문에 없는 규칙이다."""
    assert C.STRATEGY_SPECS["easy_teaching"].use_dead_extras is False


def test_easy_teaching_시간손절은_익절이_도달할_수_있을_만큼_길다():
    """
    8봉(2시간)은 '익절 못 가면 비용만 내고 나감'의 다른 이름이었다.
    실측 MFE 도달까지 최장 24봉이 걸렸다 → 그보다 넉넉해야 한다.
    """
    assert C.time_stop_bars_for(C.STRATEGY_SPECS["easy_teaching"]) >= 48


# ── 트레이더 통합: 반익절이 실제로 절반만 팔고 포지션을 유지하는가 ──────
from tests.test_multi_position import bare_trader, flat_df   # noqa: E402
from data.upbit_client import OrderResult                    # noqa: E402


def _trader_with_partial_pos(fill=True, fill_ratio=1.0):
    t = bare_trader(("rsi2_15m",))
    sold = []

    class Orders:
        def enter_long(self, market, price, krw):
            return OrderResult(ok=True, filled_volume=krw / price, avg_price=price)

        def exit_position(self, pos, price, reason):
            sold.append(("full", pos.volume, price))
            return OrderResult(ok=True, filled_volume=pos.volume, avg_price=price)

        def exit_partial(self, pos, price, volume):
            sold.append(("partial", volume, price))
            if not fill:
                return OrderResult(ok=False, error="partial unfilled — retry next tick")
            return OrderResult(ok=True, filled_volume=volume * fill_ratio, avg_price=price)

    t.orders = Orders()
    pos = Position(strategy="easy_teaching", market="KRW-BTC", entry_price=1000.0,
                   size_krw=30_000.0, volume=30.0,
                   entry_time=pd.Timestamp("2026-07-01"), entry_atr=10.0,
                   stop_price=990.0, target_price=1030.0)
    t.positions[pos.key] = pos
    t.risk.s.open_positions = 1
    return t, pos, sold


def _frames_at(price: float):
    df = flat_df(tf="15min")
    for col in ("open", "high", "low", "close"):
        df[col] = price
    return {"minute15": df, "minute5": df}


def test_트레이더_목표도달시_절반만_팔고_포지션은_유지된다():
    t, pos, sold = _trader_with_partial_pos()
    t._process_market("KRW-BTC", _frames_at(1030.0))

    assert sold == [("partial", 15.0, 1030.0)]
    assert pos.key in t.positions, "반익절은 청산이 아니다 — 포지션이 남아야 한다"
    assert pos.volume == pytest.approx(15.0)
    assert pos.half_taken is True
    assert t.risk.s.open_positions == 1, "보유 개수를 줄이면 안 된다"
    assert t.risk.s.consecutive_losses == 0


def test_트레이더_반익_후_본절스탑에_걸리면_전량_청산된다():
    t, pos, sold = _trader_with_partial_pos()
    t._process_market("KRW-BTC", _frames_at(1030.0))     # 반익
    t._process_market("KRW-BTC", _frames_at(999.0))      # 본절 이탈

    assert sold[-1][0] == "full"
    assert pos.key not in t.positions
    assert t.risk.s.open_positions == 0


def test_반익_체결실패시_상태를_바꾸지_않는다():
    """상태만 바꾸면 실계좌와 어긋난 오펀이 된다 — 실매매에서 실제로 난 사고다."""
    t, pos, sold = _trader_with_partial_pos(fill=False)
    t._process_market("KRW-BTC", _frames_at(1030.0))

    assert pos.half_taken is False
    assert pos.volume == pytest.approx(30.0)
    assert pos.target_price == 1030.0
    assert any(e[0] == "exit_fail" for e in t.logger.events)


def test_반익_부분체결이면_체결분만_반영하고_잔량을_되돌린다():
    t, pos, sold = _trader_with_partial_pos(fill_ratio=0.6)   # 15개 중 9개만 체결
    t._process_market("KRW-BTC", _frames_at(1030.0))

    assert pos.volume == pytest.approx(15.0 + 6.0)
    assert pos.half_taken is True


def test_손익비_미달이면_진입하지_않고_이유를_남긴다():
    t = bare_trader(("rsi2_15m",))

    class Sig:
        stop_price, target_price = 990.0, 1005.0
        meta: dict = {}    # RR 0.5

    t._open("rsi2_15m", t.strategies["rsi2_15m"], "KRW-BTC", 1000.0,
            flat_df(tf="15min"), note="test", sig=Sig())
    assert not t.positions
    skips = [kw for ev, kw in t.logger.events if ev == "entry_skip"]
    assert skips and "손익비" in skips[0]["reason"]


def test_구조레벨_진입시_사이징은_구조_손절거리를_쓴다():
    t = bare_trader(("rsi2_15m",))
    captured = {}

    class Orders:
        def enter_long(self, market, price, krw):
            captured["krw"] = krw
            return OrderResult(ok=True, filled_volume=krw / price, avg_price=price)
    t.orders = Orders()

    class Sig:
        stop_price, target_price = 980.0, 1040.0
        meta: dict = {}    # 손절 2.0%, RR 2.0

    t._open("rsi2_15m", t.strategies["rsi2_15m"], "KRW-BTC", 1000.0,
            flat_df(tf="15min"), note="test", sig=Sig())
    pos = next(iter(t.positions.values()))
    assert pos.stop_price == 980.0 and pos.target_price == 1040.0
    # 구조 손절거리(2.0%)로 사이징한다 — 배분상한·잔고 clamp 를 반영한 값과 일치해야 한다
    assert captured["krw"] == pytest.approx(C.position_size_krw(0.02, 90_000.0))

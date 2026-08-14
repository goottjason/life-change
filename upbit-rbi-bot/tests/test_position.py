"""ATR 기반 SL/TP 청산 판정 (헌장 v1.2 §4)."""
import pandas as pd
from bot.position import Position, ExitReason


def _p(entry=100.0, atr=2.0, strategy="macd"):
    return Position(strategy=strategy, market="KRW-BTC", entry_price=entry,
                    size_krw=30000, volume=1.0,
                    entry_time=pd.Timestamp("2026-07-26T00:00:00"), entry_atr=atr)


def test_atr_sl_tp_ratios_computed():
    p = _p(entry=100, atr=2)   # macd k=1.5 → sl=1.5*2/100=0.03, rr=2 → tp=0.06
    assert round(p.sl_ratio, 4) == 0.03
    assert round(p.tp_ratio, 4) == 0.06


def test_stop_loss_triggers_at_atr_distance():
    p = _p(entry=100, atr=2)   # sl 3%
    assert p.check_price_exit(96.9) == ExitReason.STOP_LOSS   # -3.1%
    assert p.check_price_exit(98.0) == ExitReason.NONE        # -2%


def test_take_profit_triggers_at_atr_distance():
    p = _p(entry=100, atr=2)   # tp 6%
    assert p.check_price_exit(106.1) == ExitReason.TAKE_PROFIT
    assert p.check_price_exit(105.0) == ExitReason.NONE


def test_fallback_ratio_when_no_atr():
    p = _p(entry=100, atr=0)   # ATR 없음 → FALLBACK_STOP_RATIO(0.03)
    assert round(p.sl_ratio, 4) == 0.03
    assert round(p.tp_ratio, 4) == 0.06   # macd rr=2 × 0.03


# ── v4.0 실험 트랙: 트레일링 스톱 + 시간손절 수익 유예 ──────────────
import pandas as _pd
from bot import dead_position as _dead


def _breakout_pos(entry=100.0, atr=0.3):
    return Position(strategy="breakout", market="KRW-TEST", entry_price=entry,
                    size_krw=10_000, volume=100.0,
                    entry_time=_pd.Timestamp("2026-08-14 09:00"), entry_atr=atr)


def test_breakout_trailing_stop_exits_below_high_minus_atr():
    """고점 − 1.5×진입ATR 하회 시 청산. 수익 중이면 take_profit, 손실이면 stop_loss."""
    pos = _breakout_pos(entry=100.0, atr=0.3)      # 트레일 거리 = 0.45
    pos.update_high(102.0)
    assert pos.check_price_exit(101.7) == ExitReason.NONE         # 102−0.45=101.55 위
    assert pos.check_price_exit(101.5) == ExitReason.TAKE_PROFIT  # 진입가 위에서 트레일 이탈
    pos2 = _breakout_pos(entry=100.0, atr=0.3)
    pos2.update_high(100.0)                         # 고점 갱신 없이 하락
    assert pos2.check_price_exit(99.5) == ExitReason.STOP_LOSS    # 100−0.45=99.55 아래, 손실


def test_breakout_no_fixed_take_profit():
    """rr=0.0이어도 즉시 익절되면 안 된다 — 트레일링이 익절을 대체한다."""
    pos = _breakout_pos(entry=100.0, atr=0.3)
    pos.update_high(100.4)
    assert pos.check_price_exit(100.4) == ExitReason.NONE


def test_breakout_hard_stop_backstop():
    """트레일보다 고정 손절(sl_ratio = max(1×ATR/가격, 1%))이 위에 있으면 그쪽이 잡는다."""
    pos = _breakout_pos(entry=100.0, atr=2.0)
    # atr=2.0 → sl_ratio = max(1.0×2/100, 1%) = 2% → 고정손절 98.0 · 트레일 100−3=97.0 → 98이 위
    assert pos.check_price_exit(97.9) == ExitReason.STOP_LOSS


def test_rsi2_ratio_exit_unchanged_v4():
    """검증 트랙 회귀: rsi2는 고정 ±2.5% 비율 청산 그대로 (§11 재현성)."""
    pos = Position(strategy="rsi2", market="KRW-TEST", entry_price=100.0,
                   size_krw=50_000, volume=500.0,
                   entry_time=_pd.Timestamp("2026-08-14 09:00"), entry_atr=0.5)
    assert pos.check_price_exit(97.4) == ExitReason.STOP_LOSS
    assert pos.check_price_exit(102.6) == ExitReason.TAKE_PROFIT
    assert pos.check_price_exit(101.0) == ExitReason.NONE


def _df_after_entry(entry_ts: str, bars: int, close: float) -> _pd.DataFrame:
    idx = _pd.date_range(_pd.Timestamp(entry_ts), periods=bars + 1, freq="5min")
    return _pd.DataFrame({"open": close, "high": close, "low": close,
                          "close": close, "volume": 1.0}, index=idx)


def test_breakout_time_stop_exits_when_flat():
    """12봉 경과 & 수익 < +0.3% → 시간손절."""
    pos = _breakout_pos(entry=100.0)
    df = _df_after_entry("2026-08-14 09:00", 12, close=100.1)   # +0.1% < +0.3%
    dead, why = _dead.is_dead(pos, df)
    assert dead and "time_stop" in why


def test_breakout_time_stop_deferred_when_in_profit():
    """12봉 경과라도 수익 ≥ +0.3%면 유예 — 트레일링이 마무리한다."""
    pos = _breakout_pos(entry=100.0)
    df = _df_after_entry("2026-08-14 09:00", 12, close=100.5)   # +0.5%
    dead, _ = _dead.is_dead(pos, df)
    assert not dead

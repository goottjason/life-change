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

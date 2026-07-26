"""체결 내역(진입↔청산 페어링) 로직 검증 (Task B)."""
from __future__ import annotations

from dashboard.service import pair_round_trips


def _row(id_, ts, event, strategy, market, price, pnl=0.0, reason=""):
    return {"ts": ts, "event": event, "strategy": strategy, "market": market,
            "price": price, "volume": 1.0, "size_krw": 30000, "pnl_krw": pnl,
            "reason": reason}


def test_pairs_entry_with_next_exit_same_strategy():
    rows = [
        _row(1, "2026-07-26T09:00:00+09:00", "entry", "macd", "KRW-BTC", 100, reason="macd cross up"),
        _row(2, "2026-07-26T10:30:00+09:00", "exit", "macd", "KRW-BTC", 106, pnl=1800, reason="take_profit:"),
    ]
    trips = pair_round_trips(rows)
    assert len(trips) == 1
    t = trips[0]
    assert t["entry_price"] == 100 and t["exit_price"] == 106
    assert t["pnl_krw"] == 1800
    assert t["hold_sec"] == 5400          # 1시간30분
    assert t["entry_reason"] == "macd cross up"
    assert "take_profit" in t["exit_reason"]


def test_interleaved_strategies_pair_independently():
    rows = [
        _row(1, "2026-07-26T09:00:00+09:00", "entry", "macd", "KRW-BTC", 100),
        _row(2, "2026-07-26T09:10:00+09:00", "entry", "rsi", "KRW-ETH", 50),
        _row(3, "2026-07-26T09:20:00+09:00", "exit", "rsi", "KRW-ETH", 52, pnl=500),
        _row(4, "2026-07-26T09:30:00+09:00", "exit", "macd", "KRW-BTC", 98, pnl=-300),
    ]
    trips = pair_round_trips(rows)
    assert len(trips) == 2
    by_strat = {t["strategy"]: t for t in trips}
    assert by_strat["rsi"]["entry_price"] == 50 and by_strat["rsi"]["exit_price"] == 52
    assert by_strat["macd"]["entry_price"] == 100 and by_strat["macd"]["exit_price"] == 98


def test_orphan_exit_without_entry_is_tolerated():
    rows = [_row(1, "2026-07-26T09:00:00+09:00", "exit", "cvd", "KRW-BTC", 100, pnl=0)]
    trips = pair_round_trips(rows)
    assert len(trips) == 1
    assert trips[0]["entry_price"] is None
    assert trips[0]["hold_sec"] is None

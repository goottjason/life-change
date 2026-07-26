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


# ── v1.4: 한 전략이 여러 코인을 동시 보유 ────────────────────
def test_same_strategy_two_markets_pair_by_market():
    """
    v1.4에서 '전략당 1포지션' 제약이 풀렸다. 전략명만으로 짝지으면 두 코인의
    진입가·손익이 뒤섞이므로 '전략:코인'으로 짝지어야 한다.
    """
    rows = [
        _row(1, "2026-07-26T09:00:00+09:00", "entry", "rsi2", "KRW-BTC", 100, reason="btc 진입"),
        _row(2, "2026-07-26T09:05:00+09:00", "entry", "rsi2", "KRW-XRP", 500, reason="xrp 진입"),
        _row(3, "2026-07-26T10:00:00+09:00", "exit", "rsi2", "KRW-XRP", 515, pnl=900, reason="reverse:"),
        _row(4, "2026-07-26T11:00:00+09:00", "exit", "rsi2", "KRW-BTC", 102, pnl=600, reason="reverse:"),
    ]
    trips = {t["market"]: t for t in pair_round_trips(rows)}
    assert trips["KRW-XRP"]["entry_price"] == 500 and trips["KRW-XRP"]["exit_price"] == 515
    assert trips["KRW-BTC"]["entry_price"] == 100 and trips["KRW-BTC"]["exit_price"] == 102
    assert trips["KRW-BTC"]["entry_reason"] == "btc 진입"
    assert trips["KRW-XRP"]["hold_sec"] == 3300      # 55분


def test_two_timeframe_strategies_same_market_are_separate():
    """rsi2(5분)와 rsi2_15m(15분)이 같은 코인을 각각 들고 있어도 섞이지 않는다."""
    rows = [
        _row(1, "2026-07-26T09:00:00+09:00", "entry", "rsi2", "KRW-BTC", 100),
        _row(2, "2026-07-26T09:00:00+09:00", "entry", "rsi2_15m", "KRW-BTC", 101),
        _row(3, "2026-07-26T10:00:00+09:00", "exit", "rsi2_15m", "KRW-BTC", 104, pnl=900),
        _row(4, "2026-07-26T10:30:00+09:00", "exit", "rsi2", "KRW-BTC", 103, pnl=600),
    ]
    trips = {t["strategy"]: t for t in pair_round_trips(rows)}
    assert trips["rsi2"]["entry_price"] == 100 and trips["rsi2"]["exit_price"] == 103
    assert trips["rsi2_15m"]["entry_price"] == 101 and trips["rsi2_15m"]["exit_price"] == 104

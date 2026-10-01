"""Ledger review must separate recovered exits, matched returns and account equity."""
from backtesting.research.lab_live_review import live_report, pair, kst


def row(id_, event, pnl=0, size=10_000):
    return {"id": id_, "event": event, "strategy": "breakout", "market": "KRW-X",
            "ts": f"2026-10-01T00:{id_:02d}:00+00:00", "pnl_krw": pnl,
            "size_krw": size, "context": '{"fingerprint":"old","vol_ratio":5}', "reason": ""}


def test_recovery_and_replaced_entries_do_not_duplicate_paired_returns():
    rows = [row(1, "exit", 10), row(2, "entry"), row(3, "entry", size=5_000),
            row(4, "exit", 50), row(5, "exit", -10)]
    trips, quality = pair(rows)
    assert len(trips) == 1
    assert trips[0]["pct"] == 1
    assert quality["overwritten_entries"] == [2]
    assert quality["unmatched_exits"] == [1, 5]


def test_logged_peak_and_unreconciled_equity_are_distinct():
    rows = [row(1, "entry"), row(2, "exit", 100), row(3, "entry"), row(4, "exit", -50)]
    rep = live_report(rows, initial=90_000, observed=90_200)
    assert rep["logged_realized_peak"]["exit_id"] == 2
    assert rep["after_logged_peak"]["breakout"]["pnl_krw"] == -50
    assert rep["reconciliation"]["unreconciled_difference_krw"] == 150
    assert rep["all_exits_by_strategy"]["breakout"]["pnl_krw"] == 50
    assert kst(rows[0]["ts"]) == "2026-10-01T09:01:00+09:00"

"""Financial-model checks: no stop-price fantasy after gaps or future trend leak."""
from dataclasses import replace
import numpy as np
import pandas as pd
import pytest

from backtesting.research.lab_profit_search import B, hourly_up, simulate


def breakout_frame():
    idx = pd.date_range("2026-08-01", periods=310, freq="5min")
    df = pd.DataFrame({"open": 100., "high": 100.1, "low": 99.9,
                       "close": 100., "volume": 100.}, index=idx)
    df.iloc[305] = [100., 103., 99.9, 103., 600.]
    df.iloc[306] = [104., 104., 103.5, 104., 100.]
    df.iloc[307] = [100., 101., 99., 100., 100.]
    return df


def test_breakout_enters_next_open_and_slips_through_gap_stop():
    df = breakout_frame()
    trades = simulate(df, df, replace(B, trail=100), .002,
                      df.index[0], df.index[-1] + pd.Timedelta(minutes=5))
    first = trades[0]
    assert first["entry"] == str(df.index[306])
    assert first["reason"] == "gap_stop"
    assert first["net"] == pytest.approx(100 / 104 - 1 - .002)


def test_same_candle_high_does_not_raise_trailing_stop_before_low():
    df = breakout_frame()
    df.iloc[306] = [104., 110., 103.5, 108., 100.]
    trades = simulate(df, df, B, .002, df.index[0], df.index[-1] + pd.Timedelta(minutes=5))
    assert trades[0]["exit"] == str(df.index[307] + pd.Timedelta(minutes=5))


def test_unfinished_hour_and_future_data_do_not_change_previous_trend():
    idx = pd.date_range("2026-08-01", periods=4000, freq="5min")
    df = pd.DataFrame({"close": np.linspace(100., 200., len(idx))}, index=idx)
    cutoff = pd.Timestamp("2026-08-10 09:30")
    previous = hourly_up(df, idx)
    df.loc[cutoff:, "close"] = 1.
    modified = hourly_up(df, idx)
    assert np.array_equal(previous[idx <= cutoff], modified[idx <= cutoff])

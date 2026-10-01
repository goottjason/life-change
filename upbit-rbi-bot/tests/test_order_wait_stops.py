"""A pending entry must not leave an already-held coin without stop checks."""
from bot.order_manager import OrderManager
from bot.position import ExitReason
from data.upbit_client import OrderResult
from tests.test_held_market_management import _trader, _hold, _flat, _crash


def test_held_stop_runs_before_universe_refresh():
    t = _trader(["KRW-BTC"], {"KRW-BTC": _flat(), "KRW-ETH": _crash()})
    _hold(t, "KRW-ETH")

    def refresh():
        assert not t.positions, "a slow universe refresh must follow the stop check"
        return ["KRW-BTC"]
    t.screener.eligible = refresh
    t.tick()
    assert t.orders.sold[0] == ("KRW-ETH", ExitReason.STOP_LOSS)


def test_entry_fill_wait_services_another_held_stop():
    t = _trader([], {"KRW-ETH": _crash()})
    _hold(t, "KRW-ETH")

    class Waiting:
        def get_open_orders(self, market):
            assert not t.positions, "stop should run while waiting for the new buy"
            return []
    om = OrderManager(Waiting(), poll_timeout_sec=0)
    om.on_wait = t._check_held_price_exits
    assert om._wait_fill("KRW-BTC", OrderResult(ok=True, order_id="buy"))
    assert len(t.orders.sold) == 1


def test_fill_confirmation_services_held_stop_too():
    t = _trader([], {"KRW-ETH": _crash()})
    _hold(t, "KRW-ETH")

    class Confirming:
        def get_order_detail(self, order_id):
            assert not t.positions
            return {"state": "done", "executed_volume": "1"}
    om = OrderManager(Confirming(), poll_timeout_sec=0)
    om.on_wait = t._check_held_price_exits
    res = om._confirm_fill(OrderResult(ok=True, order_id="buy"), 1)
    assert res.ok and res.filled_volume == 1


def test_wait_callback_does_not_sell_the_currently_closing_position_twice():
    t = _trader([], {"KRW-ETH": _crash()})
    pos = _hold(t, "KRW-ETH")
    calls = []

    class Closing:
        def exit_position(self, p, price, reason):
            calls.append(p.key)
            t._check_held_price_exits()
            return OrderResult(ok=True, filled_volume=p.volume, avg_price=price)
    t.orders = Closing()
    t._close(pos, 9500, ExitReason.STOP_LOSS)
    assert calls == [pos.key]
    assert not t.positions


def test_one_price_failure_does_not_hide_another_held_stop():
    t = _trader([], {"KRW-BTC": _flat(), "KRW-ETH": _crash()})
    _hold(t, "KRW-BTC")
    _hold(t, "KRW-ETH")
    original = t.client.get_price

    def price(market):
        if market == "KRW-BTC":
            raise RuntimeError("ticker unavailable")
        return original(market)
    t.client.get_price = price
    t._check_held_price_exits()
    assert "breakout:KRW-BTC" in t.positions
    assert "breakout:KRW-ETH" not in t.positions


def test_wait_checks_are_throttled_without_losing_stops(monkeypatch):
    t = _trader([], {"KRW-ETH": _flat()})
    _hold(t, "KRW-ETH")
    clock = [100.]
    monkeypatch.setattr("bot.trader.time.monotonic", lambda: clock[0])
    calls = []
    original = t.client.get_price
    t.client.get_price = lambda m: calls.append(m) or original(m)
    t._check_held_price_exits()
    for _ in range(10):
        t._check_held_price_exits()
    assert len(calls) == 1
    clock[0] += 10
    t.client.frames["KRW-ETH"] = _crash()
    t._check_held_price_exits()
    assert len(calls) == 2 and not t.positions


def test_disabled_breakout_recovers_in_its_original_slot(monkeypatch):
    from bot.trader import Trader, build_strategies
    monkeypatch.setattr("bot.trader.last_entry_strategy", lambda market: "breakout")
    assert Trader._recover_slot("KRW-BTC", set(), build_strategies()) == "breakout"


def test_disabled_strategy_still_gets_candle_exit_checks():
    t = _trader([], {"KRW-ETH": _flat()}, strategies=())
    _hold(t, "KRW-ETH")
    t.tick()
    assert "KRW-ETH" in t.client.fetched
    assert not t.positions  # old holding exits on its time-stop, no new entry


def test_kill_switch_confirmation_does_not_launch_a_second_sell():
    t = _trader([], {"KRW-ETH": _crash()})
    _hold(t, "KRW-ETH")
    t.failsafe.triggered = True
    t._check_held_price_exits()
    assert not t.orders.sold

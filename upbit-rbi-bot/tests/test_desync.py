"""
DB/메모리 ↔ 실계좌 상태 desync 회귀 방지 테스트 (최우선 버그).

검증 대상 근본원인:
  ① 진입/청산이 실제 체결량(executed_volume)을 확인하지 않음
     → 포지션 volume=0, 청산 시 0개 매도(=미체결)인데 성공으로 기록.
  ② _close 가 res.ok / filled_volume 무검사 → 실패해도 포지션 제거 + exit 기록(오펀).
  ③ 청산 미체결/실패 시 시장가 강제청산이 안 됨.
  ④ recover_state 가 실잔고로 오펀 포지션을 복원하지 않음.
"""
from __future__ import annotations

import pandas as pd
import pytest

from bot.order_manager import OrderManager
from bot.position import Position, ExitReason
from bot.trader import Trader
from bot.risk_manager import RiskManager
from data.upbit_client import OrderResult


# ── 테스트용 가짜 거래소 클라이언트 ─────────────────────────────
class FakeClient:
    """프로그래밍 가능한 업비트 클라이언트 대역. 실계좌 대신 메모리 상태."""

    def __init__(self):
        self.open_orders: dict[str, list] = {}   # market -> [orders]
        self.details: dict[str, dict] = {}       # uuid -> order detail
        self.balances: list[dict] = []
        self.buy_time: dict[str, float] = {}
        self.calls: list[tuple] = []
        self._seq = 0

    def _uuid(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    # 주문
    def buy_limit(self, market, price, krw):
        self.calls.append(("buy_limit", market, price, krw))
        uid = self._uuid("buy")
        # 기본: 즉시 전량 체결로 상세 등록
        vol = krw / price
        self.details[uid] = {
            "state": "done", "executed_volume": vol,
            "trades": [{"price": price, "volume": vol, "funds": price * vol}],
        }
        return OrderResult(ok=True, order_id=uid)

    def sell_limit(self, market, price, volume):
        self.calls.append(("sell_limit", market, price, volume))
        return OrderResult(ok=True, order_id=self._uuid("sell"))

    def sell_market(self, market, volume):
        self.calls.append(("sell_market", market, volume))
        uid = self._uuid("mkt")
        px = 100.0
        self.details[uid] = {
            "state": "done", "executed_volume": volume,
            "trades": [{"price": px, "volume": volume, "funds": px * volume}],
        }
        return OrderResult(ok=True, order_id=uid)

    def cancel(self, order_id):
        self.calls.append(("cancel", order_id))
        return True

    def get_open_orders(self, market):
        return self.open_orders.get(market, [])

    def get_order_detail(self, uuid):
        return self.details.get(uuid)

    def get_balances(self):
        return self.balances

    def get_last_buy_time(self, market):
        return self.buy_time.get(market)


def _om(client, **kw):
    return OrderManager(client, poll_timeout_sec=0, poll_interval_sec=0, **kw)


def _pos(volume=1.0, market="KRW-BTC", strategy="macd"):
    return Position(strategy=strategy, market=market, entry_price=100.0,
                    size_krw=100.0, volume=volume,
                    entry_time=pd.Timestamp("2026-07-26T00:00:00"))


# ── ① 진입: 실제 체결량을 채워야 한다 (volume=0 방지) ──────────
def test_enter_long_populates_real_filled_volume():
    c = FakeClient()
    res = _om(c).enter_long("KRW-BTC", price=100.0, krw=30_000)
    assert res.ok
    assert res.filled_volume == pytest.approx(300.0)   # 30000/100
    assert res.avg_price == pytest.approx(100.0)


# ── ③ 청산: 지정가 미체결이면 시장가 강제청산 ─────────────────
def test_exit_forces_market_when_limit_unfilled():
    c = FakeClient()
    pos = _pos(volume=2.0)
    # 지정가 주문은 체결 0 (details 미등록 → executed 0)
    res = _om(c).exit_position(pos, price=100.0, reason=ExitReason.REVERSE_SIGNAL)
    kinds = [x[0] for x in c.calls]
    assert "sell_limit" in kinds
    assert "cancel" in kinds          # 미체결 → 취소
    assert "sell_market" in kinds     # → 시장가 강제청산
    assert res.ok and res.filled_volume == pytest.approx(2.0)


# ── ② _close: 청산 실패면 포지션 유지 + exit 기록 안 함 ────────
class _FailingOrders:
    def exit_position(self, pos, price, reason):
        return OrderResult(ok=False, error="insufficient volume", filled_volume=0.0)


class _RecordingLogger:
    def __init__(self):
        self.events = []

    def log(self, event, **kw):
        self.events.append((event, kw))


class _NullNotifier:
    def send(self, msg):
        pass


def _bare_trader():
    t = Trader.__new__(Trader)
    t.risk = RiskManager()
    t.risk.s.open_positions = 1
    t.positions = {}
    t.logger = _RecordingLogger()
    t.notifier = _NullNotifier()
    t.last_prices = {}
    return t


def test_close_keeps_position_when_exit_fails():
    t = _bare_trader()
    pos = _pos()
    t.positions[pos.key] = pos          # v1.4: 키 = 전략:코인
    t.orders = _FailingOrders()

    t._close(pos, price=100.0, reason=ExitReason.REVERSE_SIGNAL)

    assert pos.key in t.positions, "청산 실패 시 포지션을 유지해야 함(오펀 방지)"
    events = [e for e, _ in t.logger.events]
    assert "exit" not in events, "실패한 청산을 exit 로 기록하면 안 됨"


class _OkOrders:
    def exit_position(self, pos, price, reason):
        return OrderResult(ok=True, filled_volume=pos.volume, avg_price=100.0)


class _PartialOrders:
    def __init__(self, filled):
        self.filled = filled

    def exit_position(self, pos, price, reason):
        return OrderResult(ok=True, filled_volume=self.filled, avg_price=100.0)


def test_close_keeps_remainder_on_partial_fill():
    t = _bare_trader()
    pos = _pos(volume=200.0)          # 진입가 100 → 잔량 100개 = 10,000원(>최소주문)
    t.positions[pos.key] = pos          # v1.4: 키 = 전략:코인
    t.orders = _PartialOrders(filled=100.0)

    t._close(pos, price=100.0, reason=ExitReason.REVERSE_SIGNAL)

    assert pos.key in t.positions              # 잔량 유지
    assert t.positions[pos.key].volume == pytest.approx(100.0)
    assert "exit_partial" in [e for e, _ in t.logger.events]


def test_close_finalizes_when_remainder_is_dust():
    t = _bare_trader()
    pos = _pos(volume=200.0)          # 잔량 1개 = 100원(<최소주문 5,000) → 먼지 → 청산완료
    t.positions[pos.key] = pos          # v1.4: 키 = 전략:코인
    t.orders = _PartialOrders(filled=199.0)

    t._close(pos, price=100.0, reason=ExitReason.REVERSE_SIGNAL)

    assert pos.key not in t.positions
    assert "exit" in [e for e, _ in t.logger.events]


def test_close_removes_position_when_exit_confirmed():
    t = _bare_trader()
    pos = _pos()
    t.positions[pos.key] = pos          # v1.4: 키 = 전략:코인
    t.orders = _OkOrders()

    t._close(pos, price=100.0, reason=ExitReason.REVERSE_SIGNAL)

    assert pos.key not in t.positions
    events = [e for e, _ in t.logger.events]
    assert "exit" in events


# ── ④ recover_state: 실잔고로 오펀 포지션 복원 ─────────────────
def test_recover_state_rebuilds_orphan_positions():
    from safety.failsafe import Failsafe

    c = FakeClient()
    c.balances = [
        {"currency": "KRW", "balance": "50000", "locked": "0"},
        {"currency": "BTC", "balance": "0.001", "locked": "0",
         "avg_buy_price": "160000000"},
    ]
    c.buy_time["KRW-BTC"] = pd.Timestamp("2026-07-26T09:00:00").timestamp()

    fs = Failsafe(c, order_manager=None)
    orphans = fs.recover_state(known_markets=set())

    assert len(orphans) == 1
    o = orphans[0]
    assert o["market"] == "KRW-BTC"
    assert o["volume"] == pytest.approx(0.001)
    assert o["avg_price"] == pytest.approx(160000000.0)


def test_boot_recovers_orphan_as_managed_position():
    """부팅 시 오펀 코인이 전략 슬롯에 관리 포지션으로 복원되고, entry_time 이 tz-naive(KST)여야 한다."""
    from safety.failsafe import Failsafe
    from bot.trader import build_strategies

    c = FakeClient()
    c.balances = [
        {"currency": "BTC", "balance": "0.001", "locked": "0", "avg_buy_price": "160000000"},
    ]
    c.buy_time["KRW-BTC"] = pd.Timestamp("2026-07-26T09:00:00+09:00").timestamp()

    t = Trader.__new__(Trader)
    t.client = c
    t.strategies = build_strategies()
    t.positions = {}
    t.risk = RiskManager()
    t.logger = _RecordingLogger()
    t.notifier = _NullNotifier()
    t.failsafe = Failsafe(c, order_manager=None, notifier=_NullNotifier())

    t._recover_positions()

    assert len(t.positions) == 1
    pos = next(iter(t.positions.values()))
    assert pos.market == "KRW-BTC"
    assert pos.entry_price == pytest.approx(160000000.0)
    assert pos.volume == pytest.approx(0.001)
    assert pos.entry_time.tzinfo is None, "entry_time 은 pyupbit 인덱스와 맞춰 tz-naive 여야 함"
    assert pos.entry_time.hour == 9   # KST 09:00
    assert t.risk.s.open_positions == 1
    assert "recover" in [e for e, _ in t.logger.events]


def test_recover_state_ignores_known_and_dust():
    from safety.failsafe import Failsafe

    c = FakeClient()
    c.balances = [
        {"currency": "BTC", "balance": "0.001", "locked": "0", "avg_buy_price": "160000000"},
        {"currency": "ETH", "balance": "0.00000001", "locked": "0", "avg_buy_price": "5000000"},  # dust
    ]
    fs = Failsafe(c, order_manager=None)
    orphans = fs.recover_state(known_markets={"KRW-BTC"})  # BTC 는 이미 추적 중
    markets = [o["market"] for o in orphans]
    assert "KRW-BTC" not in markets       # 이미 알고 있음
    assert "KRW-ETH" not in markets       # 먼지(최소주문 미만) 제외

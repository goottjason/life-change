"""
청산 실행 계층의 복원력 (v2.7).

실매매 사고(2026-07-31, 서버 DB 실측):
  - `exit_fail: unexpected response: None` **2,854건**을 10시간 42분 동안 매 tick 재시도했다.
  - 진짜 원인은 5,000원 미만 먼지 잔량(0.5 AVAX ≈ 4,600원)이라 **영원히 팔 수 없는 주문**을
    무한 반복한 것이었다. pyupbit 가 typed exception 을 삼켜 원인이 로그에 남지 않았다.
  - 그 사이 재배포가 일어나 오펀이 **rsi2 슬롯으로 복구**되면서 easy_teaching 의 손실
    −375원이 rsi2 장부에 기록됐다(전략별 통계 오염).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from config import charter as C
from bot.position import Position, ExitReason
from data.upbit_client import OrderResult, UpbitClient
from tests.test_multi_position import bare_trader, flat_df


def _pos(strategy="rsi2_15m", volume=3.0, price=10_000.0):
    return Position(strategy=strategy, market="KRW-BTC", entry_price=price,
                    size_krw=price * volume, volume=volume,
                    entry_time=pd.Timestamp("2026-07-01"), entry_atr=100.0)


# ── ① 원인이 로그에 남는가 ───────────────────────────────────
def test_주문실패_원인이_None으로_뭉개지지_않는다():
    """
    pyupbit 의 sell_market_order 는 모든 예외를 삼키고 None 을 돌려준다. 그래서
    `_place_order` 로 직접 POST 한다 — 예외 타입과 메시지가 reason 에 남아야 한다.
    """
    c = UpbitClient.__new__(UpbitClient)

    class Upbit:
        def _request_headers(self, data):
            raise RuntimeError("too many requests (429)")
    c._upbit = Upbit()

    res = c._place_order({"market": "KRW-BTC", "side": "ask", "ord_type": "market"})
    assert res.ok is False
    assert "RuntimeError" in res.error and "429" in res.error
    assert "None" not in res.error


# ── ② 먼지를 만들지 않는가 ───────────────────────────────────
def test_잔량이_최소주문금액_미만이_되는_부분청산은_전량으로_바꾼다():
    """
    5,000원 밑으로 남기면 그 잔량은 업비트에서 영구히 팔 수 없다.
    남길 바에는 전량 판다 — 실매매 사고의 직접 원인이었다.
    """
    from bot.order_manager import OrderManager

    om = OrderManager.__new__(OrderManager)
    om.client = None
    pos = _pos(volume=0.6, price=10_000.0)      # 6,000원 — 절반이면 3,000원씩
    res = om.exit_partial(pos, 10_000.0, 0.3)
    assert res.ok is False and "min order" in res.error


def test_부분체결_후_남은_먼지는_청산완료로_처리한다():
    """이미 먼지가 생겼다면 무한 재시도하지 말고 종료 처리한다(로그 스팸 방지)."""
    t = bare_trader(("rsi2_15m",))
    pos = _pos(volume=3.0)
    t.positions[pos.key] = pos
    t.risk.s.open_positions = 1

    class Orders:
        def exit_position(self, p, price, reason):
            # 3.0 중 2.9997 만 체결 → 잔량 0.0003 × 10,000 = 3원 (먼지)
            return OrderResult(ok=True, filled_volume=2.9997, avg_price=price)
    t.orders = Orders()

    t._close(pos, 10_000.0, ExitReason.DEAD_POSITION)
    assert pos.key not in t.positions, "먼지 잔량으로 포지션을 붙들고 있으면 안 된다"


# ── ③ 무한 재시도를 하지 않는가 ──────────────────────────────
class _Clock:
    """가짜 단조시계 — 백오프가 '시간이 흘러야' 재시도하는지 보려면 시간을 제어해야 한다."""
    def __init__(self): self.now = 1000.0
    def __call__(self): return self.now
    def advance(self, sec): self.now += sec


def _failing_trader(error="unexpected response: None", monkeypatch=None):
    from bot import trader as trader_mod
    t = bare_trader(("rsi2_15m",))
    pos = _pos()
    t.positions[pos.key] = pos
    t.risk.s.open_positions = 1
    calls = []
    clock = _Clock()
    monkeypatch.setattr(trader_mod.time, "monotonic", clock)

    class Orders:
        def exit_position(self, p, price, reason):
            calls.append(reason)
            return OrderResult(ok=False, error=error)
    t.orders = Orders()
    return t, pos, calls, clock


def _spin(t, pos, clock, ticks=200, tick_sec=13.0):
    """실매매와 같은 조건: 13초 tick 으로 약 43분(=200틱) 돌린다."""
    for _ in range(ticks):
        t._close(pos, 10_000.0, ExitReason.DEAD_POSITION)
        clock.advance(tick_sec)


def test_청산_연속실패시_매_tick_재시도하지_않는다(monkeypatch):
    t, pos, calls, clock = _failing_trader(monkeypatch=monkeypatch)
    _spin(t, pos, clock)
    assert len(calls) < 20, f"200틱에 {len(calls)}회 재시도 — 실매매는 2,854건이었다"
    assert len(calls) >= 3, "시간이 지나면 다시 시도는 해야 한다(오펀 방치 금지)"


def test_백오프는_지수적으로_늘어난다():
    assert C.exit_retry_delay_sec(1) == C.EXIT_RETRY_BASE_SEC
    assert C.exit_retry_delay_sec(2) == C.EXIT_RETRY_BASE_SEC * 2
    assert C.exit_retry_delay_sec(3) == C.EXIT_RETRY_BASE_SEC * 4
    assert C.exit_retry_delay_sec(99) == C.EXIT_RETRY_MAX_SEC       # 상한
    assert C.exit_retry_delay_sec(0) == 0.0


def test_청산_실패로그도_매번_남기지_않는다(monkeypatch):
    t, pos, calls, clock = _failing_trader(monkeypatch=monkeypatch)
    _spin(t, pos, clock)
    fails = [e for e in t.logger.events if e[0] == "exit_fail"]
    assert len(fails) <= C.EXIT_FAIL_ALERT_AFTER, f"실패 로그 {len(fails)}건 — DB를 덮는다"


def test_청산_실패가_임계치를_넘으면_한_번_경보하고_포지션은_유지한다(monkeypatch):
    sent = []
    t, pos, calls, clock = _failing_trader(monkeypatch=monkeypatch)
    t.notifier = type("N", (), {"send": lambda self, m: sent.append(m)})()
    _spin(t, pos, clock)

    assert pos.key in t.positions, "팔지 못한 포지션을 장부에서 지우면 오펀이 된다"
    assert pos.exit_failures >= C.EXIT_FAIL_ALERT_AFTER
    alerts = [m for m in sent if "수동 확인 필요" in m]
    assert len(alerts) == 1, f"경보가 {len(alerts)}건 — 한 번만 보내야 한다"


def test_청산에_성공하면_실패카운터가_초기화된다(monkeypatch):
    t, pos, calls, clock = _failing_trader(monkeypatch=monkeypatch)
    _spin(t, pos, clock, ticks=20)
    assert pos.exit_failures > 0

    class Ok:
        def exit_position(self, p, price, reason):
            return OrderResult(ok=True, filled_volume=p.volume, avg_price=price)
    t.orders = Ok()
    clock.advance(C.EXIT_RETRY_MAX_SEC)        # 백오프가 끝날 만큼 시간이 흐른다
    t._close(pos, 10_000.0, ExitReason.DEAD_POSITION)
    assert pos.key not in t.positions


# ── ④ 오펀 복구가 원래 전략으로 귀속되는가 ───────────────────
def test_오펀은_원래_진입한_전략으로_복구된다(tmp_path, monkeypatch):
    """
    실매매에서 easy_teaching 이 산 AVAX/XRP 가 재배포 후 rsi2 슬롯으로 복구되어
    −375원의 손실이 rsi2 장부에 기록됐다. 전략별 성과 비교가 통째로 무의미해진다.
    """
    import sqlite3
    from bot import trader as trader_mod

    db = tmp_path / "trades.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,"
                " event TEXT, strategy TEXT, market TEXT, price REAL, volume REAL,"
                " size_krw REAL, pnl_krw REAL, reason TEXT)")
    con.execute("INSERT INTO trades (ts,event,strategy,market) VALUES"
                " ('2026-07-31T14:31:03+09:00','entry','easy_teaching','KRW-AVAX')")
    con.commit()
    con.close()
    monkeypatch.setattr(trader_mod, "settings",
                        type("S", (), {"db_path": str(db)})())

    assert trader_mod.last_entry_strategy("KRW-AVAX") == "easy_teaching"
    assert trader_mod.last_entry_strategy("KRW-DOGE") is None

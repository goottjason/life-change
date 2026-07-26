"""
BotService — 봇 루프를 백그라운드 스레드로 돌리고, 대시보드가 읽을 상태·통계를 제공한다.
한 컨테이너 안에서 FastAPI(관찰) + 봇(매매)이 함께 산다.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import traceback

from config.settings import settings
from config.timeutil import now_kst_iso
from bot.trader import Trader
from bot.position import ExitReason


class BotService:
    def __init__(self):
        self.trader = Trader()
        self._thread: threading.Thread | None = None
        self._running = False
        self.started_at: str | None = None
        self.last_tick_at: str | None = None
        self.last_error: str | None = None
        self.tick_count = 0

    # ── 라이프사이클 ─────────────────────────────────────────
    def start(self) -> None:
        if self._running:
            return
        self.trader.boot()
        self._running = True
        self.started_at = _now()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="bot-loop")
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self.trader.tick()
                self.last_tick_at = _now()
                self.tick_count += 1
                self.last_error = None
            except Exception:
                self.last_error = traceback.format_exc().splitlines()[-1]
                self.trader.notifier.send(f"⚠️ tick 예외: {self.last_error}")
            time.sleep(settings.loop_interval_sec)

    # ── 대시보드 데이터 ──────────────────────────────────────
    def status(self) -> dict:
        snap = self.trader.snapshot()
        snap["bot"] = {
            "running": self._running,
            "dry_run": settings.dry_run,
            "mode": "DRY_RUN(모의)" if settings.dry_run else "LIVE(실전)",
            "started_at": self.started_at,
            "last_tick_at": self.last_tick_at,
            "tick_count": self.tick_count,
            "last_error": self.last_error,
            "universe": self.trader.screener.eligible(),
            "interval_sec": settings.loop_interval_sec,
        }
        return snap

    def trades(self, limit: int = 50) -> list[dict]:
        try:
            with sqlite3.connect(settings.db_path) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute(
                    "SELECT ts,event,strategy,market,price,volume,size_krw,pnl_krw,reason"
                    " FROM trades ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []  # 아직 거래 없음

    def stats(self) -> list[dict]:
        """전략별 집계 (헌장 §10.3 주간 리뷰 근거)."""
        try:
            with sqlite3.connect(settings.db_path) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute("""
                    SELECT strategy,
                           COUNT(*) AS trades,
                           SUM(CASE WHEN pnl_krw > 0 THEN 1 ELSE 0 END) AS wins,
                           ROUND(SUM(pnl_krw)) AS total_pnl
                    FROM trades WHERE event='exit' GROUP BY strategy
                """).fetchall()
        except sqlite3.OperationalError:
            return []
        out = []
        for r in rows:
            trades = r["trades"] or 0
            wins = r["wins"] or 0
            out.append({
                "strategy": r["strategy"],
                "trades": trades,
                "wins": wins,
                "win_rate": round(wins / trades * 100, 1) if trades else 0.0,
                "total_pnl": r["total_pnl"] or 0,
            })
        return out

    def round_trips(self, limit: int = 50) -> list[dict]:
        """
        체결 내역(진입↔청산 묶음). 전략당 1포지션 원칙을 이용해 시간순으로 진입-청산을 짝짓는다.
        각 행: 진입가/청산가/보유시간/손익/신호이유. (Task B)
        """
        try:
            with sqlite3.connect(settings.db_path) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute(
                    "SELECT ts,event,strategy,market,price,volume,size_krw,pnl_krw,reason"
                    " FROM trades WHERE event IN ('entry','exit') ORDER BY id ASC"
                ).fetchall()
        except sqlite3.OperationalError:
            return []
        return pair_round_trips([dict(r) for r in rows])[-limit:][::-1]

    # ── 킬 스위치 (헌장 §9.1) ────────────────────────────────
    def kill(self) -> dict:
        positions = list(self.trader.positions.values())
        self.trader.failsafe.kill_switch(positions, get_price=self.trader.client.get_price)
        self.trader.risk.s.halted = True
        self.trader.risk.s.halt_reason = "수동 킬 스위치 (대시보드)"
        return {"killed": len(positions), "halted": True}


def _now() -> str:
    return now_kst_iso()


def _hold_seconds(entry_ts: str, exit_ts: str) -> float | None:
    """두 ISO 타임스탬프의 간격(초). 파싱 실패 시 None."""
    from datetime import datetime
    try:
        return (datetime.fromisoformat(exit_ts) - datetime.fromisoformat(entry_ts)).total_seconds()
    except (ValueError, TypeError):
        return None


def pair_round_trips(rows: list[dict]) -> list[dict]:
    """
    id 오름차순 이벤트 목록을 받아 (entry, exit) 쌍을 만든다.
    전략당 동시 1포지션이므로 전략별로 진입을 열어두고 다음 청산에 매칭한다.
    exit 만 있고 대응 entry 가 없으면(과거 데이터 경계) 진입 정보는 빈 값으로 둔다.
    """
    open_by_strat: dict[str, dict] = {}
    trips: list[dict] = []
    for r in rows:
        strat = r.get("strategy") or ""
        if r["event"] == "entry":
            open_by_strat[strat] = r
        elif r["event"] == "exit":
            e = open_by_strat.pop(strat, None)
            trips.append({
                "strategy": strat,
                "market": r.get("market") or (e.get("market") if e else ""),
                "entry_ts": e["ts"] if e else None,
                "exit_ts": r["ts"],
                "entry_price": e["price"] if e else None,
                "exit_price": r["price"],
                "size_krw": e["size_krw"] if e else r.get("size_krw"),
                "pnl_krw": r.get("pnl_krw"),
                "hold_sec": _hold_seconds(e["ts"], r["ts"]) if e else None,
                "entry_reason": e.get("reason") if e else None,
                "exit_reason": r.get("reason"),
            })
    return trips

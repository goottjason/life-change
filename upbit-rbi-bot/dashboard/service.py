"""
BotService — 봇 루프를 백그라운드 스레드로 돌리고, 대시보드가 읽을 상태·통계를 제공한다.
한 컨테이너 안에서 FastAPI(관찰) + 봇(매매)이 함께 산다.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import traceback

from config import charter as C
from config.charter import STRATEGY_SPECS
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
            "charter_version": C.CHARTER_VERSION,
            "active_strategies": list(C.ACTIVE_STRATEGIES),
            "started_at": self.started_at,
            "last_tick_at": self.last_tick_at,
            "tick_count": self.tick_count,
            "last_error": self.last_error,
            "universe": self.trader.screener.eligible(),
            "interval_sec": settings.loop_interval_sec,
        }
        return snap

    # ── 전략 세대 분리 (v1.5) ────────────────────────────────
    # 헌장 v1.3에서 전략을 macd/rsi/cvd → rsi2 계열로 **완전히 교체**했다. 과거 전략의 거래
    # 기록을 현재 성과와 섞으면 승률·손익이 무의미해지므로, 기본 조회는 **현재 가동 전략만**
    # 본다. 과거 기록은 지우지 않고 scope="all" 로 볼 수 있게 남긴다(§10.5 기록 보존).
    @staticmethod
    def _scope_clause(scope: str) -> tuple[str, tuple]:
        if scope == "all":
            return "", ()
        names = tuple(C.ACTIVE_STRATEGIES)
        holders = ",".join("?" * len(names))
        return f" AND strategy IN ({holders})", names

    def trades(self, limit: int = 50, scope: str = "current",
               events: str = "meaningful") -> list[dict]:
        """
        events="meaningful": entry/exit/exit_fail/recover 만. 기본값으로 둔 이유는
        과거 entry_fail 로그가 1,100건 넘게 쌓여 있어(잔고부족 스팸) 목록을 덮기 때문이다.
        """
        where = "WHERE 1=1"
        params: list = []
        if events == "meaningful":
            where += " AND event IN ('entry','exit','exit_partial','exit_fail','recover')"
        clause, sp = self._scope_clause(scope)
        where += clause
        params += list(sp)
        try:
            with sqlite3.connect(settings.db_path) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute(
                    "SELECT ts,event,strategy,market,price,volume,size_krw,pnl_krw,reason"
                    f" FROM trades {where} ORDER BY id DESC LIMIT ?", (*params, limit)
                ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []  # 아직 거래 없음

    def stats(self, scope: str = "current") -> list[dict]:
        """전략별 집계 (헌장 §10.3 주간 리뷰 근거). 손익비·PF까지 계산해 §11 기준과 대조 가능하게."""
        clause, sp = self._scope_clause(scope)
        try:
            with sqlite3.connect(settings.db_path) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute(f"""
                    SELECT strategy,
                           COUNT(*) AS trades,
                           SUM(CASE WHEN pnl_krw > 0 THEN 1 ELSE 0 END) AS wins,
                           ROUND(SUM(pnl_krw)) AS total_pnl,
                           ROUND(SUM(CASE WHEN pnl_krw > 0 THEN pnl_krw ELSE 0 END)) AS gross_win,
                           ROUND(-SUM(CASE WHEN pnl_krw <= 0 THEN pnl_krw ELSE 0 END)) AS gross_loss
                    FROM trades WHERE event='exit'{clause} GROUP BY strategy
                """, sp).fetchall()
        except sqlite3.OperationalError:
            return []
        out = []
        for r in rows:
            trades = r["trades"] or 0
            wins = r["wins"] or 0
            gw, gl = r["gross_win"] or 0, r["gross_loss"] or 0
            spec = STRATEGY_SPECS.get(r["strategy"])
            out.append({
                "strategy": r["strategy"],
                "timeframe": spec.timeframe if spec else "",
                "active": r["strategy"] in C.ACTIVE_STRATEGIES,
                "trades": trades,
                "wins": wins,
                "win_rate": round(wins / trades * 100, 1) if trades else 0.0,
                "profit_factor": round(gw / gl, 2) if gl else None,
                "total_pnl": r["total_pnl"] or 0,
                "avg_pnl": round((r["total_pnl"] or 0) / trades, 1) if trades else 0.0,
            })
        return out

    def expectations(self) -> dict:
        """
        백테스트 기대치 — 실전 성과를 여기에 대조해 괴리를 본다 (§10.4).
        수치 출처: backtesting/research/README.md (2년·홀드아웃 17개월·실측 스프레드 반영).

        ⚠ v2.3에서 15분봉의 진입선(3→7)과 게이트(1.0%→0.83%)를 바꿨다. 아래 15분봉·병행 수치는
        **개정 전 파라미터**의 홀드아웃 측정치다 — 개정 근거는 선택구간(209일·보수적 스프레드)에서
        59거래 승률 81.4% 거래당 +0.2585% 이지만, 구간과 비용 가정이 달라 이 표에 섞어 넣으면
        대조 기준이 무의미해진다. 홀드아웃 재측정 전까지는 '옛 파라미터 기준'임을 표시만 한다.
        """
        return {
            "rsi2": {"win_rate": 69.1, "profit_factor": 1.49, "exp_pct": 0.180,
                     "trades_per_day": 0.78},
            "rsi2_15m": {"win_rate": 73.9, "profit_factor": 1.96, "exp_pct": 0.489,
                         "trades_per_day": 0.26,
                         "note": "v2.3 개정 전(진입선 3·게이트 1.0%) 기준 — 재측정 전"},
            "combined": {"win_rate": 70.3, "profit_factor": 1.64, "exp_pct": 0.259,
                         "trades_per_day": 1.21, "account_2y_pct": 51.4, "mdd_pct": 6.2,
                         "note": "15분봉이 v2.3 개정 전 기준이라 병행 합계도 재측정 전"},
        }

    def round_trips(self, limit: int = 50, scope: str = "current") -> list[dict]:
        """
        체결 내역(진입↔청산 묶음). 전략당 1포지션 원칙을 이용해 시간순으로 진입-청산을 짝짓는다.
        각 행: 진입가/청산가/보유시간/손익/신호이유. (Task B)
        """
        clause, sp = self._scope_clause(scope)
        try:
            with sqlite3.connect(settings.db_path) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute(
                    "SELECT ts,event,strategy,market,price,volume,size_krw,pnl_krw,reason"
                    f" FROM trades WHERE event IN ('entry','exit'){clause} ORDER BY id ASC", sp
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

    v1.4부터 한 전략이 서로 다른 코인에 동시 진입할 수 있으므로 **'전략:코인'** 으로 짝짓는다.
    (전략명만으로 짝지으면 두 코인을 동시 보유할 때 진입가·손익이 뒤섞인다.)
    exit 만 있고 대응 entry 가 없으면(과거 데이터 경계) 진입 정보는 빈 값으로 둔다.
    """
    open_by_key: dict[str, dict] = {}
    trips: list[dict] = []
    for r in rows:
        strat = r.get("strategy") or ""
        key = f"{strat}:{r.get('market') or ''}"
        if r["event"] == "entry":
            open_by_key[key] = r
        elif r["event"] == "exit":
            e = open_by_key.pop(key, None)
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

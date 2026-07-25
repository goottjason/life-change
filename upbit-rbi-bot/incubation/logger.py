"""
거래 로깅 (헌장 §10.1). 모든 진입/청산/에러를 SQLite에 저장 → 주간 리뷰(§10.3) 근거.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from config.settings import settings


class TradeLogger:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.db_path
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT, event TEXT, strategy TEXT, market TEXT,
                    price REAL, volume REAL, size_krw REAL,
                    pnl_krw REAL, reason TEXT
                )
            """)

    def log(self, event: str, *, strategy: str = "", market: str = "",
            price: float = 0.0, volume: float = 0.0, size_krw: float = 0.0,
            pnl_krw: float = 0.0, reason: str = "") -> None:
        ts = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                "INSERT INTO trades (ts,event,strategy,market,price,volume,size_krw,pnl_krw,reason)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, event, strategy, market, price, volume, size_krw, pnl_krw, reason),
            )
        print(f"[{ts}] {event} {strategy} {market} {reason} pnl={pnl_krw:.0f}")

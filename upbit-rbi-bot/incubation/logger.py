"""
거래 로깅 (헌장 §10.1). 모든 진입/청산/에러를 SQLite에 저장 → 주간 리뷰(§10.3) 근거.
"""
from __future__ import annotations

import os
import sqlite3

from config.settings import settings
from config.timeutil import now_kst_iso


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
            # fill_price (v2.9): `price` 는 **신호가**(판단에 쓴 종가)이고 실제 체결가는 따로다.
            # 둘의 차이 = 실효 슬리피지인데, 거래당 기댓값이 +0.176% 뿐이라 이 값이 0.25%를
            # 넘으면 엣지가 통째로 사라진다(§10 관찰 필수 항목). 그래서 별도 컬럼에 남긴다.
            # 기존 DB 에도 안전하게 붙도록 매번 존재 여부를 확인한다.
            cols = {r[1] for r in con.execute("PRAGMA table_info(trades)")}
            if "fill_price" not in cols:
                con.execute("ALTER TABLE trades ADD COLUMN fill_price REAL")

    def log(self, event: str, *, strategy: str = "", market: str = "",
            price: float = 0.0, volume: float = 0.0, size_krw: float = 0.0,
            pnl_krw: float = 0.0, reason: str = "", fill_price: float = 0.0) -> None:
        ts = now_kst_iso()  # KST(Asia/Seoul) 표기 — 사용자 혼란 방지
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                "INSERT INTO trades (ts,event,strategy,market,price,volume,size_krw,"
                "pnl_krw,reason,fill_price) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, event, strategy, market, price, volume, size_krw, pnl_krw, reason,
                 fill_price or None),
            )
        print(f"[{ts}] {event} {strategy} {market} {reason} pnl={pnl_krw:.0f}")

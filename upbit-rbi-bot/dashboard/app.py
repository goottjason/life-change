"""
대시보드 웹앱 (FastAPI). 모든 라우트는 nginx 경로 규칙에 맞춰 '/life-change' 프리픽스 아래에 둔다.
  GET  /life-change/            → 대시보드 HTML
  GET  /life-change/health      → CI 헬스체크
  GET  /life-change/api/status  → 봇 실시간 상태(JSON)
  GET  /life-change/api/trades  → 최근 거래
  GET  /life-change/api/stats   → 전략별 집계
  POST /life-change/api/kill    → 킬 스위치 (DASHBOARD_TOKEN 필요)
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse

from dashboard.service import BotService

PREFIX = "/life-change"
STATIC = Path(__file__).parent / "static"
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")

app = FastAPI(title="life-change dashboard", docs_url=None, redoc_url=None)
service = BotService()


@app.on_event("startup")
def _startup() -> None:
    service.start()


@app.get(PREFIX, response_class=HTMLResponse)
@app.get(PREFIX + "/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get(PREFIX + "/health", response_class=PlainTextResponse)
def health() -> str:
    return "ok"


@app.get(PREFIX + "/api/status")
def api_status() -> dict:
    return service.status()


@app.get(PREFIX + "/api/trades")
def api_trades(limit: int = 50) -> list[dict]:
    return service.trades(min(limit, 200))


@app.get(PREFIX + "/api/stats")
def api_stats() -> list[dict]:
    return service.stats()


@app.get(PREFIX + "/api/roundtrips")
def api_roundtrips(limit: int = 50) -> list[dict]:
    return service.round_trips(min(limit, 200))


@app.post(PREFIX + "/api/kill")
def api_kill(x_token: str = Header(default="")) -> dict:
    if not DASHBOARD_TOKEN or x_token != DASHBOARD_TOKEN:
        raise HTTPException(status_code=403, detail="invalid token")
    return service.kill()

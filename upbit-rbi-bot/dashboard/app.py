"""
대시보드 웹앱 (FastAPI). 모든 라우트는 nginx 경로 규칙에 맞춰 '/life-change' 프리픽스 아래에 둔다.
  GET  /life-change/            → 대시보드 HTML
  GET  /life-change/health      → CI 헬스체크
  GET  /life-change/api/status  → 봇 실시간 상태(JSON)
  GET  /life-change/api/trades  → 최근 거래 (scope=current|all)
  GET  /life-change/api/stats   → 전략별 집계 (scope=current|all)
  GET  /life-change/api/expectations → 백테스트 기대치(실전 대조용)
  POST /life-change/api/kill    → 킬 스위치 (DASHBOARD_TOKEN 필요)
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from dashboard.service import BotService

class ToggleRequest(BaseModel):
    name: str
    enable: bool

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
def api_trades(limit: int = 50, scope: str = "current", events: str = "meaningful") -> list[dict]:
    """scope=current(기본, 현재 가동 전략만) | all(과거 전략 포함)"""
    return service.trades(min(limit, 200), scope=scope, events=events)


@app.get(PREFIX + "/api/stats")
def api_stats(scope: str = "current") -> list[dict]:
    return service.stats(scope=scope)


@app.get(PREFIX + "/api/expectations")
def api_expectations() -> dict:
    """백테스트 기대치 — 실전 성과 대조용 (§10.4)"""
    return service.expectations()


@app.get(PREFIX + "/api/incubation")
def api_incubation() -> dict:
    """
    인큐베이션 진행 리포트 (§11-3) — 백테스트가 실전에서 재현되는지.
    rsi2 계열은 §11을 통과한 유일한 전략이지만 실전 표본이 아직 한 자릿수다.
    100거래까지의 진행률·실전 vs 백테스트 대조·실효 슬리피지를 한 번에 본다.
    """
    from incubation.progress import report, format_text
    rep = report()
    rep["text"] = format_text(rep)
    return rep


@app.get(PREFIX + "/api/roundtrips")
def api_roundtrips(limit: int = 50, scope: str = "current") -> list[dict]:
    return service.round_trips(min(limit, 200), scope=scope)


@app.post(PREFIX + "/api/kill")
def api_kill(x_token: str = Header(default="")) -> dict:
    if not DASHBOARD_TOKEN or x_token != DASHBOARD_TOKEN:
        raise HTTPException(status_code=403, detail="invalid token")
    return service.kill()

@app.post(PREFIX + "/api/strategy/toggle")
def api_toggle_strategy(req: ToggleRequest, x_token: str = Header(default="")) -> dict:
    if not DASHBOARD_TOKEN or x_token != DASHBOARD_TOKEN:
        raise HTTPException(status_code=403, detail="invalid token")
    return service.toggle_strategy(req.name, req.enable)

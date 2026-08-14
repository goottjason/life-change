"""
실험 트랙(breakout) 주간 코호트 리포트 테스트 (v4.0, 스펙 §5).
"어떤 조건의 돌파가 손실인가"를 축별로 쪼개는 집계가 맞는지 본다.
"""
from __future__ import annotations

import json

from incubation.logger import TradeLogger
from incubation import cohort


def _seed(db):
    """breakout 왕복 4건: 거래량확인·추세·시간대가 서로 다른 케이스."""
    lg = TradeLogger(db)
    cases = [
        ({"vol_ratio": 2.0, "trend_up": True,  "breakout_pct": 0.3, "fingerprint": "v4.0-aaaa"}, +80.0),
        ({"vol_ratio": 2.5, "trend_up": False, "breakout_pct": 0.1, "fingerprint": "v4.0-aaaa"}, -60.0),
        ({"vol_ratio": 1.6, "trend_up": True,  "breakout_pct": 0.5, "fingerprint": "v4.0-aaaa"}, -30.0),
        ({"vol_ratio": 3.0, "trend_up": None,  "breakout_pct": 0.2, "fingerprint": "v4.0-aaaa"}, +40.0),
    ]
    for i, (ctx, pnl) in enumerate(cases):
        m = f"KRW-C{i}"
        lg.log("entry", strategy="breakout", market=m, price=100.0, size_krw=10_000,
               context=json.dumps(ctx))
        lg.log("exit", strategy="breakout", market=m, price=100.0, pnl_krw=pnl)


def test_cohort_report_axes(tmp_path):
    db = str(tmp_path / "t.sqlite")
    _seed(db)
    rep = cohort.report(db)
    assert rep["n"] == 4
    axes = rep["axes"]
    trend = {b["bucket"]: b for b in axes["추세(1시간봉)"]}
    assert trend["위"]["n"] == 2 and trend["아래"]["n"] == 1 and trend["판정불가"]["n"] == 1
    fp = {b["bucket"]: b for b in axes["설정(지문)"]}
    assert fp["v4.0-aaaa"]["n"] == 4
    vol = {b["bucket"]: b for b in axes["거래량비"]}
    assert vol["2배+"]["n"] == 3 and vol["1.5~2배"]["n"] == 1
    # 30건 미만이면 판정 문구 대신 '표본 부족' 안내 (기존 원칙 유지)
    assert "표본" in rep["text"]
    # 손익 집계가 맞는다
    assert rep["overall"]["pnl_krw"] == 30


def test_cohort_excludes_validated_track(tmp_path):
    db = str(tmp_path / "t.sqlite")
    _seed(db)
    lg = TradeLogger(db)
    lg.log("entry", strategy="rsi2", market="KRW-ETH", price=100.0, size_krw=60_000)
    lg.log("exit", strategy="rsi2", market="KRW-ETH", price=101.0, pnl_krw=500.0)
    assert cohort.report(db)["n"] == 4


def test_cohort_survives_missing_context(tmp_path):
    """context 없는 옛 거래·깨진 JSON도 리포트를 죽이지 않는다 — '기록없음' 버킷으로 간다."""
    db = str(tmp_path / "t.sqlite")
    lg = TradeLogger(db)
    lg.log("entry", strategy="breakout", market="KRW-X", price=100.0, size_krw=10_000)
    lg.log("exit", strategy="breakout", market="KRW-X", price=99.0, pnl_krw=-100.0)
    rep = cohort.report(db)
    assert rep["n"] == 1
    assert {b["bucket"] for b in rep["axes"]["설정(지문)"]} == {"기록없음"}

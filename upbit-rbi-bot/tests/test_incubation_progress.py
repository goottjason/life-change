"""
인큐베이션 진행 리포트 (§11-3) — 실전 표본이 백테스트를 재현하는지 자동 대조.

이 프로젝트가 반복해서 저지른 실수는 **적은 표본으로 결론 내리기**다
(easy_teaching 을 16거래 실적으로 실계좌에 넣었다). 그래서 여기서는
표본이 부족하면 성적을 아예 판정하지 않는 것을 테스트로 못 박는다.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from incubation import progress as P

ALL = "2000-01-01T00:00:00+09:00"   # 기준일 필터를 끄고 전부 세게 하는 값


def make_db(tmp_path, rows) -> str:
    """rows: (event, strategy, market, price, size_krw, pnl_krw, reason, fill_price)"""
    db = tmp_path / "t.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,"
                " event TEXT, strategy TEXT, market TEXT, price REAL, volume REAL,"
                " size_krw REAL, pnl_krw REAL, reason TEXT, fill_price REAL)")
    for i, (ev, st, mk, px, size, pnl, reason, fill) in enumerate(rows):
        con.execute("INSERT INTO trades (ts,event,strategy,market,price,volume,size_krw,"
                    "pnl_krw,reason,fill_price) VALUES (?,?,?,?,?,0,?,?,?,?)",
                    (f"2026-08-{i + 1:02d}T10:00:00+09:00", ev, st, mk, px, size, pnl,
                     reason, fill))
    con.commit(); con.close()
    return str(db)


def pair(strategy="rsi2", market="KRW-BTC", size=30_000.0, pnl=53.0,
         signal=100.0, fill=100.0):
    return [("entry", strategy, market, signal, size, 0.0, "진입", fill),
            ("exit", strategy, market, signal, 0.0, pnl, "reverse", signal)]


# ── 표본 부족일 때 결론 내리지 않는다 ────────────────────────
def test_거래가_없으면_안내만_한다(tmp_path):
    rep = P.report(make_db(tmp_path, []), since=ALL)
    assert rep["overall"]["n"] == 0
    assert "아직 청산된 거래가 없습니다" in P.format_text(rep)


def test_표본이_부족하면_성적을_판정하지_않는다(tmp_path):
    """5건으로 승률 100% 가 나와도 '순항'이라고 말하면 안 된다."""
    rows = []
    for _ in range(5):
        rows += pair(pnl=500.0)
    rep = P.report(make_db(tmp_path, rows), since=ALL)
    assert rep["overall"]["winrate"] == 100.0
    assert "관찰 중" in rep["verdict"]
    assert any("판정하지 않는다" in n for n in rep["notes"])


def test_진행률이_목표_100건_기준으로_표시된다(tmp_path):
    rows = []
    for _ in range(12):
        rows += pair()
    rep = P.report(make_db(tmp_path, rows), since=ALL)
    assert rep["target"] == 100
    assert "12/100" in P.format_text(rep)


# ── 충분한 표본에서의 판정 ───────────────────────────────────
def _many(n, pnl, **kw):
    rows = []
    for _ in range(n):
        rows += pair(pnl=pnl, **kw)
    return rows


def test_실전_기댓값이_음수면_중단_검토(tmp_path):
    rep = P.report(make_db(tmp_path, _many(40, -100.0)), since=ALL)
    assert "중단 검토" in rep["verdict"]


def test_기댓값이_백테스트_절반_미만이면_주의(tmp_path):
    # 백테스트 +0.180% → 30,000원에 +54원. 그 1/4 수준인 +13원.
    rep = P.report(make_db(tmp_path, _many(40, 13.0)), since=ALL)
    assert "주의" in rep["verdict"]
    assert any("절반 미만" in n for n in rep["notes"])


def test_백테스트를_재현하면_순항(tmp_path):
    rows = []
    for i in range(40):                       # 승률 70%, 거래당 평균 ≈ +0.18%
        rows += pair(pnl=110.0 if i % 10 < 7 else -80.0)
    rep = P.report(make_db(tmp_path, rows), since=ALL)
    assert "순항" in rep["verdict"], rep["notes"]


# ── ★ 슬리피지 감시 (엣지를 죽이는 1순위) ────────────────────
def test_슬리피지를_신호가와_체결가_차이로_계산한다(tmp_path):
    rows = _many(5, 50.0, signal=100.0, fill=100.3)      # +0.3%
    rep = P.report(make_db(tmp_path, rows), since=ALL)
    assert rep["overall"]["slippage"] == pytest.approx(0.3, abs=1e-6)


def test_슬리피지가_경보선을_넘으면_표본이_적어도_경고한다(tmp_path):
    """비용은 표본과 무관하게 즉시 판단할 수 있다 — 신호 성적과 달리 운이 아니다."""
    rep = P.report(make_db(tmp_path, _many(5, 50.0, signal=100.0, fill=100.4)), since=ALL)
    alerts = [n for n in rep["notes"] if "슬리피지" in n and "🚨" in n]
    assert alerts, rep["notes"]
    # 숫자만 던지지 말고 '왜 위험한지'까지 말해야 운영자가 판단할 수 있다
    assert "기댓값" in alerts[0] and "진다" in alerts[0]


def test_슬리피지_기록이_없으면_없다고_말한다(tmp_path):
    """v2.9 이전 거래는 체결가가 없다 — 0으로 표시해 안심시키면 안 된다."""
    rows = _many(3, 50.0, signal=100.0, fill=0.0)
    rep = P.report(make_db(tmp_path, rows), since=ALL)
    assert rep["overall"]["slippage"] is None
    assert "기록 없음" in P.format_text(rep)


# ── 전략별 분리 ──────────────────────────────────────────────
def test_전략별로_따로_집계한다(tmp_path):
    rows = _many(20, 100.0) + _many(20, -50.0, strategy="rsi2_15m", market="KRW-ETH")
    rep = P.report(make_db(tmp_path, rows), since=ALL)
    assert set(rep["per_strategy"]) == {"rsi2", "rsi2_15m"}
    assert rep["per_strategy"]["rsi2"]["n"] == 20
    assert rep["per_strategy"]["rsi2_15m"]["pnl_krw"] == pytest.approx(-1000.0)


def test_다른_전략의_거래는_섞이지_않는다(tmp_path):
    """easy_teaching 손실이 rsi2 장부에 섞여 통계가 오염된 사고가 실제로 있었다."""
    rows = _many(10, 100.0) + _many(10, -500.0, strategy="easy_teaching")
    rep = P.report(make_db(tmp_path, rows), since=ALL)
    assert rep["overall"]["n"] == 10
    assert rep["overall"]["pnl_krw"] == pytest.approx(1000.0)


# ── DB 스키마 하위호환 ───────────────────────────────────────
def test_fill_price_컬럼이_없는_구버전_DB도_읽는다(tmp_path):
    db = tmp_path / "old.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,"
                " event TEXT, strategy TEXT, market TEXT, price REAL, volume REAL,"
                " size_krw REAL, pnl_krw REAL, reason TEXT)")
    con.execute("INSERT INTO trades (ts,event,strategy,market,price,volume,size_krw,"
                "pnl_krw,reason) VALUES ('2026-08-01','entry','rsi2','KRW-BTC',100,0,30000,0,'')")
    con.execute("INSERT INTO trades (ts,event,strategy,market,price,volume,size_krw,"
                "pnl_krw,reason) VALUES ('2026-08-01','exit','rsi2','KRW-BTC',101,0,0,53,'reverse')")
    con.commit(); con.close()
    rep = P.report(str(db), since=ALL)
    assert rep["overall"]["n"] == 1
    assert rep["overall"]["slippage"] is None


def test_DB가_없어도_죽지_않는다(tmp_path):
    rep = P.report(str(tmp_path / "없는파일.sqlite"), since=ALL)
    assert rep["overall"]["n"] == 0


# ── 인큐베이션 시작 기준일 (§11-3) ───────────────────────────
def test_기준일_이전_거래는_표본에_넣지_않는다(tmp_path):
    """
    기준일 이전 기록은 조건이 다르다 — 변동성 게이트가 미검증 값(백테스트 음수 지점)이었고,
    오펀 복구가 아무 슬롯에나 붙어 다른 전략 손익이 rsi2 장부에 섞여 있었다.
    섞어서 세면 '무엇을 검증하는지' 자체가 흐려진다.
    """
    rows = _many(3, 100.0)                     # 2026-08-01 ~ 08-06 사이로 찍힌다
    db = make_db(tmp_path, rows)
    전체 = P.report(db, since=ALL)
    이후 = P.report(db, since="2026-08-05T00:00:00+09:00")
    assert 전체["overall"]["n"] == 3
    assert 이후["overall"]["n"] < 3
    assert 이후["excluded_prior"] == 3 - 이후["overall"]["n"]


def test_제외된_이전_거래가_있으면_이유를_설명한다(tmp_path):
    rep = P.report(make_db(tmp_path, _many(3, 100.0)),
                   since="2026-08-05T00:00:00+09:00")
    notes = " ".join(rep["notes"])
    assert "세지 않습니다" in notes and "진입한" in notes


def test_기본값은_헌장의_기준일을_쓴다(tmp_path):
    from config import charter as C
    rep = P.report(make_db(tmp_path, _many(2, 100.0)))
    assert rep["since"] == C.INCUBATION_START


def test_기준시각은_청산이_아니라_진입_시각으로_거른다(tmp_path):
    """
    ★ 실제로 났던 오류 — 옛 설정으로 사서 새 설정 배포 뒤에 팔린 거래가 표본에 섞였다
    (2026-08-04 AVAX: 진입 15:41 옛 게이트 0.4% → 청산 16:33 복구 배포 후).
    판정 대상은 '어떤 설정으로 샀는가'이지 '언제 팔렸는가'가 아니다.
    """
    db = tmp_path / "x.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,"
                " event TEXT, strategy TEXT, market TEXT, price REAL, volume REAL,"
                " size_krw REAL, pnl_krw REAL, reason TEXT, fill_price REAL)")
    ins = ("INSERT INTO trades (ts,event,strategy,market,price,volume,size_krw,pnl_krw,"
           "reason,fill_price) VALUES (?,?,?,?,100,0,?,?,'',0)")
    con.execute(ins, ("2026-08-04T15:41:00+09:00","entry","rsi2_15m","KRW-AVAX",30000,0))
    con.execute(ins, ("2026-08-04T16:33:00+09:00","exit","rsi2_15m","KRW-AVAX",0,-46))
    con.execute(ins, ("2026-08-04T17:00:00+09:00","entry","rsi2","KRW-BTC",30000,0))
    con.execute(ins, ("2026-08-04T18:00:00+09:00","exit","rsi2","KRW-BTC",0,50))
    con.commit(); con.close()

    rep = P.report(str(db), since="2026-08-04T16:15:00+09:00")
    assert rep["overall"]["n"] == 1, "옛 설정으로 산 거래가 표본에 섞였다"
    assert rep["per_strategy"]["rsi2"]["n"] == 1
    assert "rsi2_15m" not in rep["per_strategy"]
    assert rep["excluded_prior"] == 1


# ── v4.0: 진입 컨텍스트 컬럼 + 실험 트랙 격리 ────────────────────
def test_logger_context_column_roundtrip(tmp_path):
    """진입 컨텍스트 JSON이 저장·조회되고, 컬럼이 없던 기존 DB도 자동 마이그레이션된다."""
    import json, sqlite3
    from incubation.logger import TradeLogger
    db = str(tmp_path / "trades.sqlite")
    # 구버전 스키마(컨텍스트 없음)를 먼저 만들어 마이그레이션을 검증한다
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " ts TEXT, event TEXT, strategy TEXT, market TEXT, price REAL,"
                    " volume REAL, size_krw REAL, pnl_krw REAL, reason TEXT)")
    lg = TradeLogger(db)
    ctx = {"breakout_pct": 0.42, "vol_ratio": 2.1, "trend_up": False}
    lg.log("entry", strategy="breakout", market="KRW-BTC", price=100.0,
           context=json.dumps(ctx, ensure_ascii=False))
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT context FROM trades WHERE event='entry'").fetchone()
    assert json.loads(row[0])["vol_ratio"] == 2.1


def test_breakout_trades_excluded_from_incubation(tmp_path):
    """실험 트랙 거래는 인큐베이션 표본에 절대 섞이지 않는다 (스펙 §1)."""
    from incubation.logger import TradeLogger
    from incubation.progress import load_roundtrips, activity
    db = str(tmp_path / "trades.sqlite")
    lg = TradeLogger(db)
    lg.log("entry", strategy="breakout", market="KRW-BTC", price=100.0, size_krw=10_000)
    lg.log("exit", strategy="breakout", market="KRW-BTC", price=101.0, pnl_krw=90.0)
    lg.log("entry", strategy="rsi2", market="KRW-ETH", price=100.0, size_krw=60_000)
    lg.log("exit", strategy="rsi2", market="KRW-ETH", price=99.0, pnl_krw=-660.0)
    trips = load_roundtrips(db, since=None)
    assert {t.strategy for t in trips} == {"rsi2"}      # 기본 필터가 rsi2 계열만 본다
    act = activity(db)
    assert act["n7"] == 1                               # breakout 진입은 활동 집계에도 안 들어간다

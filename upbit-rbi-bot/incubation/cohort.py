"""
실험 트랙(breakout) 주간 코호트 리포트 (v4.0, 스펙 §5).

"어떤 조건의 돌파가 손실인가"를 실거래로 답한다. 진입 컨텍스트(trades.context JSON)를
축별로 쪼개 승률·거래당 손익을 집계한다 — 이것이 주간 튜닝 세션의 근거 자료다.
30건 미만 코호트는 판정하지 않는다(적은 표본의 승률은 운이다 — incubation.progress 와
같은 원칙. 이 프로젝트는 16거래를 보고 실계좌에 넣었다가 데인 적이 있다).
"""
from __future__ import annotations

import json
import sqlite3

from config.settings import settings

MIN_JUDGE = 30          # 이 미만이면 '표본 부족'만 말한다


def _load(db_path: str | None = None) -> list[dict]:
    """breakout 왕복 거래 + 진입 컨텍스트. [{pnl_krw, size_krw, pct, ctx, entry_ts, market}…]"""
    path = db_path or settings.db_path
    try:
        con = sqlite3.connect(path)
        cols = {r[1] for r in con.execute("PRAGMA table_info(trades)")}
        cctx = "context" if "context" in cols else "NULL"
        exits = con.execute(
            "SELECT id, market, pnl_krw FROM trades"
            " WHERE event='exit' AND strategy='breakout' ORDER BY id").fetchall()
        entries = con.execute(
            f"SELECT id, market, size_krw, ts, {cctx} FROM trades"
            " WHERE event='entry' AND strategy='breakout' ORDER BY id").fetchall()
    except sqlite3.Error:
        return []
    finally:
        try:
            con.close()
        except Exception:
            pass
    out = []
    for xid, market, pnl in exits:
        prior = [e for e in entries if e[1] == market and e[0] < xid]
        if not prior:
            continue
        _, _, size, ts, raw = prior[-1]
        try:
            ctx = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            ctx = {}
        out.append({"pnl_krw": pnl or 0.0, "size_krw": size or 0.0,
                    "pct": (pnl or 0.0) / size * 100 if size else 0.0,
                    "ctx": ctx, "entry_ts": ts or "", "market": market})
    return out


def _bucket_axes(t: dict) -> dict[str, str]:
    """한 거래 → 축별 버킷 이름. 새 축을 추가하려면 여기만 고치면 된다."""
    ctx = t["ctx"]
    tr = ctx.get("trend_up")
    vol = ctx.get("vol_ratio")
    hour = int(t["entry_ts"][11:13]) if len(t["entry_ts"]) >= 13 else None
    return {
        "추세(1시간봉)": "위" if tr is True else ("아래" if tr is False else "판정불가"),
        "거래량비": (("2배+" if vol >= 2.0 else "1.5~2배" if vol >= 1.5 else "1.5배 미만")
                     if isinstance(vol, (int, float)) else "기록없음"),
        "시간대(KST)": (f"{hour // 4 * 4:02d}~{hour // 4 * 4 + 4:02d}시"
                        if hour is not None else "기록없음"),
        "종목": t["market"],
        "설정(지문)": ctx.get("fingerprint") or "기록없음",
    }


def _stat(trades: list[dict]) -> dict:
    n = len(trades)
    wins = sum(1 for t in trades if t["pct"] > 0)
    return {"n": n, "winrate": round(wins / n * 100, 1) if n else 0.0,
            "exp_pct": round(sum(t["pct"] for t in trades) / n, 3) if n else 0.0,
            "pnl_krw": round(sum(t["pnl_krw"] for t in trades))}


def report(db_path: str | None = None) -> dict:
    trades = _load(db_path)
    grouped: dict[str, dict[str, list[dict]]] = {}
    for t in trades:
        for axis, bucket in _bucket_axes(t).items():
            grouped.setdefault(axis, {}).setdefault(bucket, []).append(t)
    axes_out = {axis: sorted(({"bucket": b, **_stat(ts)} for b, ts in buckets.items()),
                             key=lambda r: -r["n"])
                for axis, buckets in grouped.items()}
    rep = {"n": len(trades), "overall": _stat(trades), "axes": axes_out}
    rep["text"] = format_text(rep)
    return rep


def format_text(rep: dict) -> str:
    """텔레그램/콘솔용 평이한 한국어. 코호트 = '같은 조건끼리 묶은 거래 그룹'."""
    o = rep["overall"]
    lines = [f"🧪 breakout 실험 트랙 — 왕복 {rep['n']}건, "
             f"누적 {o['pnl_krw']:+,}원 (거래당 {o['exp_pct']:+.3f}%)"]
    if not rep["n"]:
        lines.append("아직 청산된 거래가 없습니다.")
        return "\n".join(lines)
    if rep["n"] < MIN_JUDGE:
        lines.append(f"표본 {rep['n']}건 — {MIN_JUDGE}건 미만이라 코호트 판정은 하지 않습니다. "
                     f"(적은 표본의 승률은 운입니다)")
    for axis, rows in rep["axes"].items():
        lines.append(f"\n[{axis}] — 이 조건별로 성적이 어떻게 갈리는지")
        for r in rows:
            mark = "" if r["n"] >= MIN_JUDGE else " (표본 부족)"
            lines.append(f"  {r['bucket']:<12} {r['n']:>3}건  승률 {r['winrate']:>5.1f}%  "
                         f"거래당 {r['exp_pct']:+.3f}%{mark}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report()["text"])

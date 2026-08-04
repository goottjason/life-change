"""
인큐베이션 진행 리포트 (헌장 §11-3, §10.3) — "백테스트가 실전에서 재현되는가".

## 왜 필요한가
rsi2 계열은 2년 검증에서 §11을 통과한 **유일한** 전략이지만, 실전 표본은 아직 한 자릿수다.
백테스트 성적(홀드아웃 17개월 493거래 승률 69.0% PF 1.50 거래당 +0.176%)이 실계좌에서
재현되는지는 **거래를 100건 쌓아 봐야** 안다. 이 모듈이 그 진행을 자동으로 집계한다.

## 무엇을 보는가 (운영자가 판단만 하면 되게)
1. **진행률** — 목표 100거래 중 몇 건인가
2. **실전 vs 백테스트** — 승률·PF·거래당 기댓값이 기대 범위 안인가
3. ★ **실효 슬리피지** — 신호가와 실제 체결가의 차이. 거래당 기댓값이 +0.176% 뿐이라
   왕복 0.25%를 넘으면 **엣지가 통째로 사라진다**. 엣지를 죽이는 1순위 요인이다.
4. **판정** — 계속/경고/중단 중 하나를 평이한 한국어로

## 통계에 대한 태도
표본이 적을 때 "승률 100%" 같은 숫자는 아무 의미가 없다. 그래서 거래 수가 임계에 못 미치면
성적을 판정하지 않고 **'표본 부족'이라고만 말한다**. 조기에 결론 내리는 것이 이 프로젝트가
반복해서 저지른 실수이므로(easy_teaching 16거래로 실계좌 투입) 여기서는 막는다.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

from config import charter as C
from config.settings import settings

# 검증 성적 (backtesting/research/README.md · 홀드아웃 17개월). 실전과 대조할 기준선.
BACKTEST = {
    "rsi2":     {"winrate": 69.1, "pf": 1.49, "exp_pct": 0.180, "per_day": 0.7},
    "rsi2_15m": {"winrate": 73.9, "pf": 1.96, "exp_pct": 0.489, "per_day": 0.28},
}
TARGET_TRADES = 100          # §11 표본 기준
MIN_JUDGE_TRADES = 30        # 이 미만이면 성적을 판정하지 않는다(표본 부족)
SLIPPAGE_ALERT = 0.25        # 왕복 실효 슬리피지 경보선(%) — 넘으면 엣지 소멸
EXP_FLOOR_RATIO = 0.0        # 실전 거래당 기댓값이 이 값 미만이면 경고


@dataclass
class Roundtrip:
    strategy: str
    market: str
    exit_ts: str
    pnl_krw: float
    size_krw: float
    reason: str
    signal_price: float = 0.0
    fill_price: float = 0.0

    @property
    def pct(self) -> float:
        return (self.pnl_krw / self.size_krw * 100) if self.size_krw else 0.0


def _has_col(con, name: str) -> bool:
    return name in {r[1] for r in con.execute("PRAGMA table_info(trades)")}


def load_roundtrips(db_path: str | None = None,
                    strategies: tuple[str, ...] = ("rsi2", "rsi2_15m"),
                    since: str | None = None) -> list[Roundtrip]:
    """
    청산(exit) 기록을 읽어 왕복 거래로 만든다.
    size_krw 는 청산 행에 없으므로(0), 같은 전략·코인의 **직전 진입** 행에서 가져온다.
    """
    path = db_path or settings.db_path
    try:
        con = sqlite3.connect(path)
        fill = "fill_price" if _has_col(con, "fill_price") else "NULL"
        rows = con.execute(
            f"SELECT id, ts, strategy, market, price, {fill}, pnl_krw, reason, size_krw"
            " FROM trades WHERE event='exit' AND strategy IN (%s)"
            % ",".join("?" * len(strategies)) +
            (" AND ts >= ?" if since else "") + " ORDER BY id",
            (*strategies, *( (since,) if since else () )),
        ).fetchall()
        entries = con.execute(
            f"SELECT id, strategy, market, size_krw, price, {fill} FROM trades"
            " WHERE event='entry' ORDER BY id").fetchall()
    except sqlite3.Error:
        return []
    finally:
        try:
            con.close()
        except Exception:
            pass

    out: list[Roundtrip] = []
    for eid, ts, strat, market, px, fpx, pnl, reason, size in rows:
        prior = [e for e in entries if e[1] == strat and e[2] == market and e[0] < eid]
        entry = prior[-1] if prior else None
        out.append(Roundtrip(
            strategy=strat, market=market, exit_ts=ts, pnl_krw=pnl or 0.0,
            size_krw=(size or 0.0) or (entry[3] if entry else 0.0),
            reason=reason or "",
            signal_price=(entry[4] if entry else 0.0) or 0.0,
            fill_price=(entry[5] if entry and entry[5] else 0.0) or 0.0,
        ))
    return out


def slippage_pct(trips: list[Roundtrip]) -> float | None:
    """
    진입 실효 슬리피지 평균(%). 신호가 대비 체결가가 얼마나 불리했는가.
    fill_price 가 기록되기 전 거래는 제외한다(v2.9 이전).
    """
    vals = [(t.fill_price - t.signal_price) / t.signal_price * 100
            for t in trips if t.signal_price > 0 and t.fill_price > 0]
    return sum(vals) / len(vals) if vals else None


def summarize(trips: list[Roundtrip]) -> dict:
    if not trips:
        return {"n": 0}
    pcts = [t.pct for t in trips]
    wins = [p for p in pcts if p > 0]
    losses = [p for p in pcts if p <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) else math.inf
    mean = sum(pcts) / len(pcts)
    if len(pcts) > 1:
        sd = (sum((p - mean) ** 2 for p in pcts) / (len(pcts) - 1)) ** 0.5
        t = mean / (sd / math.sqrt(len(pcts))) if sd else 0.0
    else:
        t = 0.0
    return {
        "n": len(pcts), "winrate": len(wins) / len(pcts) * 100, "pf": pf,
        "exp_pct": mean, "t": t, "pnl_krw": sum(t_.pnl_krw for t_ in trips),
        "slippage": slippage_pct(trips),
    }


def verdict(overall: dict, per_strategy: dict[str, dict]) -> tuple[str, list[str]]:
    """(한 줄 판정, 사유 목록). 표본이 부족하면 성적을 판정하지 않는다."""
    notes: list[str] = []
    n = overall.get("n", 0)
    slip = overall.get("slippage")
    if slip is not None and slip > SLIPPAGE_ALERT:
        notes.append(f"🚨 실효 슬리피지 {slip:+.3f}% > {SLIPPAGE_ALERT}% — "
                     f"거래당 기댓값(+0.18%)을 넘어섰다. 이대로면 신호가 맞아도 진다.")
    if n < MIN_JUDGE_TRADES:
        notes.append(f"표본 {n}건 — {MIN_JUDGE_TRADES}건 미만이라 성적을 판정하지 않는다. "
                     f"(적은 표본의 승률·PF는 운이다)")
        return f"관찰 중 ({n}/{TARGET_TRADES}건)", notes

    for name, s in per_strategy.items():
        b = BACKTEST.get(name)
        if not b or s["n"] < MIN_JUDGE_TRADES:
            continue
        if s["exp_pct"] < EXP_FLOOR_RATIO:
            notes.append(f"⚠️ {name}: 거래당 {s['exp_pct']:+.3f}% (백테스트 +{b['exp_pct']}%) "
                         f"— 실전이 음수다.")
        elif s["exp_pct"] < b["exp_pct"] * 0.5:
            notes.append(f"⚠️ {name}: 거래당 {s['exp_pct']:+.3f}% 로 백테스트"
                         f"(+{b['exp_pct']}%)의 절반 미만이다.")
        if s["winrate"] < b["winrate"] - 15:
            notes.append(f"⚠️ {name}: 승률 {s['winrate']:.0f}% (백테스트 {b['winrate']}%) — 15%p 이상 낮다.")

    if overall["exp_pct"] < 0:
        return f"🔴 중단 검토 ({n}/{TARGET_TRADES}건) — 실전 기댓값이 음수", notes
    if any(x.startswith("⚠️") or x.startswith("🚨") for x in notes):
        return f"🟠 주의 ({n}/{TARGET_TRADES}건)", notes
    return f"🟢 순항 ({n}/{TARGET_TRADES}건)", notes


def report(db_path: str | None = None) -> dict:
    trips = load_roundtrips(db_path)
    overall = summarize(trips)
    per = {name: summarize([t for t in trips if t.strategy == name])
           for name in BACKTEST}
    per = {k: v for k, v in per.items() if v.get("n")}
    head, notes = verdict(overall, per)
    return {"verdict": head, "notes": notes, "overall": overall, "per_strategy": per,
            "target": TARGET_TRADES, "backtest": BACKTEST}


def format_text(rep: dict) -> str:
    """텔레그램/콘솔용 평이한 한국어 리포트."""
    o = rep["overall"]
    if not o.get("n"):
        return ("📊 rsi2 인큐베이션 리포트\n"
                "아직 청산된 거래가 없습니다. 신호를 기다리는 중입니다.\n"
                "(하루 약 1회 거래가 정상이며, 조용한 국면에서는 며칠간 0건일 수 있습니다)")
    lines = [f"📊 rsi2 인큐베이션 리포트 — {rep['verdict']}", ""]
    bar_n = min(20, int(o["n"] / rep["target"] * 20))
    lines.append(f"진행 [{'█' * bar_n}{'░' * (20 - bar_n)}] {o['n']}/{rep['target']}건")
    lines.append("")
    lines.append(f"누적 손익  {o['pnl_krw']:+,.0f}원")
    lines.append(f"거래당     {o['exp_pct']:+.3f}%   (백테스트 +0.176%)")
    lines.append(f"승률       {o['winrate']:.1f}%    (백테스트 69.0%)")
    pf = o["pf"]
    lines.append(f"손익비(PF) {'∞' if pf == math.inf else f'{pf:.2f}'}     (백테스트 1.50)")
    if o["n"] > 1:
        lines.append(f"t값        {o['t']:+.2f}     (엣지 입증엔 +2 이상 필요)")
    if o.get("slippage") is not None:
        mark = "🚨" if o["slippage"] > SLIPPAGE_ALERT else "✅"
        lines.append(f"슬리피지   {o['slippage']:+.3f}% {mark} (경보선 {SLIPPAGE_ALERT}%)")
    else:
        lines.append("슬리피지   기록 없음 (v2.9 이후 거래부터 집계됩니다)")
    if rep["per_strategy"]:
        lines.append("")
        for name, s in rep["per_strategy"].items():
            lines.append(f"  · {name}: {s['n']}건 승률 {s['winrate']:.0f}% "
                         f"거래당 {s['exp_pct']:+.3f}% ({s['pnl_krw']:+,.0f}원)")
    if rep["notes"]:
        lines.append("")
        lines += rep["notes"]
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_text(report()))

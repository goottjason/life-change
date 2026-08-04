"""
easy_teaching — 원저자 강의(2025.09) 반영 후 재검증.

## 왜 다시 하는가
1차(오더블록+FVG)·2차(페이크아웃/트랩 36셀) 모두 §11 미통과였다. 그 뒤 원저자의 전체 강의
(docs/strategies/easy-teaching-man/2025. 09. …)를 확보했고, **내 구현이 원문과 다른 지점**을
찾았다. 그중 하나는 결과를 뒤집을 수 있는 크기다:

  ★ **"손절은 복마감(봉마감)을 보고 한다."** 강의는 손절 기준을 둘로 제시한다 —
    ① 꼬리 끝을 깨면 즉시  ② 꼬리 끝을 벗어나 **봉마감**할 때.
    그리고 "②가 ①보다 승률이 확실히 높다"고 명시한다.
    1·2차 백테스트는 **전부 ①(intrabar 저가 터치)** 이었고, 승률이 25~30%로 나온 것이
    정확히 그 증상이다(꼬리로 스치고 되돌아오는 거래가 전부 패배로 계상됐다).

  ★ **손익비 게이트는 원저자가 명시적으로 경계한다.** "1:1 고정은 핸디캡", "필수가 아니라
    권장", "손익비 따지다 먹을 것도 못 먹고 나오는 경우가 대다수"(손익비의 함정).
    v2.7이 넣은 RR ≥ 1.2 게이트는 원문에 없던 제약이다.

  ★ **오더블록 신뢰도 필터**: "각 캔들의 몸통 크기가 **2배 이상** 차이 날 경우 신뢰할 만한
    오더블록으로 식별된다." 1·2차는 아무 장악형이나 다 받았다.

## 사전 등록 (돌리기 전에 고정)
요인을 한 번에 다 바꾸면 무엇이 기여했는지 알 수 없다 → **ablation** 으로 하나씩 켠다.

  A 현행(v2.7)            : intrabar 손절 · RR게이트 ON  · 몸통필터 없음
  B +봉마감손절            : **봉마감 손절** · RR게이트 ON  · 몸통필터 없음
  C +RR게이트 해제         : intrabar 손절 · RR게이트 OFF · 몸통필터 없음
  D +몸통2배 필터          : intrabar 손절 · RR게이트 ON  · **몸통 2배**
  E 강의 충실(전부 적용)   : **봉마감 손절** · RR게이트 OFF · **몸통 2배**

  5구성 × 타임프레임 2(15분·1시간) = **10셀 전부 보고**(사후 선별 금지).
  다중비교 보정: 10개 비교 → 유의 임계값 **|t| > 2.8**(Šidák 근사, α=0.05).
  비용은 full(수수료+실측 스프레드) 1차 판정, fee-only 병기.

  ⚠ 전 구간 in-sample. 통과 셀이 나오면 결론이 아니라 **walk-forward 대상**일 뿐이다.

실행: .venv/bin/python backtesting/research/lab_easy_teaching_lecture.py
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config import charter as C
from backtesting.research import lab_easy_teaching as L
from backtesting.research.data_cache import load

# (이름, stop_on_close, use_rr_gate, min_ob_body_mult)
CONFIGS = [
    ("A 현행(v2.7)",      False, True,  0.0),
    ("B +봉마감손절",      True,  True,  0.0),
    ("C +RR게이트해제",    False, False, 0.0),
    ("D +몸통2배필터",     False, True,  2.0),
    ("E 강의충실(전부)",   True,  False, 2.0),
]
TFS = ["15min", "1h"]
SIDAK_T = 2.8


def run_cell(stop_on_close: bool, rr_gate: bool, body: float, tf: str):
    L.TF = tf
    L.WARM = {"15min": 900, "1h": 260, "4h": 220}[tf]
    L.COST_MODEL = "full"
    L.SPEC = replace(C.STRATEGY_SPECS["easy_teaching"],
                     confluence_mode="ob_fvg", require_trend=True,
                     stop_on_close=stop_on_close, use_rr_gate=rr_gate,
                     min_ob_body_mult=body)
    trades = []
    for sym in C.VALIDATED_MARKETS:
        df5 = load(f"KRW-{sym}", "minute5")
        df = L.resample(df5, tf) if df5 is not None else None
        if df is None or len(df) < L.WARM + 200:
            continue
        trades += L.run_market(f"KRW-{sym}", df)
    fee = [{**t, "net": t["gross"] - C.FEE_ROUNDTRIP * 100} for t in trades]
    return L.report(trades), L.report(fee), trades


def main() -> None:
    print(f"사전 등록 ablation: {len(CONFIGS)}구성 × {len(TFS)}TF = "
          f"{len(CONFIGS) * len(TFS)}셀 · 다중비교 보정 |t| > {SIDAK_T}\n")
    hdr = (f"{'구성':18} {'TF':6} {'거래':>6} {'승률%':>6} {'PF':>5} {'gross%':>7} "
           f"{'net%':>7} {'t':>6} {'판정':>4}  {'net(fee만)%':>11}")
    print(hdr); print("-" * len(hdr))
    rows = []
    for name, soc, rr, body in CONFIGS:
        for tf in TFS:
            full, fee, trades = run_cell(soc, rr, body, tf)
            if not full["n"]:
                print(f"{name:18} {tf:6} {0:6d}  거래 없음"); continue
            ok = (full["n"] >= C.BACKTEST_MIN_TRADES
                  and full["winrate"] > C.BACKTEST_MIN_WINRATE * 100
                  and full["pf"] > C.BACKTEST_MIN_PROFIT_FACTOR
                  and full["exp"] > 0 and full["t"] > SIDAK_T)
            print(f"{name:18} {tf:6} {full['n']:6d} {full['winrate']:6.1f} "
                  f"{full['pf']:5.2f} {full['gross_exp']:+7.3f} {full['exp']:+7.3f} "
                  f"{full['t']:+6.2f} {'✅' if ok else '❌':>4}  {fee['exp']:+11.3f}",
                  flush=True)
            rows.append({"name": name, "tf": tf, "ok": ok, "fee_exp": fee["exp"],
                         **{k: full[k] for k in
                            ("n", "winrate", "pf", "exp", "gross_exp", "t")}})

    print("\n" + "=" * 84)
    if not rows:
        print("전 셀 거래 0건."); return
    win = [r for r in rows if r["ok"]]
    if win:
        print(f"§11 + 보정 통과: {len(win)}셀")
        for r in win:
            print(f"  {r['name']}/{r['tf']}: {r['n']}거래 승률 {r['winrate']:.1f}% "
                  f"PF {r['pf']:.2f} 거래당 {r['exp']:+.3f}% t {r['t']:+.2f}")
        print("→ 결론 아님. walk-forward 재검증 대상(전 구간 in-sample).")
    else:
        print("§11 + 보정 통과 셀: **0개**")
    base = {r["tf"]: r for r in rows if r["name"].startswith("A")}
    print("\n[요인별 기여 — 현행(A) 대비 gross 변화]")
    for r in rows:
        if r["name"].startswith("A"):
            continue
        b = base.get(r["tf"])
        if b:
            print(f"  {r['name']:18} {r['tf']:6} gross {b['gross_exp']:+.3f} → "
                  f"{r['gross_exp']:+.3f} ({r['gross_exp'] - b['gross_exp']:+.3f}%p) · "
                  f"승률 {b['winrate']:.1f}% → {r['winrate']:.1f}% · "
                  f"거래 {b['n']} → {r['n']}")


if __name__ == "__main__":
    main()

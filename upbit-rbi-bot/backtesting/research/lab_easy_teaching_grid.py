"""
easy_teaching 근거 조합 그리드 — 페이크아웃/트랩을 넣으면 비용 벽을 넘는가.

## 왜 그리드인가 / 사전 등록
v2.7 재설계본(오더블록+FVG)은 전 타임프레임 §11 미통과였다. 결정적 수치는
**거래당 gross 엣지 +0.04~0.12% vs 왕복비용 0.23~0.26%** — 청산을 원문대로 고쳐도
바뀌지 않았으므로 문제는 **진입 신호의 엣지 크기**다. 그래서 원문에서 아직 구현하지
않은 근거인 **페이크아웃/트랩**(원문이 "★가장 중요★"라 한 것)을 넣고 다시 잰다.

여러 조합을 재면 그중 하나는 우연히 좋아 보인다. 그래서 **돌리기 전에** 아래를 고정한다:

  조합(3) × 추세필터(2) × 타임프레임(2) × 지지유의성 pivot_k(3) = **36개 셀**,
  전부 보고한다(사후 선별 금지).
  1차 판정 비용모형 = full(수수료 + 실측 스프레드 전액). fee-only 는 참고로만 병기한다.
  다중비교 보정: 36개 비교이므로 유의 임계값을 **|t| > 3.05** 로 둔다(Šidák 근사, α=0.05).
  즉 §11 통과 + t > 3.05 를 동시에 넘겨야 '살아 있다'고 말한다.

  ※ pivot_k 를 축에 넣은 이유(투명하게 밝힌다): 그리드를 확정하기 전 스모크 테스트로
    fakeout/추세True/1h/k=3 한 셀을 돌렸고 2,119거래가 나왔다. 3봉 피벗마다 '지지'가
    생긴다는 뜻이라 원문의 '채널 하단'과 수준이 다르다. 결과를 보고 값을 고르는 것을
    피하려고, 튜닝하는 대신 **유의성을 축으로 올려 전부 보고**한다.

  ⚠ 이 그리드는 전 구간 in-sample 이다. 통과 셀이 나오면 그 자체로 결론이 아니라
    **walk-forward 재검증 대상**이 될 뿐이다(README 의 rsi2 검증 절차와 같다).

실행: .venv/bin/python backtesting/research/lab_easy_teaching_grid.py
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config import charter as C
from backtesting.research import lab_easy_teaching as L
from backtesting.research.data_cache import load

MODES = ["ob_fvg", "fakeout", "fakeout_zone"]
TRENDS = [True, False]
TFS = ["15min", "1h"]
PIVOT_KS = [3, 6, 10]
SIDAK_T = 3.05         # 36개 비교 보정 후 유의 임계값


def run_cell(mode: str, require_trend: bool, tf: str, pivot_k: int) -> tuple[dict, dict]:
    """(full 비용, fee-only 비용) 리포트. 거래는 한 번만 시뮬레이션하고 비용만 갈아 끼운다."""
    L.TF = tf
    L.WARM = {"15min": 900, "1h": 260, "4h": 220}[tf]
    L.COST_MODEL = "full"
    L.SPEC = replace(C.STRATEGY_SPECS["easy_teaching"], confluence_mode=mode,
                     require_trend=require_trend, pivot_k=pivot_k)
    trades = []
    for sym in C.VALIDATED_MARKETS:
        market = f"KRW-{sym}"
        df5 = load(market, "minute5")
        df = L.resample(df5, tf) if df5 is not None else None
        if df is None or len(df) < L.WARM + 200:
            continue
        trades += L.run_market(market, df)
    # fee-only 는 gross 에서 수수료만 빼면 된다(재시뮬 불필요)
    fee_trades = [{**t, "net": t["gross"] - C.FEE_ROUNDTRIP * 100} for t in trades]
    return L.report(trades), L.report(fee_trades)


def main() -> None:
    total = len(MODES) * len(TRENDS) * len(TFS) * len(PIVOT_KS)
    print(f"사전 등록 그리드: {len(MODES)}조합 × {len(TRENDS)}추세 × {len(TFS)}TF "
          f"× {len(PIVOT_KS)}pivot_k = {total}셀 · 다중비교 보정 |t| > {SIDAK_T}\n")
    header = (f"{'조합':13} {'추세':5} {'TF':6} {'k':>3} {'거래':>5} {'승률%':>6} {'PF':>5} "
              f"{'gross%':>7} {'net%':>7} {'t':>6} {'판정':>5}  {'net(fee만)%':>11}")
    print(header)
    print("-" * len(header))

    rows = []
    for mode in MODES:
        for trend in TRENDS:
            for tf in TFS:
                for k in PIVOT_KS:
                    full, fee = run_cell(mode, trend, tf, k)
                    if not full["n"]:
                        print(f"{mode:13} {str(trend):5} {tf:6} {k:3d} {0:5d}  거래 없음")
                        continue
                    passed = (full["n"] >= C.BACKTEST_MIN_TRADES
                              and full["winrate"] > C.BACKTEST_MIN_WINRATE * 100
                              and full["pf"] > C.BACKTEST_MIN_PROFIT_FACTOR
                              and full["exp"] > 0 and full["t"] > SIDAK_T)
                    print(f"{mode:13} {str(trend):5} {tf:6} {k:3d} {full['n']:5d} "
                          f"{full['winrate']:6.1f} {full['pf']:5.2f} "
                          f"{full['gross_exp']:+7.3f} {full['exp']:+7.3f} {full['t']:+6.2f} "
                          f"{'✅' if passed else '❌':>5}  {fee['exp']:+11.3f}", flush=True)
                    rows.append({"mode": mode, "trend": trend, "tf": tf, "k": k,
                                 "passed": passed,
                                 **{kk: full[kk] for kk in
                                    ("n", "winrate", "pf", "exp", "gross_exp", "t")},
                                 "fee_exp": fee["exp"]})

    print("\n" + "=" * 78)
    if not rows:
        print("모든 셀에서 거래 0건.")
        return
    winners = [r for r in rows if r["passed"]]
    if winners:
        print(f"§11 + 다중비교 보정 통과: {len(winners)}셀")
        for r in winners:
            print(f"  {r['mode']}/추세{r['trend']}/{r['tf']}/k{r['k']}: {r['n']}거래 "
                  f"PF {r['pf']:.2f} 거래당 {r['exp']:+.3f}% t {r['t']:+.2f}")
        print("→ 결론 아님. walk-forward 재검증 대상일 뿐이다(전 구간 in-sample).")
    else:
        print("§11 + 다중비교 보정 통과 셀: **0개**")
        best = max(rows, key=lambda r: r["exp"])
        print(f"최선 셀: {best['mode']}/추세{best['trend']}/{best['tf']}/k{best['k']} "
              f"{best['n']}거래 거래당 {best['exp']:+.3f}% (gross {best['gross_exp']:+.3f}%) "
              f"t {best['t']:+.2f}")
        gb = max(rows, key=lambda r: r["gross_exp"])
        print(f"gross 최대: {gb['mode']}/추세{gb['trend']}/{gb['tf']}/k{gb['k']} "
              f"{gb['gross_exp']:+.3f}% — 왕복비용 0.23~0.26% 대비 "
              f"{gb['gross_exp'] / 0.245:.2f}배")


if __name__ == "__main__":
    main()

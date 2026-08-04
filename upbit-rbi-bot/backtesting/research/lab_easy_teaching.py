"""
easy_teaching (오더블록 + FVG) §11 백테스트.

**왜 이 파일이 이제야 생겼는가** — easy_teaching 은 2026-07-30 에 백테스트 없이 실계좌에
투입됐다(backtesting/·tests/·incubation/ 어디에도 참조가 없었다). 결과는 진입 16건 /
청산 15건 / 1승, 거래당 −0.379%, 합계 약 −1,886원이었다. 헌장 §11 은 "통과 못하면 미련
없이 폐기"이므로, 되살리려면 먼저 여기를 통과해야 한다.

검증 대상은 **v2.7 재설계본**이다(구조 손절 = 오더블록 저점, 반익반본, 반등 확인,
1시간봉 추세 필수). 라이브와 같은 코드(strategies/easy_teaching.py)를 봉 단위로 돌려
백테스트-라이브 괴리를 없앤다(§11 재현성).

비용: 왕복 수수료 0.1% + 종목별 실측 스프레드(charter.VALIDATED_MARKETS, 보수적으로 전액).
통과 기준(헌장 §11): 승률 > 55%, PF > 1.5, MDD < 20%, 표본 100거래 이상.
추가로 거래당 기댓값 > 0 과 t > 2 를 함께 본다(승률·PF 만으로는 비용을 못 이길 수 있다).

실행: .venv/bin/python backtesting/research/lab_easy_teaching.py [--markets N] [--bars N]
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from config import charter as C
from strategies.base import Action
from strategies.easy_teaching import EasyTeachingStrategy
from backtesting.research.data_cache import load

SPEC = C.STRATEGY_SPECS["easy_teaching"]
TF = "15min"        # 검증 타임프레임 (--tf 로 교체)
WARM = 900          # 상위추세 EMA200 확보에 충분한 봉 수

# 상위 추세를 볼 봉 = 대상 봉의 4배 (15분→1시간, 1시간→4시간, 4시간→1일).
# 라이브의 '15분봉 매매 + 1시간봉 추세'와 같은 비율을 유지한다.
HTF = {"15min": "1h", "1h": "4h", "4h": "1D"}


def resample(df5: pd.DataFrame, tf: str) -> pd.DataFrame:
    return df5.resample(tf).agg({"open": "first", "high": "max", "low": "min",
                                 "close": "last", "volume": "sum"}).dropna()


def trend_htf(df: pd.DataFrame) -> np.ndarray:
    """상위 타임프레임 EMA200 위 여부 — 라이브(trend_up_from_hourly)와 같은 계산."""
    from indicators import ta
    h = df["close"].resample(HTF.get(TF, "1h")).last().dropna()
    up = h > ta.ema(h, 200)
    return up.shift(1).reindex(df.index, method="ffill").astype(float).fillna(0.0).to_numpy(bool)


COST_MODEL = "full"     # "full" = 수수료 + 실측 스프레드 전액 / "fee" = 수수료만


def cost_ratio(market: str) -> float:
    """
    왕복 비용.
      full — 수수료 0.1% + 종목별 실측 스프레드 전액. 시장가로 스프레드를 다 문다는 가정.
      fee  — 수수료 0.1% 만. 지정가가 전량 스프레드 없이 체결된다는 **낙관적** 가정.
    실제는 둘 사이다. 두 값을 같이 보면 '엣지가 체결 품질에 달렸는가'를 가를 수 있다.
    """
    if COST_MODEL == "fee":
        return C.FEE_ROUNDTRIP
    sym = market.split("-", 1)[-1]
    return C.FEE_ROUNDTRIP + C.VALIDATED_MARKETS.get(sym, 0.0025)


def run_market(market: str, df: pd.DataFrame, bars: int | None = None) -> list[dict]:
    """
    라이브 전략을 봉 단위로 호출해 거래를 재현한다.
    체결 가정(보수적): 진입은 확인봉 **다음 봉의 시가**, 청산도 트리거 발생 봉의 시가/해당가.
    """
    strat = EasyTeachingStrategy(SPEC)
    up = trend_htf(df)
    # 멀티 타임프레임 겹침(강의): 상위 봉의 오더블록 구간을 미리 계산해 ctx 로 넘긴다.
    # 각 시점에서 '그때까지 확정된' 상위봉 오더블록만 보이게 해야 미래참조가 없다.
    htf_zones: list[tuple] = []
    if SPEC.require_mtf_overlap:
        hdf = df.resample(HTF.get(TF, "1h")).agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"}).dropna()
        for z in strat._find_order_blocks(hdf):
            htf_zones.append((hdf.index[z.formed_at], z.bottom, z.top))
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    cl = df["close"].to_numpy()
    n = len(df)
    start = WARM
    end = n if bars is None else min(n, WARM + bars)
    cost = cost_ratio(market)
    time_stop = C.time_stop_bars_for(SPEC)

    trades: list[dict] = []
    pos = None
    for i in range(start, end):
        if pos is not None:
            held = i - pos["bar"]
            stop = pos["entry"] if pos["half"] else pos["stop"]
            exit_px, why = None, ""
            # 손절 판정 기준 (강의): 기본은 꼬리 끝 터치 즉시(저가), stop_on_close 면
            # **손절선 아래로 봉마감**해야 손절한다. 원저자는 후자가 "승률이 확실히 높다"고
            # 명시한다 — 꼬리로 스치고 되돌아오는 케이스를 살려주기 때문이다.
            if SPEC.stop_on_close:
                hit_stop = cl[i] <= stop
                fill = cl[i]              # 마감 후 청산이므로 종가 체결(보수적)
            else:
                hit_stop = l[i] <= stop
                fill = stop
            if hit_stop:
                exit_px, why = fill, "stop_loss" if not pos["half"] else "breakeven"
            elif not pos["half"] and h[i] >= pos["target"]:    # 반익절 — 절반만
                pos["half"] = True
                pos["parts"].append((pos["target"], SPEC.partial_tp_ratio))
            elif held >= time_stop:
                exit_px, why = o[i], "time_stop"
            if exit_px is not None:
                pos["parts"].append((exit_px, 1.0 - sum(w for _, w in pos["parts"])))
                gross = sum(w * (px / pos["entry"] - 1) for px, w in pos["parts"])
                trades.append({"market": market, "ts": df.index[i], "reason": why,
                               "gross": gross * 100, "net": (gross - cost) * 100,
                               "bars": held, "half": pos["half"]})
                pos = None

        if pos is not None or i + 1 >= n:
            continue
        # 라이브와 동일: df 의 마지막 봉은 '미완성' 취급되므로 i+1 까지 넘긴다
        ctx = {"trend_up": bool(up[i])}
        if SPEC.require_mtf_overlap:
            now = df.index[i]
            ctx["htf_obs"] = [(b, t) for ts, b, t in htf_zones if ts <= now]
        sig = strat.signal(df.iloc[max(0, i - 400): i + 2], ctx)
        if sig.action != Action.ENTER_LONG:
            continue
        entry = o[i + 1]                                      # 다음 봉 시가 체결(보수적)
        # 손익비 게이트 — 강의는 "손익비 1:1 고정은 핸디캡, 필수가 아니라 권장"이라며
        # 명시적으로 경계한다("손익비의 함정"). 그래서 켜고 끌 수 있는 축으로 둔다.
        if SPEC.use_rr_gate:
            if not C.entry_rr_ok(entry, sig.stop_price, sig.target_price):
                continue
        elif not (sig.stop_price < entry < sig.target_price):
            continue                                          # 기하가 성립하지 않는 자리만 배제
        pos = {"entry": entry, "stop": sig.stop_price, "target": sig.target_price,
               "bar": i + 1, "half": False, "parts": []}
    return trades


def report(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    net = np.array([t["net"] for t in trades])
    wins, losses = net[net > 0], net[net <= 0]
    pf = (wins.sum() / abs(losses.sum())) if losses.sum() else math.inf
    eq = np.cumsum(net)
    mdd = float(np.max(np.maximum.accumulate(eq) - eq)) if len(eq) else 0.0
    t = float(net.mean() / (net.std(ddof=1) / math.sqrt(len(net)))) if len(net) > 1 else 0.0
    gross = np.array([t_["gross"] for t_ in trades])
    return {"n": len(net), "winrate": len(wins) / len(net) * 100, "pf": pf,
            "exp": float(net.mean()), "t": t, "mdd_pct": mdd, "total": float(net.sum()),
            "gross_exp": float(gross.mean())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", type=int, default=99)
    ap.add_argument("--bars", type=int, default=None, help="종목당 검사 봉 수(디버그용)")
    ap.add_argument("--tf", default="15min",
                    help="검증 타임프레임(15min/1h/4h). 원문은 레버리지 선물의 구조 스윙 "
                         "타점을 전제하므로 15분봉은 노이즈일 수 있다 — 비용 벽 대비 "
                         "목표 크기를 키우려면 상위 봉으로 올려야 한다.")
    args = ap.parse_args()
    global TF, WARM
    TF = args.tf
    WARM = {"15min": 900, "1h": 260, "4h": 220}.get(TF, 900)

    markets = [m for m in (list(C.VALIDATED_MARKETS)) ][: args.markets]
    all_trades: list[dict] = []
    print(f"[타임프레임 {TF} · 상위추세 {HTF.get(TF, '1h')}]")
    print(f"{'market':10} {'거래':>5} {'승률%':>7} {'PF':>6} {'거래당%':>8} {'t':>6}")
    for sym in markets:
        market = f"KRW-{sym}"
        df5 = load(market, "minute5")
        df = resample(df5, TF) if df5 is not None else (
            resample(load(market, "minute15"), TF)
            if load(market, "minute15") is not None else None)
        if df is None or len(df) < WARM + 200:
            print(f"{market:10} {'— 캐시 없음/부족':>30}")
            continue
        tr = run_market(market, df, args.bars)
        all_trades += tr
        r = report(tr)
        if r["n"]:
            print(f"{market:10} {r['n']:5d} {r['winrate']:7.1f} {r['pf']:6.2f} "
                  f"{r['exp']:+8.3f} {r['t']:+6.2f}")
        else:
            print(f"{market:10} {0:5d} {'—':>7} {'—':>6} {'—':>8} {'—':>6}")

    r = report(all_trades)
    print("\n" + "=" * 62)
    if not r["n"]:
        print("거래 0건 — 신호가 성립하지 않았다. 진입 조건이 과도하게 좁다는 뜻이다.")
        print("§11 판정: **미통과** (표본 없음)")
        return
    print(f"전체: {r['n']}거래 승률 {r['winrate']:.1f}% PF {r['pf']:.2f} "
          f"거래당 {r['exp']:+.3f}% t {r['t']:+.2f} 누적 {r['total']:+.1f}% "
          f"MDD {r['mdd_pct']:.1f}%p")
    # 비용 탓인가 신호 탓인가 — gross 가 음수면 비용을 0으로 만들어도 지는 신호다.
    print(f"비용 차감 전(gross) 거래당 {r['gross_exp']:+.3f}% "
          f"→ 비용 {r['gross_exp'] - r['exp']:.3f}%")
    from collections import Counter
    print("청산 사유:", dict(Counter(t_["reason"] for t_ in all_trades)))
    half = sum(1 for t_ in all_trades if t_.get("half"))
    print(f"반익절 도달: {half}/{r['n']}건 ({half / r['n'] * 100:.1f}%)")
    checks = [
        ("표본 ≥ 100거래", r["n"] >= C.BACKTEST_MIN_TRADES),
        ("승률 > 55%", r["winrate"] > C.BACKTEST_MIN_WINRATE * 100),
        ("PF > 1.5", r["pf"] > C.BACKTEST_MIN_PROFIT_FACTOR),
        ("거래당 기댓값 > 0", r["exp"] > 0),
        ("t > 2", r["t"] > 2.0),
    ]
    for label, ok in checks:
        print(f"  {'✅' if ok else '❌'} {label}")
    print(f"§11 판정: **{'통과' if all(ok for _, ok in checks) else '미통과'}**")
    if not all(ok for _, ok in checks):
        print("→ 헌장 §11: 통과 못하면 미련 없이 폐기. ACTIVE_STRATEGIES 에 넣지 않는다.")


if __name__ == "__main__":
    main()

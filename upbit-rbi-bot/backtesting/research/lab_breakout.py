"""
breakout 실험 트랙 캘리브레이션 (v4.0, 스펙 §7 — docs/superpowers/specs/2026-08-14-…).

⚠ 이것은 §11 통과/탈락 판정이 **아니다**. 운영자는 결과와 무관하게 가동을 결정했다.
목적: ① 시작 파라미터가 실제로 하루 3~10건을 만드는지
     ② 그 대역에서 가장 덜 나쁜 조합 선택
     ③ 예상 수업료(월 예상 손익)를 가동 전에 보고

체결 모델: 진입 = 신호봉 종가(라이브의 '다음 tick 시장가' 근사 — 리포 관례와 동일).
청산 = Position.check_price_exit 를 종가 기준으로 재현:
    트레일링(고점−trail×진입ATR) · 고정손절 백스톱(max(1×ATR/가격, 1%)) ·
    시간손절 12봉(+0.3% 이상 수익 중이면 유예).
비용 = 수수료 0.1% + 종목별 스프레드. 스프레드는 min(실측 중앙값, 헌장 검증 상한) —
    실측 JSON 에는 호가단위 변경 전 낡은 값(DOGE 0.93%)이 있고, 라이브 스크리너는
    상한 초과 시 진입 자체를 막으므로 '상한'이 실제 거래 시 비용의 상한이다.

실행: .venv/bin/python backtesting/research/lab_breakout.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from indicators import ta                                  # noqa: E402
from config import charter as C                            # noqa: E402
from backtesting.research import data_cache                # noqa: E402

FEE = C.FEE_ROUNDTRIP           # 0.001
MIN_STOP = C.MIN_STOP_RATIO     # 0.01 (백스톱 하한)
MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
           "KRW-LINK", "KRW-BCH", "KRW-ETC", "KRW-DOT", "KRW-AVAX"]

# 1차 그리드(12/20/36 × 0/1.5/2 × 1/1.5/2)는 전 조합이 하루 25~91건으로 목표(3~10건)를
# 크게 초과했고 net −0.24~−0.27%로 균일했다(= 비용이 지배, gross ≈ −0.03%p).
# → 룩백·거래량 필터를 위로 확장한 2차 그리드. n=288(24시간 고점)에서 gross 가 양수로
#   돌아서고(net −0.19% vs 비용 ~0.21%) 빈도가 목표 대역에 들어온다.
GRID_N = (36, 72, 144, 288)
GRID_VOL = (2.0, 3.0, 5.0)
GRID_TRAIL = (1.5, 2.0)


def load_spreads() -> dict[str, float]:
    med: dict[str, float] = {}
    for name in ("spreads.json", "spreads_mid.json"):
        p = data_cache.DATA_DIR / name
        if p.exists():
            for m, v in json.loads(p.read_text()).items():
                med[m] = max(med.get(m, 0.0), float(v))
    out = {}
    for m in MARKETS:
        sym = m.split("-")[1]
        cap = C.VALIDATED_MARKETS.get(sym, C.MAX_SPREAD_RATIO)
        out[m] = min(med.get(m, cap), cap)
    return out


def simulate(df: pd.DataFrame, spread: float, n: int, vol_mult: float, trail: float,
             stop_mult: float = 1.0, time_stop: int = 12,
             min_profit: float = 0.003) -> list[dict]:
    high_n = df["high"].shift(1).rolling(n).max()
    vol_avg = df["volume"].shift(1).rolling(n).mean()
    atr = ta.atr(df)
    close = df["close"].to_numpy()
    cond = df["close"] > high_n
    if vol_mult > 0:
        cond &= df["volume"] >= vol_avg * vol_mult
    entry_idx = np.flatnonzero(cond.to_numpy())
    atr_np = atr.to_numpy()
    trades: list[dict] = []
    i_free = 0
    last = len(df) - 1
    for i in entry_idx:
        if i < n + 15 or i < i_free or i >= last:
            continue
        e_price, e_atr = close[i], float(atr_np[i])
        if not (e_atr > 0):
            continue
        sl = max(stop_mult * e_atr / e_price, MIN_STOP)
        hard = e_price * (1 - sl)
        high = e_price
        exit_px, why, j = None, "", i
        for j in range(i + 1, len(df)):
            px = close[j]
            high = max(high, px)
            if px <= max(high - trail * e_atr, hard):
                exit_px, why = px, ("stop" if px < e_price else "trail_tp")
                break
            if j - i >= time_stop and (px - e_price) / e_price < min_profit:
                exit_px, why = px, "time"
                break
        if exit_px is None:
            exit_px, why = close[-1], "eod"
        trades.append({"net": (exit_px - e_price) / e_price - FEE - spread,
                       "hold": j - i, "why": why})
        i_free = j + 1               # 동일 종목 중복 보유 금지 (§3.3과 동일)
    return trades


def main() -> None:
    spreads = load_spreads()
    panels = {}
    for m in MARKETS:
        df = data_cache.load(m, "minute5")
        if df is not None and len(df) > 50_000:
            panels[m] = df
    if not panels:
        print("캐시 없음 — data_cache.py 먼저 실행")
        return
    days = max(len(df) for df in panels.values()) * 5 / 60 / 24
    print(f"종목 {len(panels)}개 · 기간 약 {days:.0f}일 · "
          f"스프레드(적용값) {['%s %.3f%%' % (m.split('-')[1], s*100) for m, s in spreads.items()]}")
    rows = []
    for n in GRID_N:
        for vm in GRID_VOL:
            for tr in GRID_TRAIL:
                allt = []
                for m, df in panels.items():
                    allt += simulate(df, spreads[m], n, vm, tr)
                if not allt:
                    continue
                nets = np.array([t["net"] for t in allt])
                whys = [t["why"] for t in allt]
                rows.append({
                    "n": n, "vol": vm, "trail": tr, "trades": len(allt),
                    "per_day": round(len(allt) / days, 1),
                    "win%": round(float((nets > 0).mean()) * 100, 1),
                    "net%": round(float(nets.mean()) * 100, 4),
                    "hold_med": int(np.median([t["hold"] for t in allt])),
                    "time%": round(whys.count("time") / len(allt) * 100, 0),
                    "trail%": round(whys.count("trail_tp") / len(allt) * 100, 0),
                })
    out = pd.DataFrame(rows).sort_values("net%", ascending=False)
    txt = out.to_string(index=False)
    print(txt)
    dest = Path(__file__).parent / "results" / "breakout_calibration_2026-08-14.txt"
    dest.write_text(txt + "\n")
    band = out[(out.per_day >= 3) & (out.per_day <= 10)]
    if len(band):
        b = band.iloc[0]
        krw_month = b["net%"] / 100 * C.EXPERIMENT_MAX_ORDER_KRW * b["per_day"] * 30
        msg = (f"\n★ 시작값 후보(하루 3~10건 대역 중 최선): n={b['n']:.0f} vol_mult={b['vol']} "
               f"trail={b['trail']} — 하루 {b['per_day']}건 · 승률 {b['win%']}% · "
               f"거래당 {b['net%']:+.4f}% · 월 예상 손익 {krw_month:+,.0f}원 (건당 1만원 기준)")
    else:
        msg = "\n⚠ 하루 3~10건 대역에 조합이 없음 — 그리드 확장 필요 (운영자 상의)"
    print(msg)
    with dest.open("a") as f:
        f.write(msg + "\n")


if __name__ == "__main__":
    main()

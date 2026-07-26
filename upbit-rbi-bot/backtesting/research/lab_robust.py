"""
IS 통과 후보의 **현실성 검증** — OOS로 가기 전 반드시 통과해야 하는 관문.

5분봉 단기 반전 신호의 고전적 함정: **bid-ask bounce(호가 왕복 착시)**.
 최근 급락한 코인의 5분 종가는 '매도호가에 찍힌 체결가'일 확률이 높다. 다음 봉에 가격이
 '반등'하는 것처럼 보이지만 실제로는 매수호가로 사야 하므로 그 반등은 잡을 수 없다.
 승률 70%대·t 20 같은 수치는 이 착시에서 자주 나온다.

두 축으로 스트레스를 준다:
 1) 슬리피지(왕복 추가비용) 0 / 0.05% / 0.10% / 0.20% — 업비트 KRW 호가 스프레드 수준
 2) 진입 시점: 신호 봉 종가 즉시 vs **다음 봉 시가**(라이브는 10초 폴링·지정가라 즉시 체결 아님)

'다음 봉 시가 진입 + 슬리피지 0.1%'에서도 살아남는 조합만 진짜 후보다.

실행: .venv/bin/python backtesting/research/lab_robust.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from indicators import ta
from backtesting.research import lab
from backtesting.research.data_cache import DATA_DIR
from backtesting.research.fastsim import ExitCfg, Precomp, simulate_arrays, stats_chrono
from backtesting.research.lab_screen import IS_FRACTION, LAB_WARMUP

SLIPS = [0.0, 0.0005, 0.0010, 0.0020]
DELAYS = [0, 1]
TOP_PER_CAND = 2          # 후보별 IS t 상위 몇 개를 스트레스 테스트할지


def main() -> None:
    fins = json.loads((DATA_DIR / "finalists.json").read_text())
    if not fins:
        print("finalists.json 비어 있음 — lab_screen.py 먼저 실행")
        return
    # 후보별 IS t 상위만 추린다(같은 후보의 유사 조합 중복 제거)
    by_cand: dict[str, list] = {}
    for f in sorted(fins, key=lambda x: -x["is_stats"]["t"]):
        by_cand.setdefault(f["cand"], []).append(f)
    picks = [f for v in by_cand.values() for f in v[:TOP_PER_CAND]]

    panel = lab.load_panel("minute5")
    ctx = lab.Ctx.build(panel)
    n = len(ctx.index)
    split = int(n * IS_FRACTION)
    ohlc = {m: (df["close"].to_numpy(float), df["high"].to_numpy(float),
                df["low"].to_numpy(float), ta.atr(df).to_numpy(float),
                df["open"].to_numpy(float)) for m, df in panel.items()}

    print("현실성 스트레스 테스트 (IS 구간에서만 수행 — OOS는 아직 건드리지 않음)")
    print("각 칸 = 거래당 기댓값(exp) / t값. 신호봉종가 → 다음봉시가로 바꿨을 때의 붕괴 여부가 핵심\n")

    for f in picks:
        cand = next(c for c in lab.CANDIDATES if c.name == f["cand"])
        cfg = ExitCfg(**f["exit"])
        pstr = ",".join(f"{k}={v}" for k, v in f["params"].items())
        print(f"── {cand.name} [{pstr}] {cfg.label()}")
        sigs = {m: cand.fn(df, ctx, m, **f["params"]) for m, df in panel.items()}
        print(f"   {'진입':14s}" + "".join(f"{'슬립 ' + format(s, '.2%'):>20s}" for s in SLIPS))
        for delay in DELAYS:
            cells = []
            for slip in SLIPS:
                items = []
                for m, (enter, exit_) in sigs.items():
                    close, high, low, atr, open_ = ohlc[m]
                    pre = Precomp(enter, exit_, close, high, low, atr, open_)
                    for t in simulate_arrays(pre, cfg, start=LAB_WARMUP, end=split,
                                             slippage=slip, entry_delay=delay):
                        items.append((ctx.index[t.entry_i], t.pnl))
                s = stats_chrono(items)
                cells.append(f"{s.exp:+.3%}/t{s.t_stat:+5.1f}")
            label = "신호봉 종가" if delay == 0 else "다음봉 시가"
            print(f"   {label:14s}" + "".join(f"{c:>20s}" for c in cells))
        print()

    print("판정 기준: '다음봉 시가 + 슬립 0.10%' 칸에서 exp>0 이고 t>3 이어야 실전 후보로 인정.")
    print("붕괴하면 그 엣지는 호가 착시(bid-ask bounce)였다는 뜻 — 실계좌에서 재현되지 않는다.")


if __name__ == "__main__":
    main()

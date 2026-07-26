"""
시간대(KST) 효과 확인 — 문헌의 intraday seasonality가 업비트 KRW 시장에도 있는지.

방법
 - 매 정시(분=0)에서 이후 1시간(12봉) 수익률을 잰다 → **표본이 겹치지 않아** t값이 유효하다.
 - 종목별로 재고 같은 시각끼리 평균한 뒤 시간대별로 모은다(종목 간 동시 상관으로 t가
   과대평가되는 것을 방지).
 - 참고용 서술 통계다. 여기서 좋은 시간대를 골라 바로 쓰면 과최적화이므로,
   게이트로 쓸 경우 반드시 OOS로 재검증해야 한다.

실행: .venv/bin/python backtesting/research/hour_study.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from backtesting.research import lab

BARS_AHEAD = 12          # 1시간


def main() -> None:
    panel = lab.load_panel("minute5")
    if not panel:
        print("캐시 없음 — data_cache.py lab 실행 필요")
        return
    idx = next(iter(panel.values())).index
    fwd = pd.DataFrame({m: df["close"].shift(-BARS_AHEAD) / df["close"] - 1
                        for m, df in panel.items()}, index=idx)
    rng = pd.DataFrame({m: (df["high"].rolling(BARS_AHEAD).max()
                            / df["low"].rolling(BARS_AHEAD).min() - 1).shift(-BARS_AHEAD)
                        for m, df in panel.items()}, index=idx)
    avg = fwd.mean(axis=1)                 # 종목 평균 → 종목 간 상관 제거
    avg_rng = rng.mean(axis=1)
    on_hour = idx.minute == 0
    s = avg[on_hour]
    r = avg_rng[on_hour]
    hours = idx[on_hour].hour

    print(f"KST 시간대 효과 · {len(panel)}종목 평균 · 정시 진입 후 1시간 수익률 · "
          f"{idx[0].date()}~{idx[-1].date()}")
    print(f"{'시(KST)':7s} {'표본':>5s} {'평균수익':>9s} {'t':>6s} {'상승비율':>7s} {'평균변동폭':>9s}")
    best, worst = [], []
    for h in range(24):
        v = s[hours == h].dropna()
        w = r[hours == h].dropna()
        if len(v) < 20:
            continue
        t = float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if v.std() > 0 else 0.0
        print(f"{h:02d}시    {len(v):5d} {v.mean():+9.3%} {t:+6.2f} "
              f"{float((v > 0).mean()):7.1%} {w.mean():9.3%}")
        (best if t > 0 else worst).append((t, h))
    print(f"\n변동폭(평균 고저 폭)이 큰 시간대일수록 5분봉 단타의 수수료 문턱(0.1%)을 넘기 쉽다.")
    if best:
        print(f"수익 t 상위: {', '.join(f'{h:02d}시(t{t:+.1f})' for t, h in sorted(best, reverse=True)[:5])}")
    if worst:
        print(f"수익 t 하위: {', '.join(f'{h:02d}시(t{t:+.1f})' for t, h in sorted(worst)[:5])}")
    print("※ 24개 시간대를 비교하므로 |t|>2 하나쯤은 우연히 나온다(다중비교). 게이트로 쓰려면 OOS 확인 필수.")


if __name__ == "__main__":
    main()

"""
easy_teaching — 필터 조합 전수 탐색 + **과최적화 진단**.

## 이 스크립트가 답하려는 것
"거래가 너무 많으니 필터를 걸어 줄이면 되지 않나?" — 필터는 결국 부분집합을 고르는 일이다.
그래서 **가능한 필터 조합을 거의 전부** 만들어 보고 두 가지를 본다:

  ① 탐색구간 최고 규칙의 기댓값이 왕복비용(≈0.245%)을 넘는가
  ② ★ **탐색에서 좋았던 규칙이 검증구간에서도 좋은가** (train↔test 상관)

②가 핵심이다. 상관이 0 근처면 "최고 규칙"은 실력이 아니라 **탐색 과정이 만들어낸 우연**이고,
그 경우 아무리 미세튜닝해도 실전에서는 재현되지 않는다. 수만 개 규칙을 뒤지면 그중 하나는
반드시 좋아 보이기 때문에, 이 진단 없이 "최적값을 찾았다"고 말하는 것은 의미가 없다.

데이터: lab_easy_teaching_features.py 가 만든 CSV(탐색구간 2024-07~2025-12).
탐색구간을 다시 시간순 train 60% / test 40% 로 나눈다. **홀드아웃(2026-01~)은 건드리지 않는다.**

실행: .venv/bin/python backtesting/research/lab_easy_teaching_filters.py [--tf 15min]
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / "data"
FEATURES = ["atr_pct", "rsi14", "ema50_dist", "ema200_dist", "vol_ratio", "confirm_body",
            "confirm_body_atr", "upper_wick", "lower_wick", "hour", "trend_up",
            "stop_pct", "target_pct", "rr"]
MIN_TRADES = 100          # 이보다 적게 남기는 규칙은 표본 부족으로 버린다
COST = 0.245              # 왕복비용 기준선(%)


def atomic_conditions(d: pd.DataFrame) -> list[tuple[str, np.ndarray]]:
    """각 특징을 십분위 경계에서 자른 단일 조건들(≥ / ≤ 양방향)."""
    conds = []
    for f in FEATURES:
        if f not in d:
            continue
        v = d[f].to_numpy()
        if len(np.unique(v)) < 3:
            for val in np.unique(v):
                conds.append((f"{f}=={val:g}", v == val))
            continue
        for q in np.arange(0.1, 0.91, 0.1):
            t = float(np.quantile(v, q))
            conds.append((f"{f}>={t:.4g}", v >= t))
            conds.append((f"{f}<={t:.4g}", v <= t))
    return conds


def evaluate(mask: np.ndarray, y: np.ndarray) -> tuple[int, float]:
    n = int(mask.sum())
    return (n, float(y[mask].mean()) if n else float("-inf"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="15min")
    ap.add_argument("--col", default="gross", help="gross(비용 전) 또는 net")
    args = ap.parse_args()

    d = pd.read_csv(DATA / f"et_features_{args.tf}_selection.csv")
    d = d.sort_values("ts").reset_index(drop=True)
    cut = int(len(d) * 0.6)
    tr, te = d.iloc[:cut], d.iloc[cut:]
    ytr, yte = tr[args.col].to_numpy(), te[args.col].to_numpy()
    print(f"탐색구간 {len(d)}거래 → train {len(tr)} / test {len(te)}")
    print(f"기준선: train {ytr.mean():+.4f}% · test {yte.mean():+.4f}% · 비용 {COST}%\n")

    ctr = atomic_conditions(tr)
    cte = dict(atomic_conditions(te))          # 같은 이름의 조건을 test 에서 재계산
    names_tr = [c[0] for c in ctr]
    # test 쪽은 train 의 '임계값'을 그대로 써야 한다 → 이름에서 값을 파싱해 재구성
    def te_mask(name: str) -> np.ndarray:
        if "==" in name:
            f, v = name.split("==");  return te[f].to_numpy() == float(v)
        if ">=" in name:
            f, v = name.split(">=");  return te[f].to_numpy() >= float(v)
        f, v = name.split("<=");      return te[f].to_numpy() <= float(v)

    results = []          # (train_exp, test_exp, n_tr, n_te, 이름)

    def consider(name: str, mtr: np.ndarray) -> None:
        ntr, etr = evaluate(mtr, ytr)
        if ntr < MIN_TRADES:
            return
        parts = name.split(" AND ")
        mte = np.ones(len(te), bool)
        for p in parts:
            mte &= te_mask(p)
        nte, ete = evaluate(mte, yte)
        if nte < 30:
            return
        results.append((etr, ete, ntr, nte, name))

    print("1중 조건 탐색…", flush=True)
    for name, m in ctr:
        consider(name, m)

    print("2중 조합 탐색…", flush=True)
    for (n1, m1), (n2, m2) in itertools.combinations(ctr, 2):
        if n1.split(">=")[0].split("<=")[0].split("==")[0] == \
           n2.split(">=")[0].split("<=")[0].split("==")[0]:
            continue                                    # 같은 특징끼리는 조합하지 않는다
        consider(f"{n1} AND {n2}", m1 & m2)

    print("3중 조합 탐색(2중 상위 200개 확장)…", flush=True)
    top2 = sorted([r for r in results if " AND " in r[4]], reverse=True)[:200]
    for etr, ete, ntr, nte, name in top2:
        base = np.ones(len(tr), bool)
        for p in name.split(" AND "):
            f = p.split(">=")[0].split("<=")[0].split("==")[0]
            base &= dict(ctr)[p] if p in dict(ctr) else base
        used = {p.split(">=")[0].split("<=")[0].split("==")[0] for p in name.split(" AND ")}
        for n3, m3 in ctr:
            if n3.split(">=")[0].split("<=")[0].split("==")[0] in used:
                continue
            consider(f"{name} AND {n3}", base & m3)

    results.sort(reverse=True)
    print(f"\n총 {len(results):,}개 규칙 평가\n")
    print("① 탐색(train)에서 가장 좋았던 규칙 10개 — 검증(test)에서는?")
    print(f"{'train%':>8} {'test%':>8} {'n_tr':>6} {'n_te':>6}  규칙")
    for etr, ete, ntr, nte, name in results[:10]:
        print(f"{etr:+8.4f} {ete:+8.4f} {ntr:6d} {nte:6d}  {name}")

    arr_tr = np.array([r[0] for r in results])
    arr_te = np.array([r[1] for r in results])
    corr = float(np.corrcoef(arr_tr, arr_te)[0, 1])
    top = results[: max(1, len(results) // 100)]        # 상위 1%
    print(f"\n② ★ 과최적화 진단")
    print(f"  train↔test 기댓값 상관: **{corr:+.3f}**")
    print(f"  train 상위 1%({len(top)}개) 규칙의 test 평균: "
          f"{np.mean([r[1] for r in top]):+.4f}%  (전체 test 평균 {yte.mean():+.4f}%)")
    print(f"  train 최고 규칙의 test 기댓값: {results[0][1]:+.4f}%")
    beat = [r for r in results if r[1] > COST]
    print(f"  test 에서 비용({COST}%)을 넘는 규칙: {len(beat)}개 / {len(results):,}")
    print(f"\n해석: 상관이 0 근처면 train 성적은 test 를 예측하지 못한다 = 탐색이 만든 우연이다.")


if __name__ == "__main__":
    main()

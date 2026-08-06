"""
12차 — **새 전략 계열 탐색** (사전 등록 가설, 파라미터 스윕 금지).

## 왜 스윕이 아니라 가설인가
4차에서 실측했다: 전수 탐색 4만 규칙 → train↔test 상관 **−0.281**(탐색에서 좋을수록
검증에서 나빴다). 그래서 여기서는 **파라미터를 훑지 않는다.** 대신 지금까지 이 리포가
검증한 것들(macd/rsi/cvd/rsi2/easy_teaching 5종)과 **구조적으로 다른 정보원**만 고른다.
각 가설은 **설정 하나씩**만 돌리고 **전부 보고**한다.

## 사전 등록 가설 (전부 구조적으로 새로운 정보원)
| # | 가설 | 왜 새로운가 |
|---|---|---|
| H1 | **펀딩비 극단 페이드** | 포지셔닝(군중 쏠림) 데이터. 가격에서 나오지 않는 정보. 리포 최초 |
| H2 | **횡단면 반전** | 지금까지 전부 시계열 신호였다. 종목 간 상대 비교는 처음 |
| H3 | **횡단면 모멘텀** | H2 의 반대 방향 — 둘 다 등록해야 사후선택이 아니다 |
| H4 | **변동성 압축 후 돌파** | 변동성의 2차 구조(압축→확장). 지금까지 수준만 봤다 |
| H5 | **거래량 충격 후 반전** | 거래량을 신호로 쓴 적이 없다(필터로만 썼다) |
| H6 | **베이시스(선물−현물 괴리)** | 두 시장의 가격차. 단일 시장 신호가 아니다 |

## 통제 (5~10차에서 확립한 것 전부)
- 무조건부 **드리프트 대조군** · **월클러스터 t** · 다중비교 보정
- 홀드아웃(2025-12-29~)은 탐색에서 통과 가설이 나올 때만 연다
- 사전 등록 6가설 × 2방향(롱/숏) = 12셀 → Šidák α=0.05 → **|t| > 3.0**

## 평가 방식
거래 시뮬레이션 대신 **신호 → 포트폴리오 수익률**로 잰다. 청산 규칙이 결과를 만들어내는
혼선을 없애고 신호 자체의 정보량만 본다(9차에서 청산 설계가 결과를 지배하는 걸 봤다).
매 시점 신호가 켜진 종목을 동일가중으로 보유, 정해진 기간 뒤 청산.

실행: .venv/bin/python backtesting/research/lab_newideas.py
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from backtesting.research import data_cache_futures as F
from backtesting.research import data_cache_ma200 as M

SPLIT = pd.Timestamp("2025-12-29")
HOLD = 24               # 보유 봉 수(1시간봉 → 24시간). 전 가설 공통, 사후 조정 금지
COST = 0.12             # 선물 왕복 비용 %


@dataclass(frozen=True)
class H:
    key: str
    name: str
    note: str


HYP = [
    H("funding", "H1 펀딩비 극단 페이드", "펀딩이 상위 10% → 롱 과밀 → 숏 / 하위 10% → 롱"),
    H("xs_rev", "H2 횡단면 반전", "직전 24h 수익률 하위 20% 매수(상위 20% 숏)"),
    H("xs_mom", "H3 횡단면 모멘텀", "직전 24h 수익률 상위 20% 매수(하위 20% 숏)"),
    H("squeeze", "H4 변동성 압축 후 돌파", "ATR/가격이 60일 하위 20%였다가 밴드 돌파"),
    H("vol_shock", "H5 거래량 충격 후 반전", "거래량 20봉 평균의 3배 + 큰 음봉 → 반등 매수"),
    H("basis", "H6 베이시스 괴리", "선물−현물 괴리가 상위/하위 10% → 수렴 방향"),
]


def panel():
    """선물 1시간봉 패널 + 펀딩 + (베이시스용) 현물 일봉."""
    px, fund = {}, {}
    for s in F.SYMBOLS:
        d = F.load(s, "1h")
        if d is None or len(d) < 3000:
            continue
        px[s] = d
        f = F.load_funding(s)
        if f is not None and not f.empty:
            fund[s] = f["rate"]
    return px, fund


def build_signals(px, fund):
    """각 가설의 신호를 (종목 × 시각) 불리언 두 장(롱/숏)으로 만든다. 미래참조 없음."""
    idx = sorted(set().union(*[d.index for d in px.values()]))
    C_ = pd.DataFrame({s: d["close"].reindex(idx) for s, d in px.items()}).ffill()
    H_ = pd.DataFrame({s: d["high"].reindex(idx) for s, d in px.items()}).ffill()
    L_ = pd.DataFrame({s: d["low"].reindex(idx) for s, d in px.items()}).ffill()
    O_ = pd.DataFrame({s: d["open"].reindex(idx) for s, d in px.items()}).ffill()
    V_ = pd.DataFrame({s: d["volume"].reindex(idx) for s, d in px.items()}).fillna(0)
    out = {}

    # H1 펀딩비 — 8시간마다 갱신되는 값을 시간축에 ffill (그 시점에 관측 가능)
    FD = pd.DataFrame({s: v.reindex(idx, method="ffill") for s, v in fund.items()})
    FD = FD.reindex(columns=C_.columns)
    q_hi = FD.quantile(0.90, axis=1); q_lo = FD.quantile(0.10, axis=1)
    out["funding"] = (FD.le(q_lo, axis=0), FD.ge(q_hi, axis=0))   # 펀딩 낮으면 롱, 높으면 숏

    # H2/H3 횡단면 24h 수익률
    r24 = C_ / C_.shift(24) - 1
    rk = r24.rank(axis=1, pct=True)
    out["xs_rev"] = (rk <= 0.2, rk >= 0.8)
    out["xs_mom"] = (rk >= 0.8, rk <= 0.2)

    # H4 변동성 압축 → 돌파
    tr = pd.concat([(H_ - L_).abs(), (H_ - C_.shift()).abs(), (L_ - C_.shift()).abs()])
    atr = (H_ - L_).rolling(14).mean() / C_
    comp = atr <= atr.rolling(60 * 24).quantile(0.2)
    up = C_ > H_.rolling(24).max().shift(1)
    dn = C_ < L_.rolling(24).min().shift(1)
    out["squeeze"] = (comp & up, comp & dn)

    # H5 거래량 충격 + 큰 봉 → 반전
    vr = V_ / V_.rolling(20).mean()
    body = (C_ - O_) / O_
    out["vol_shock"] = ((vr >= 3) & (body <= -0.02), (vr >= 3) & (body >= 0.02))

    # H6 베이시스 — 선물 종가 vs 현물 괴리
    # ⚠ 시간대 정합 (첫 실행에서 여기서 미래참조 버그가 났다):
    #   선물 캐시는 **KST**, 현물 캐시는 **UTC** 로 저장돼 있다. 라벨을 그냥 맞추면
    #   현물이 9시간 **미래** 가격이 되어 롱·숏이 동시에 +2% 나오는 가짜 신호가 됐다.
    #   보정 = +9h(UTC→KST) +4h(4시간봉이 닫히는 시점) = **+13h** 만큼 현물 라벨을 민다.
    sp = {}
    for s in C_.columns:
        d = M.load_coin(s, "4h")
        if d is not None:
            v = d["close"].copy()
            v.index = v.index + pd.Timedelta(hours=13)
            sp[s] = v.reindex(idx, method="ffill")
    SP = pd.DataFrame(sp).reindex(columns=C_.columns)
    bs = (C_ / SP - 1)
    bh_ = bs.quantile(0.90, axis=1); bl_ = bs.quantile(0.10, axis=1)
    out["basis"] = (bs.le(bl_, axis=0), bs.ge(bh_, axis=0))       # 저평가 롱 / 고평가 숏
    return out, C_, O_


def evaluate(sig, C_, O_, key, period="selection"):
    """신호 → 동일가중 포트폴리오. HOLD 봉 뒤 청산. 롱/숏 각각 평가."""
    longs, shorts = sig[key]
    idx = C_.index
    mask = (np.asarray(idx < SPLIT) if period == "selection" else np.asarray(idx >= SPLIT))
    fwd = (C_.shift(-1 - HOLD) / O_.shift(-1) - 1) * 100      # 다음 봉 시가 진입 → HOLD 뒤 종가
    res = {}
    base = float(fwd.values[mask].astype(float)[~np.isnan(fwd.values[mask].astype(float))].mean())
    for name, s, sgn in (("롱", longs, 1.0), ("숏", shorts, -1.0)):
        s = s.reindex_like(fwd).fillna(False) & mask[:, None]
        # 신호가 겹치는 시각은 동일가중 평균 → 시각별 1개 관측치
        r = (fwd.where(s) * sgn).mean(axis=1).dropna()
        r = r[r.index.isin(idx[mask])]
        if len(r) < 50:
            res[name] = None; continue
        b = sgn * base
        net = r - COST
        mo = net.groupby(net.index.to_period("M")).mean()
        ct = float(mo.mean() / (mo.std(ddof=1) / math.sqrt(len(mo)))) if len(mo) > 1 else 0.0
        res[name] = {"n": len(r), "gross": r.mean(), "net": net.mean(),
                     "ex": r.mean() - b, "ct": ct,
                     "pos_mo": int((mo > 0).sum()), "n_mo": len(mo), "drift": b}
    return res


def main() -> None:
    px, fund = panel()
    print(f"[새 전략 계열 탐색 · 선물 {len(px)}종목 1시간봉 · 보유 {HOLD}봉 · "
          f"왕복비용 {COST}%]")
    print(f"사전 등록 6가설 × 2방향 = 12셀 → Šidák 보정 |t| > 3.0 · 판정은 **월클러스터 t**")
    sig, C_, O_ = build_signals(px, fund)
    hdr = (f"{'가설':26} {'방향':>4} {'관측':>6} {'gross%':>8} {'드리프트':>8} "
           f"{'초과%p':>8} {'net%':>8} {'월t':>6} {'양수월':>7}")
    print(); print(hdr); print("-" * len(hdr))
    hits = []
    for h in HYP:
        r = evaluate(sig, C_, O_, h.key)
        for d in ("롱", "숏"):
            v = r.get(d)
            if not v:
                print(f"{h.name:26} {d:>4} {'표본부족':>6}"); continue
            print(f"{h.name:26} {d:>4} {v['n']:6d} {v['gross']:+8.4f} {v['drift']:+8.4f} "
                  f"{v['ex']:+8.4f} {v['net']:+8.4f} {v['ct']:+6.2f} "
                  f"{v['pos_mo']:3d}/{v['n_mo']:<3d}")
            if v["net"] > 0 and v["ct"] > 3.0:
                hits.append((h, d, v))
    print("\n[판정] net > 0 이고 월클러스터 t > 3.0")
    if not hits:
        print("  없음.")
        near = [(h, d, v) for h in HYP for d, v in evaluate(sig, C_, O_, h.key).items()
                if v and v["net"] > 0 and v["ct"] > 1.5]
        for h, d, v in near:
            print(f"  (참고) {h.name} {d}: net {v['net']:+.4f}% 월t {v['ct']:+.2f} — 미달")
    for h, d, v in hits:
        print(f"  ★ {h.name} {d}: net {v['net']:+.4f}% 월t {v['ct']:+.2f} (n={v['n']})")
    print("\n각 가설:")
    for h in HYP:
        print(f"  {h.name:26} {h.note}")


if __name__ == "__main__":
    main()

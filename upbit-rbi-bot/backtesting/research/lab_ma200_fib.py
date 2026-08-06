"""
10차 — **200MA 이중 타임프레임 + 피보나치 눌림목** 전략 백테스트 (2026-06-11 영상).

원문 정리: `docs/strategies/easy-teaching-man/2026. 06. 11. 진짜 이동평균선 보는 법 …md`

## 전략 (영상 그대로)
```
지표   MA_slow = SMA(200, 일봉)
       MA_fast = SMA(200, 4시간봉)   # 코인: 실제 4h봉 / 주식: 일봉 100 근사(장 6.5h → 하루 2봉)
진입   ① MA_fast 가 MA_slow 상향 돌파(골든크로스) → 셋업 개시
       ② 눌림목 대기. 피보나치 앵커 low=크로스 이전 저점, high=크로스 이후 최고점
       ③ high−(high−low)×{0.382, 0.5, 0.618} 에서 분할 매수 (비중 1:1:2)
익절   가격이 high 돌파 → 보유량의 1/2 청산 → 나머지는 스탑을 평단으로(본절) 올리고 추세 태움
손절   3차까지 전부 체결된 뒤에만 설정. stop = 평단 − (high−평단)/2   (RR 2:1)
제외   데드크로스 직후 곧바로 골든크로스(이평선 꼬임) → 거래 안 함
```

## ★ 이 검정에서 가장 중요한 것 — 승률에 속지 않기
댓글에 "백테스팅해보니 승률이 꽤 좋다"는 얘기가 있다. **구조상 승률은 높게 나올 수밖에 없다:**
  - 목표(직전 고점)는 **가깝고** 자주 닿는다 → 절반 익절 성공 = '승리'로 집계
  - 나머지 절반은 **본절 스탑**이라 최악이 0 → 손실로 안 잡힌다
  - 손실은 **3차까지 다 채워지고 RR 2:1 손절**에 닿을 때만 발생 → 드물지만 크다
이 리포가 4차에서 이미 겪은 구조다("승률 70%는 착시 — 목표가 가까워 자주 닿고 손절은 멀어
가끔 크게 맞는다"). 그러므로 **승률이 아니라 거래당 기댓값과 매수후보유 대비 초과**로 판정한다.

## ⚠ 생존 편향
'현재의' 대형주로 과거를 재면 살아남아 오른 종목만 고르는 셈이다. 롱 전용 추세추종에
결정적으로 유리하다. 완화책으로 무너진 대형주를 섞었고(`data_cache_ma200.STOCKS`),
**결과를 '현재 승자군' vs '무너진 군'으로 나눠서도 보고**한다.

실행: .venv/bin/python backtesting/research/lab_ma200_fib.py [--venue stocks|coins]
      [--fast 100] [--weights 1,1,2] [--tangle-days 0] [--cost 0.05]
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from backtesting.research import data_cache_ma200 as M

FIB = (0.382, 0.5, 0.618)
SLOW = 200
# 무너진 대형주 그룹(생존 편향 진단용) — 데이터 캐시의 주석과 같은 목록
FALLEN = {"INTC", "PYPL", "ZM", "PTON", "DOCU", "ROKU", "SQ", "SHOP", "SNAP", "UBER",
          "LYFT", "RIVN", "LCID", "NIO", "BABA", "BA", "F", "GM", "WBA", "MRNA",
          "PFE", "CVS", "TGT", "EBAY", "TWLO", "ZS", "OKTA", "U", "RBLX", "AFRM",
          "SOFI", "CHWY", "W", "ETSY", "MTCH", "PINS", "SPOT", "NET", "COIN", "HOOD"}


@dataclass
class Cfg:
    fast: int = 100          # 주식: MA200(4h) 근사 일봉 수 / 코인은 실제 4h 사용
    weights: tuple = (1, 1, 2)
    tangle_days: int = 0     # 직전 데드크로스가 이 일수 이내면 '꼬임'으로 보고 스킵 (0=끄기)
    cost: float = 0.05       # 왕복 비용 %(주식 0.05, 코인 0.2 권장)
    anchor_lookback: int = 250   # 크로스 이전 저점을 찾을 최대 소급 일수
    max_wait: int = 400      # 눌림목을 기다리는 최대 일수(이 안에 안 오면 셋업 포기)


def load(venue: str):
    """(심볼, 일봉 df, MA_fast, MA_slow) 를 돌려준다. 미래참조 없도록 전부 종가 기준."""
    if venue == "stocks":
        for t in dict.fromkeys(M.STOCKS):
            d = M.load_stock(t)
            if d is None or len(d) < SLOW + 300:
                continue
            yield t, d
    else:
        for s in M.COINS:
            d1 = M.load_coin(s, "1d")
            d4 = M.load_coin(s, "4h")
            if d1 is None or d4 is None or len(d1) < SLOW + 300:
                continue
            yield s, (d1, d4)


def make_mas(venue: str, data, cfg: Cfg):
    if venue == "stocks":
        d = data
        fast = d["close"].rolling(cfg.fast).mean()
        slow = d["close"].rolling(SLOW).mean()
        return d, fast, slow
    d1, d4 = data
    # 4시간봉 SMA200 → 각 날짜의 마지막 값으로 일봉에 정렬 (그날 종가 시점에 알 수 있는 값)
    f4 = d4["close"].rolling(200).mean()
    f4d = f4.groupby(f4.index.normalize()).last()
    fast = f4d.reindex(d1.index.normalize()).to_numpy()
    fast = pd.Series(fast, index=d1.index)
    slow = d1["close"].rolling(SLOW).mean()
    return d1, fast, slow


def run_symbol(sym, d, fast, slow, cfg: Cfg):
    o = d["open"].to_numpy(); h = d["high"].to_numpy()
    l = d["low"].to_numpy(); c = d["close"].to_numpy()
    f = fast.to_numpy(); s = slow.to_numpy()
    n = len(d)
    ok = ~(np.isnan(f) | np.isnan(s))
    above = np.where(ok, f > s, False)
    gc = np.zeros(n, bool); dc = np.zeros(n, bool)
    gc[1:] = above[1:] & ~above[:-1] & ok[1:] & ok[:-1]
    dc[1:] = ~above[1:] & above[:-1] & ok[1:] & ok[:-1]
    last_dc = -10**9
    trades = []
    i = 0
    while i < n:
        if not gc[i]:
            if dc[i]:
                last_dc = i
            i += 1
            continue
        if cfg.tangle_days and (i - last_dc) <= cfg.tangle_days:
            i += 1
            continue                                     # 이평선 꼬임 → 스킵
        # ── 셋업 개시 ──
        lo_from = max(0, i - cfg.anchor_lookback)
        anchor_low = float(l[lo_from:i + 1].min())
        high = float(h[i])                               # 크로스 이후 최고점(갱신)
        filled = []          # (price, weight)
        wsum = sum(cfg.weights)
        j = i + 1
        avg = None
        stop = None
        half_done = False
        qty = 0.0
        exit_reason = None
        pnl_parts = []       # (수익률, 비중)
        while j < n:
            if dc[j] and not filled:
                exit_reason = "expired_dc"; break        # 미체결 상태에서 추세 종료
            if not filled and (j - i) > cfg.max_wait:
                exit_reason = "expired_wait"; break
            if not half_done and not filled:
                high = max(high, float(h[j]))            # 진입 전에는 고점 갱신
            rng = high - anchor_low
            if rng <= 0:
                j += 1; continue
            levels = [high - rng * r for r in FIB]
            # ── 진입: 그날 저가가 레벨에 닿으면 지정가 체결 ──
            for k, lv in enumerate(levels):
                if k < len(filled):
                    continue
                if l[j] <= lv and (len(filled) == k):
                    filled.append((lv, cfg.weights[k]))
            if filled:
                tw = sum(w for _, w in filled)
                avg = sum(p * w for p, w in filled) / tw
                if len(filled) == 3:
                    stop = avg - (high - avg) / 2.0      # RR 2:1, 3차 완료 후에만
            # ── 청산 ──
            if filled:
                if stop is not None and l[j] <= stop and not half_done:
                    r = (stop / avg - 1) * 100
                    pnl_parts.append((r, 1.0)); exit_reason = "stop"; break
                if half_done and l[j] <= avg:            # 본절 스탑
                    pnl_parts.append((0.0, 0.5)); exit_reason = "breakeven"; break
                if not half_done and h[j] >= high:       # 1/2 익절
                    pnl_parts.append(((high / avg - 1) * 100, 0.5))
                    half_done = True
                if half_done and dc[j]:                  # 추세 종료 → 잔량 청산
                    pnl_parts.append(((c[j] / avg - 1) * 100, 0.5))
                    exit_reason = "runner_dc"; break
            j += 1
        if exit_reason is None and filled:
            pnl_parts.append((((c[n - 1] / avg - 1) * 100), 0.5 if half_done else 1.0))
            exit_reason = "eod"
        if filled and pnl_parts:
            gross = sum(r * w for r, w in pnl_parts)
            deployed = sum(w for _, w in filled) / wsum
            trades.append({
                "sym": sym, "ts": d.index[i], "entry_ts": d.index[min(j, n - 1)],
                "gross": gross, "net": gross - cfg.cost, "reason": exit_reason,
                "tranches": len(filled), "days": int(j - i),
                "deployed": deployed, "half": half_done,
                "fallen": sym in FALLEN})
        i = max(j, i + 1)
    return trades


def buy_hold(venue: str, cfg: Cfg):
    """대조군 — 각 종목을 MA 워밍업 끝난 날 사서 끝까지 보유. 비용 1회."""
    rs, ann = [], []
    for sym, data in load(venue):
        d, f, s = make_mas(venue, data, cfg)
        v = d["close"].to_numpy()
        st = SLOW + 10
        if st >= len(v) - 10:
            continue
        r = (v[-1] / v[st] - 1) * 100 - cfg.cost
        yrs = (d.index[-1] - d.index[st]).days / 365.25
        rs.append(r)
        if yrs > 0.5:
            ann.append(((1 + r / 100) ** (1 / yrs) - 1) * 100)
    return float(np.mean(rs)), float(np.median(rs)), float(np.mean(ann))


def report(tr, label, bh=None):
    if not tr:
        print(f"{label}: 거래 0건"); return None
    d = pd.DataFrame(tr)
    net = d["net"].to_numpy()
    wins, losses = net[net > 0], net[net <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() else math.inf
    mo = d.groupby(d["ts"].dt.to_period("Y"))["net"].mean()
    ct = float(mo.mean() / (mo.std(ddof=1) / math.sqrt(len(mo)))) if len(mo) > 1 else 0.0
    t = float(net.mean() / (net.std(ddof=1) / math.sqrt(len(net)))) if len(net) > 1 else 0.0
    per = d.groupby("sym")["net"].mean()
    print(f"\n── {label} ──")
    print(f"  거래 {len(d):,}건 · **승률 {(net > 0).mean()*100:.1f}%** · PF {pf:.2f} · "
          f"평균보유 {d['days'].mean():.0f}일")
    print(f"  거래당 net **{net.mean():+.3f}%** (거래t {t:+.2f} · 연클러스터t {ct:+.2f}) · "
          f"중앙값 {np.median(net):+.3f}%")
    print(f"  승리 평균 {wins.mean() if len(wins) else 0:+.2f}% · "
          f"패배 평균 {losses.mean() if len(losses) else 0:+.2f}% · "
          f"종목양수 {(per > 0).sum()}/{len(per)}")
    from collections import Counter
    print(f"  청산사유 {dict(Counter(d['reason']))}")
    print(f"  체결단계 {dict(Counter(d['tranches']))} · 절반익절 도달 "
          f"{d['half'].mean()*100:.1f}% · 평균 투입비중 {d['deployed'].mean()*100:.0f}%")
    if bh:
        print(f"  [대조군] 매수후보유 평균 {bh[0]:+.1f}% · 중앙값 {bh[1]:+.1f}% · "
              f"연율 {bh[2]:+.1f}%")
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", default="stocks", choices=["stocks", "coins"])
    ap.add_argument("--fast", type=int, default=100)
    ap.add_argument("--weights", default="1,1,2")
    ap.add_argument("--tangle-days", type=int, default=0)
    ap.add_argument("--cost", type=float, default=None)
    a = ap.parse_args()
    cfg = Cfg(fast=a.fast, weights=tuple(int(x) for x in a.weights.split(",")),
              tangle_days=a.tangle_days,
              cost=a.cost if a.cost is not None else (0.05 if a.venue == "stocks" else 0.2))
    fast_txt = (f"일봉 SMA{cfg.fast} (4시간 MA200 근사)" if a.venue == "stocks"
                else "실제 4시간봉 SMA200")
    print(f"[200MA 이중 타임프레임 + 피보나치 눌림목 · {a.venue}]")
    print(f"  MA_fast = {fast_txt} · MA_slow = 일봉 SMA200")
    print(f"  분할비중 {cfg.weights} · 꼬임필터 {cfg.tangle_days}일 · 왕복비용 {cfg.cost}%")

    tr = []
    for sym, data in load(a.venue):
        d, f, s = make_mas(a.venue, data, cfg)
        tr += run_symbol(sym, d, f, s, cfg)
    bh = buy_hold(a.venue, cfg)
    d = report(tr, f"전체 ({a.venue})", bh)
    if d is None:
        return
    if a.venue == "stocks":
        report([t for t in tr if not t["fallen"]], "① 현재 승자군(생존편향 노출)")
        report([t for t in tr if t["fallen"]], "② 무너진 대형주군(편향 완화)")
    print("\n[승률 해부] — 구조상 승률이 높게 나오는지 확인")
    for r in sorted(set(d["reason"])):
        x = d[d["reason"] == r]["net"]
        print(f"  {r:14} {len(x):5d}건 ({len(x)/len(d)*100:4.1f}%) 평균 {x.mean():+.2f}%")


if __name__ == "__main__":
    main()


# ── 대조군·CAGR 비교 (핵심) ─────────────────────────────────
def cagr_compare(venue: str, cfg: Cfg):
    """
    종목마다 '전략 계좌'(거래 중이 아닐 땐 현금)를 복리로 굴려 CAGR 을 내고
    매수후보유 CAGR 과 맞붙인다. 그리고 엣지가 어디서 오는지 대조군으로 분해한다.
      A. 골든크로스 매수 → 데드크로스 매도 (피보나치·눌림목 없음) = 순수 추세추종
      B. 무작위 날짜 매수 → 같은 평균 보유일수 (신호 제거)
      C. 매수후보유
    """
    rng = np.random.default_rng(11)
    rows = []
    for sym, data in load(venue):
        d, f, s = make_mas(venue, data, cfg)
        v = d["close"].to_numpy(); n = len(v)
        st = SLOW + 10
        if st >= n - 260:
            continue
        yrs = (d.index[-1] - d.index[st]).days / 365.25
        if yrs < 2:
            continue
        # 전략
        tr = run_symbol(sym, d, f, s, cfg)
        mult = 1.0
        for t in tr:
            mult *= (1 + t["net"] / 100)
        strat = (mult ** (1 / yrs) - 1) * 100
        # A. 골든크로스 → 데드크로스
        fa, sa = f.to_numpy(), s.to_numpy()
        ok = ~(np.isnan(fa) | np.isnan(sa))
        ab = np.where(ok, fa > sa, False)
        gc = np.zeros(n, bool); dc = np.zeros(n, bool)
        gc[1:] = ab[1:] & ~ab[:-1] & ok[1:] & ok[:-1]
        dc[1:] = ~ab[1:] & ab[:-1] & ok[1:] & ok[:-1]
        m2, pos = 1.0, None
        for i in range(st, n):
            if pos is None and gc[i]:
                pos = v[i]
            elif pos is not None and dc[i]:
                m2 *= (1 + (v[i] / pos - 1) - cfg.cost / 100); pos = None
        if pos is not None:
            m2 *= (1 + (v[-1] / pos - 1) - cfg.cost / 100)
        a_gc = (m2 ** (1 / yrs) - 1) * 100
        # B. 무작위 진입, 같은 평균 보유
        hold = int(np.mean([t["days"] for t in tr])) if tr else 250
        hold = max(20, min(hold, n - st - 5))
        m3, k = 1.0, st
        while k + hold < n:
            k2 = k + hold
            m3 *= (1 + (v[k2] / v[k] - 1) - cfg.cost / 100)
            k = k2 + int(rng.integers(0, max(2, hold)))
        b_rand = (m3 ** (1 / yrs) - 1) * 100
        # C. 매수후보유
        bh = ((v[-1] / v[st]) ** (1 / yrs) - 1) * 100
        rows.append({"sym": sym, "yrs": yrs, "strat": strat, "gc_dc": a_gc,
                     "rand": b_rand, "bh": bh, "n_tr": len(tr),
                     "fallen": sym in FALLEN})
    return pd.DataFrame(rows)


def cagr_report(x: pd.DataFrame, tag: str):
    if x.empty:
        print(f"{tag}: 없음"); return
    print(f"\n── CAGR 비교 ({tag}, {len(x)}종목 · 평균 {x['yrs'].mean():.1f}년) ──")
    print(f"{'':26} {'평균':>9} {'중앙값':>9} {'B&H초과(중앙)':>13} {'B&H이긴종목':>11}")
    for col, name in (("strat", "전략(MA+피보나치)"), ("gc_dc", "A. 골든→데드크로스만"),
                      ("rand", "B. 무작위 진입"), ("bh", "C. 매수후보유")):
        med = x[col].median()
        beat = (x[col] > x["bh"]).mean() * 100
        print(f"  {name:24} {x[col].mean():+8.2f}% {med:+8.2f}% "
              f"{med - x['bh'].median():+12.2f}%p {beat:10.1f}%")

"""
실제 체결 비용 측정 — 백테스트 결론을 좌우하는 단 하나의 미지수.

`lab_verdict.py` 결과: 후보 전략의 엣지(+0.05~0.09%/거래)는 왕복 슬리피지가 0.1%를 넘으면
소멸한다. 따라서 '업비트에서 이 종목을 실제로 얼마에 사고팔 수 있는가'가 성패를 가른다.

두 가지를 측정한다:
 1) **호가 단위(tick) 하한** — 업비트 KRW는 가격대별 호가 단위가 고정이다. 가격이 낮은 코인은
    한 틱이 이미 0.2~0.5%다. 이 경우 매수호가↔매도호가 왕복만으로 엣지를 전부 잃는다.
    (백테스트는 '체결가'로 계산하므로 이 비용이 빠져 있다)
 2) **현재 실제 스프레드** — 오더북 최우선 매수/매도호가 차이(%). 상위 호가 잔량도 함께 본다
    (30,000원 주문이 최우선 호가에서 다 채워지는지).

실행: .venv/bin/python backtesting/research/spread_check.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pyupbit

from backtesting.research import data_cache

ORDER_KRW = 30_000      # 전략당 배분(≈자본의 1/3) 기준 1회 주문 금액


def tick_size(price: float) -> float:
    """업비트 KRW 마켓 호가 단위 (가격대별)."""
    if price >= 2_000_000:
        return 1_000
    if price >= 1_000_000:
        return 500
    if price >= 500_000:
        return 100
    if price >= 100_000:
        return 50
    if price >= 10_000:
        return 10
    if price >= 1_000:
        return 1
    if price >= 100:
        return 0.1
    if price >= 10:
        return 0.01
    if price >= 1:
        return 0.001
    return 0.0001


def sample(markets: list[str], n: int = 8, interval: float = 15.0) -> dict[str, float]:
    """스프레드를 여러 번 재서 종목별 중앙값을 반환(1회 스냅샷은 표본이 아니다)."""
    import statistics
    acc: dict[str, list[float]] = {m: [] for m in markets}
    for i in range(n):
        try:
            books = pyupbit.get_orderbook(markets)
            if isinstance(books, dict):
                books = [books]
            for b in books:
                u = b["orderbook_units"][0]
                bid, ask = float(u["bid_price"]), float(u["ask_price"])
                acc[b["market"]].append((ask - bid) / ((ask + bid) / 2))
        except Exception as e:
            print(f"  샘플 {i+1} 실패: {e}")
        if i < n - 1:
            time.sleep(interval)
    return {m: statistics.median(v) for m, v in acc.items() if v}


def main() -> None:
    markets = data_cache.MARKETS_MID if "mid" in sys.argv else data_cache.MARKETS_LAB
    if "--sample" in sys.argv:
        import json
        n = 8
        print(f"스프레드 {n}회 샘플링 중(약 {n * 15 // 60}분)…")
        med = sample(markets, n=n)
        out = data_cache.DATA_DIR / ("spreads_mid.json" if "mid" in sys.argv else "spreads.json")
        out.write_text(json.dumps(med, indent=2))
        print(f"\n{'종목':7s} {'중앙 스프레드':>12s} {'왕복비용(수수료+스프레드)':>24s} {'거래가능':>8s}")
        for m, s in sorted(med.items(), key=lambda kv: kv[1]):
            print(f"{m.replace('KRW-',''):7s} {s:12.3%} {0.001 + s:24.3%} "
                  f"{'✅' if s <= 0.001 else '❌'}")
        tradable = [m for m, s in med.items() if s <= 0.001]
        print(f"\n스프레드 ≤0.10% 종목 {len(tradable)}/{len(med)}: "
              f"{', '.join(m.replace('KRW-','') for m in tradable)}")
        print(f"→ {out} 저장 (lab_verdict.py 가 종목별 슬리피지로 사용)")
        return
    books = pyupbit.get_orderbook(markets)
    if isinstance(books, dict):
        books = [books]
    print("실제 체결 비용 측정 · 업비트 KRW · 주문금액 기준 "
          f"{ORDER_KRW:,}원 · {time.strftime('%Y-%m-%d %H:%M')}")
    print("엣지 기준선: 왕복 슬리피지가 0.10%를 넘으면 후보 전략의 엣지는 소멸한다\n")
    print(f"{'종목':7s} {'현재가':>12s} {'틱':>9s} {'틱/가격':>8s} {'실제스프레드':>10s} "
          f"{'최우선잔량(매도)':>16s} {'판정':>6s}")
    rows = []
    for b in books:
        m = b["market"]
        unit = b["orderbook_units"][0]
        bid, ask = float(unit["bid_price"]), float(unit["ask_price"])
        mid = (bid + ask) / 2
        spread = (ask - bid) / mid
        tick = tick_size(mid)
        tick_pct = tick / mid
        ask_krw = float(unit["ask_price"]) * float(unit["ask_size"])
        ok = spread <= 0.001
        rows.append((m, spread, tick_pct, ask_krw >= ORDER_KRW))
        print(f"{m.replace('KRW-',''):7s} {mid:12,.2f} {tick:9,.4g} {tick_pct:8.3%} "
              f"{spread:10.3%} {ask_krw:15,.0f}원 {'✅' if ok else '❌'}")

    bad = [r[0].replace("KRW-", "") for r in rows if r[1] > 0.001]
    thin = [r[0].replace("KRW-", "") for r in rows if not r[3]]
    print(f"\n스프레드 0.10% 초과(엣지 소멸): {', '.join(bad) if bad else '없음'}")
    print(f"최우선 호가 잔량이 {ORDER_KRW:,}원 미달(추가 슬리피지 발생): "
          f"{', '.join(thin) if thin else '없음'}")
    avg = sum(r[1] for r in rows) / len(rows)
    print(f"평균 스프레드 {avg:.3%} → 왕복비용 추정 = 수수료 0.1% + 스프레드 {avg:.3%} "
          f"= {0.001 + avg:.3%}")
    print("\n※ 지정가(패시브) 주문으로 스프레드를 아끼려 하면 '가격이 불리하게 움직일 때만 체결되는'")
    print("  선택 편향이 생긴다 — 백테스트로는 잡히지 않는 비용이므로 실전에서 별도 확인해야 한다.")
    print("※ 1회 스냅샷이다. 변동성 큰 시간대(진입 조건이 켜지는 순간)의 스프레드가 더 넓을 수 있다.")


if __name__ == "__main__":
    main()

"""
스크리너 호가 스프레드 필터 테스트 (헌장 §3, v1.3).

거래대금 상위여도 스프레드가 넓으면(저가 코인의 호가 단위 문제) 제외해야 한다.
2026-07-26 실측: DOGE 0.930%, ADA 0.412% vs BTC 0.067% → 거래당 기댓값(0.18%)을 초과.
"""
from __future__ import annotations

import pytest

from config import charter as C
from data.screener import Screener, filter_by_spread, spread_ratio


def book(market: str, bid: float, ask: float, size: float = 1_000.0) -> dict:
    """size 기본값은 잔량 필터(30,000원)를 넉넉히 통과하는 값 — 스프레드만 검증하기 위함."""
    return {"market": market,
            "orderbook_units": [{"bid_price": bid, "ask_price": ask,
                                 "bid_size": size, "ask_size": size}]}


def ticker(market: str, turnover: float) -> dict:
    return {"market": market, "acc_trade_price_24h": turnover}


def test_spread_ratio_계산():
    assert spread_ratio({"bid_price": 999, "ask_price": 1001}) == pytest.approx(0.002)
    assert spread_ratio({"bid_price": 0, "ask_price": 0}) is None
    assert spread_ratio({"bid_price": "x", "ask_price": 1}) is None


def test_filter_by_spread_통과_탈락():
    books = [book("KRW-BTC", 99_990_000, 100_010_000),     # 0.02%
             book("KRW-DOGE", 100.0, 101.0)]               # 약 1%
    passed, measured = filter_by_spread(books, 0.001)
    assert passed == ["KRW-BTC"]
    assert measured["KRW-DOGE"] > 0.001


def test_filter_빈_오더북_무시():
    passed, measured = filter_by_spread([{"market": "KRW-X", "orderbook_units": []}], 0.001)
    assert passed == [] and measured == {}


class FakeScreener(Screener):
    """네트워크 대신 주입된 응답을 쓰는 테스트용 스크리너."""

    def __init__(self, tickers, books, **kw):
        super().__init__(**kw)
        self._tickers = tickers
        self._books = books
        self.orderbook_calls = 0

    def _fetch_tickers(self):
        return self._tickers

    def _fetch_orderbooks(self, markets):
        self.orderbook_calls += 1
        if self._books is None:
            raise RuntimeError("orderbook down")
        return [b for b in self._books if b["market"] in markets]


TICKERS = [ticker("KRW-DOGE", 9e11), ticker("KRW-ADA", 8e11),
           ticker("KRW-BTC", 7e11), ticker("KRW-ETH", 6e11),
           ticker("KRW-XRP", 5e11)]
BOOKS = [book("KRW-DOGE", 100.0, 101.0),               # 1.0%  → 탈락
         book("KRW-ADA", 240.0, 241.0),                # 0.4%  → 탈락
         book("KRW-BTC", 99_990_000, 100_010_000),      # 0.02% → 통과
         book("KRW-ETH", 2_749_000, 2_751_000),         # 0.07% → 통과
         book("KRW-XRP", 1_605.0, 1_606.0)]             # 0.06% → 통과


def test_거래대금_상위라도_스프레드_넓으면_제외():
    s = FakeScreener(TICKERS, BOOKS, top_n=3, min_turnover=0, notifier=None)
    assert s.eligible() == ["KRW-BTC", "KRW-ETH", "KRW-XRP"]
    assert set(s.rejected) == {"KRW-DOGE", "KRW-ADA"}


def test_거래대금_순서는_유지된다():
    s = FakeScreener(TICKERS, BOOKS, top_n=2, min_turnover=0)
    assert s.eligible() == ["KRW-BTC", "KRW-ETH"]      # BTC(7e11) > ETH(6e11)


def test_오더북_실패시_미확인종목으로_거래하지_않는다():
    """스프레드를 확인하지 못하면 폴백(BTC/ETH)으로 내려가야 한다 — 넓은 스프레드 종목 진입 금지."""
    s = FakeScreener(TICKERS, None, top_n=3, min_turnover=0,
                     fallback=["KRW-BTC", "KRW-ETH"])
    assert s.eligible() == ["KRW-BTC", "KRW-ETH"]


def test_오더북_실패시_직전_검증된_유니버스_유지():
    s = FakeScreener(TICKERS, BOOKS, top_n=3, min_turnover=0, refresh_sec=0)
    first = s.eligible()
    s._books = None                     # 이후 오더북 장애
    assert s.eligible() == first        # 직전(검증된) 캐시 유지


def test_후보를_넉넉히_받아서_top_n을_채운다():
    """스프레드로 탈락하는 종목이 있어도 top_n 을 채우려면 후보를 더 받아야 한다."""
    s = FakeScreener(TICKERS, BOOKS, top_n=3, min_turnover=0)
    s.eligible()
    assert C.SPREAD_CANDIDATE_MULT >= 2
    assert len(s.spreads) == 5          # 후보 3×MULT 만큼 조회 → 5종목 전부 측정됨


def test_헌장_상한값():
    assert C.MAX_SPREAD_RATIO == 0.001      # 0.1%
    assert C.MIN_TOP_DEPTH_KRW == 30_000    # 전략당 배분(자본 1/3) 소화 가능해야


def test_최우선호가_잔량_부족하면_제외():
    """스프레드가 좁아도 잔량이 주문금액보다 작으면 호가를 타고 올라가 더 비싸게 산다."""
    thin = book("KRW-THIN", 999.9, 1000.0, size=1.0)      # 잔량 1,000원
    deep = book("KRW-DEEP", 999.9, 1000.0, size=1_000.0)  # 잔량 100만원
    passed, measured = filter_by_spread([thin, deep], 0.001, min_depth_krw=30_000)
    assert passed == ["KRW-DEEP"]
    assert "KRW-THIN" in measured          # 측정은 됐지만 탈락


def test_잔량필터_미지정시_스프레드만_본다():
    thin = book("KRW-THIN", 999.9, 1000.0, size=1.0)
    passed, _ = filter_by_spread([thin], 0.001)
    assert passed == ["KRW-THIN"]


def test_스크리너가_잔량부족_종목도_제외():
    tickers = TICKERS + [ticker("KRW-THIN", 9.5e11)]
    books = BOOKS + [book("KRW-THIN", 999.9, 1000.0, size=1.0)]
    s = FakeScreener(tickers, books, top_n=3, min_turnover=0)
    picked = s.eligible()
    assert "KRW-THIN" not in picked
    assert "KRW-THIN" in s.rejected

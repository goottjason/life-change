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
    """네트워크 대신 주입된 응답을 쓰는 테스트용 스크리너.

    v2.1의 '검증 종목만' 제한은 여기서 검증 대상이 아니므로 해제한다
    (해당 규칙은 test_timeframe_rules.py 에서 검증).
    """

    def __init__(self, tickers, books, **kw):
        super().__init__(**kw)
        self._require_validated = False
        self._tickers = tickers
        self._books = books
        self.orderbook_calls = 0
        # 상장 경과일 필터(v1.5)는 여기서 검증 대상이 아니다 → 전부 '충분'으로 캐시해
        # 네트워크 조회를 타지 않게 한다(별도 테스트: test_listing_filter.py).
        for t in tickers:
            self._history_ok[t["market"]] = True

    def _fetch_tickers(self):
        return self._tickers

    def _validated_only(self, candidates):
        return candidates

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


# ── v1.7: 스프레드 안정성 (순간 스냅샷으로는 판단 불가) ──────
def test_스프레드_변동이_큰_종목은_제외():
    """
    실측: LPT 가 스냅샷 0.044% vs 8회 중앙값 0.245%. 진입 직전 확인은 진입 쪽만 보장하고
    청산 시점 스프레드는 알 수 없으므로, 가끔만 좁아지는 종목은 유니버스에서 빼야 한다.
    """
    s = FakeScreener(TICKERS, BOOKS, top_n=3, min_turnover=0)
    s._spread_hist["KRW-LPT"] = [0.0004, 0.0025, 0.0021, 0.0006]   # 중앙 0.0013 > 0.001
    assert s._spread_stable("KRW-LPT") is False


def test_꾸준히_좁은_종목은_통과():
    s = FakeScreener(TICKERS, BOOKS, top_n=3, min_turnover=0)
    s._spread_hist["KRW-BTC"] = [0.0003, 0.0004, 0.0003, 0.0005]
    assert s._spread_stable("KRW-BTC") is True


def test_최댓값이_상한의_2배_넘으면_제외():
    """중앙값은 통과해도 한 번이라도 크게 벌어지면 청산 비용 위험이 있다."""
    s = FakeScreener(TICKERS, BOOKS, top_n=3, min_turnover=0)
    s._spread_hist["KRW-X"] = [0.0004, 0.0005, 0.0004, 0.0030]     # 최대 0.30% > 0.1%×2
    assert s._spread_stable("KRW-X") is False


def test_표본_부족하면_판정_보류():
    """관측이 3개 미만이면 아직 판정하지 않는다(신규 편입 종목을 즉시 배제하지 않기 위해)."""
    s = FakeScreener(TICKERS, BOOKS, top_n=3, min_turnover=0)
    s._spread_hist["KRW-NEW"] = [0.0004, 0.0005]
    assert s._spread_stable("KRW-NEW") is True


def test_불안정_종목은_유니버스에서_빠진다():
    s = FakeScreener(TICKERS, BOOKS, top_n=3, min_turnover=0, refresh_sec=0)
    s.eligible()
    # ETH 만 과거에 크게 벌어진 이력을 심는다
    s._spread_hist["KRW-ETH"] = [0.0004, 0.0030, 0.0025, 0.0006]
    picked = s.eligible()
    assert "KRW-ETH" not in picked
    assert "KRW-ETH" in s.rejected_unstable


def test_안정성_기준값():
    assert C.SPREAD_HISTORY_LEN == 6 and C.SPREAD_HISTORY_MIN_SAMPLES == 3
    assert C.SPREAD_MAX_MULT == 2.0


def test_측정근거_블랙리스트():
    """
    차단 사유가 두 종류다:
    - 비용 초과(스프레드 중앙값 > 0.1%): LPT·ICP·AXS
    - 신호 부적합(스프레드는 통과하나 2년 백테스트 음의 기댓값): KAITO
    """
    assert {"LPT", "ICP", "AXS", "KAITO"} <= C.UNIVERSE_BLACKLIST
    s = Screener()
    assert "LPT" in s.exclude        # 스크리너 제외 집합에 반영


# ── v2.0: 투자경고/주의 제외 + 거래대금 하한 완화 ──────────
def test_투자경고_주의_종목_제외():
    """거래대금 하한이 어설프게 대리하던 위험(조작·급등락)을 플래그로 직접 차단한다."""
    from data.screener import flagged_markets
    detail = [
        {"market": "KRW-BTC", "market_event": {"warning": False, "caution": {}}},
        {"market": "KRW-WARN", "market_event": {"warning": True, "caution": {}}},
        {"market": "KRW-CAUT", "market_event": {"warning": False,
                                                "caution": {"PRICE_FLUCTUATIONS": True}}},
    ]
    assert flagged_markets(detail) == {"KRW-WARN", "KRW-CAUT"}


def test_플래그_조회실패는_다른_필터를_막지_않는다():
    class S(FakeScreener):
        def _fetch_markets_detail(self):
            raise RuntimeError("down")
    s = S(TICKERS, BOOKS, top_n=3, min_turnover=0)
    assert s.eligible() == ["KRW-BTC", "KRW-ETH", "KRW-XRP"]


def test_거래대금_하한_완화값():
    """30억·100억은 근거 없는 값이었다. 실측 통과: 30억 7개 → 1억 18개."""
    assert C.MIN_TURNOVER_24H_KRW == 100_000_000
    assert C.EXCLUDE_MARKET_WARNING and C.EXCLUDE_MARKET_CAUTION

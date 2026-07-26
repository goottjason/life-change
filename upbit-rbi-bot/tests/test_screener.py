"""거래대금 상위 동적 유니버스 스크리너 (헌장 v1.2 §3)."""
from data.screener import select_universe, Screener


def _t(market, turnover):
    return {"market": market, "acc_trade_price_24h": turnover}


def test_ranks_by_turnover_and_caps_top_n():
    tickers = [_t("KRW-BTC", 100), _t("KRW-ETH", 80),
               _t("KRW-SOL", 60), _t("KRW-XRP", 40)]
    assert select_universe(tickers, top_n=2, min_turnover=0, exclude=set()) \
        == ["KRW-BTC", "KRW-ETH"]


def test_applies_turnover_floor():
    tickers = [_t("KRW-BTC", 100), _t("KRW-DOGE", 5)]
    assert select_universe(tickers, top_n=10, min_turnover=10, exclude=set()) \
        == ["KRW-BTC"]


def test_excludes_stablecoins_and_non_krw():
    tickers = [_t("KRW-BTC", 100), _t("KRW-USDT", 999), _t("BTC-ETH", 999)]
    assert select_universe(tickers, top_n=10, min_turnover=0, exclude={"USDT"}) \
        == ["KRW-BTC"]


def test_eligible_falls_back_when_fetch_fails():
    class S(Screener):
        def _fetch_tickers(self):
            raise RuntimeError("network down")
    s = S(fallback=("KRW-BTC", "KRW-ETH"))
    assert s.eligible() == ["KRW-BTC", "KRW-ETH"]


def test_eligible_fetches_then_caches():
    """
    캐시 동작만 검증한다. 스프레드·잔량·상장일 필터(v1.3~v1.7)는 네트워크를 타므로
    여기서는 모두 통과시켜 격리한다(각각 test_spread_filter/test_listing_filter 에서 검증).
    """
    class S(Screener):
        calls = 0
        def _fetch_tickers(self):
            type(self).calls += 1
            return [_t("KRW-BTC", 100), _t("KRW-ETH", 50), _t("KRW-USDT", 999)]
        def _apply_history_filter(self, markets):
            return markets
        def _apply_spread_filter(self, candidates):
            return candidates
    s = S(top_n=2, min_turnover=0, refresh_sec=999, exclude={"USDT"})
    assert s.eligible() == ["KRW-BTC", "KRW-ETH"]
    s.eligible()
    assert S.calls == 1   # refresh_sec 이내 → 캐시 사용


def test_eligible_warns_once_on_fallback_transition():
    class FakeNotifier:
        def __init__(self):
            self.messages = []
        def send(self, msg):
            self.messages.append(msg)

    class S(Screener):
        def _fetch_tickers(self):
            raise RuntimeError("network down")

    notifier = FakeNotifier()
    s = S(fallback=("KRW-BTC", "KRW-ETH"), refresh_sec=0, notifier=notifier)
    s.eligible()
    s.eligible()
    fallback_msgs = [m for m in notifier.messages if "폴백" in m]
    assert len(fallback_msgs) == 1

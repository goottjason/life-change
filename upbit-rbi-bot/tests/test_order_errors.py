"""
주문 실패 원인이 reason 에 사람이 읽을 수 있게 기록되는지 검증 (Task C).
업비트 에러 응답: {'error': {'name': 'insufficient_funds_bid', 'message': '...'}}
"""
from __future__ import annotations

from data.upbit_client import UpbitClient, OrderResult


def test_parse_extracts_upbit_error_name_and_message():
    resp = {"error": {"name": "insufficient_funds_bid",
                      "message": "주문가능한 금액이 부족합니다."}}
    res = UpbitClient._parse(resp)
    assert not res.ok
    assert "insufficient_funds_bid" in res.error
    assert "부족" in res.error


def test_parse_handles_none_response():
    res = UpbitClient._parse(None)
    assert not res.ok
    assert res.error  # 빈 문자열이 아니어야 함(원인 추적 가능)


def test_parse_success_has_order_id():
    res = UpbitClient._parse({"uuid": "abc-123", "state": "wait"})
    assert res.ok
    assert res.order_id == "abc-123"


class _FakeSettings:
    dry_run = False


def test_buy_limit_catches_exception_as_error(monkeypatch):
    """API 예외가 봇 루프로 전파되지 않고 OrderResult(ok=False) 로 잡혀야 한다."""
    c = UpbitClient()   # dry_run=True 로 생성(키 불필요)
    monkeypatch.setattr("data.upbit_client.settings", _FakeSettings())  # 이후 실주문 경로 강제

    # v2.7: pyupbit 의 buy_limit_order 는 예외를 삼키고 None 을 돌려주므로 쓰지 않는다.
    # 인증 헤더 생성만 빌려 쓰고 POST 는 직접 하므로, 예외는 여기서 난다.
    class Boom:
        def _request_headers(self, *a, **k):
            raise RuntimeError("network down")

    c._upbit = Boom()
    res = c.buy_limit("KRW-BTC", 100.0, 30_000)
    assert not res.ok
    assert "network down" in res.error
    assert "RuntimeError" in res.error, "예외 타입도 남아야 원인 추적이 된다"

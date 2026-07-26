"""
모의 모드 강제 게이트 테스트 (헌장 §9.7, v1.3).

전략 로직이 바뀐 코드가 배포됐을 때 이전 승인으로 실계좌가 계속 도는 것이 가장 위험하다.
그래서 DRY_RUN=false 만으로는 실거래가 시작되지 않고, `LIVE_CHARTER_ACK=<현재 헌장버전>`
까지 있어야 한다.
"""
from __future__ import annotations

import pytest

from config.charter import CHARTER_VERSION
from config.settings import _resolve_dry_run, forced_paper_reason


@pytest.fixture
def env(monkeypatch):
    def _set(dry_run=None, ack=None):
        for k, v in (("DRY_RUN", dry_run), ("LIVE_CHARTER_ACK", ack)):
            monkeypatch.delenv(k, raising=False)
            if v is not None:
                monkeypatch.setenv(k, v)
    return _set


def test_기본값은_모의(env):
    env()
    assert _resolve_dry_run() is True
    assert forced_paper_reason() == ""


def test_DRY_RUN_true면_모의(env):
    env(dry_run="true", ack=CHARTER_VERSION)
    assert _resolve_dry_run() is True


def test_승인없이_실거래_요청하면_모의로_강제(env):
    env(dry_run="false")
    assert _resolve_dry_run() is True
    assert "LIVE_CHARTER_ACK 미설정" in forced_paper_reason()


def test_이전_헌장버전_승인은_무효(env):
    env(dry_run="false", ack="v1.2")
    assert _resolve_dry_run() is True
    reason = forced_paper_reason()
    assert "v1.2" in reason and CHARTER_VERSION in reason


def test_현재_헌장버전_승인시_실거래(env):
    env(dry_run="false", ack=CHARTER_VERSION)
    assert _resolve_dry_run() is False
    assert forced_paper_reason() == ""


def test_공백_섞인_승인값도_허용(env):
    env(dry_run="false", ack=f"  {CHARTER_VERSION} ")
    assert _resolve_dry_run() is False

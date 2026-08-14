"""
실거래 승인 게이트 (§9.7) — 전략이 바뀌면 승인이 무효화되는가.

실제로 뚫렸던 구멍: 2026-07-30, easy_teaching 을 ACTIVE_STRATEGIES 에 새로 넣고 스펙을
두 번 고치는 동안 CHARTER_VERSION 은 "v2.5" 그대로였다. 서버의 LIVE_CHARTER_ACK=v2.5 가
그대로 통과해 **§11 백테스트 0건짜리 전략이 실계좌에서 돌았다**.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib

from config import charter as C


def test_지문은_헌장버전을_포함한다():
    assert C.charter_fingerprint().startswith(C.CHARTER_VERSION + "-")


def test_같은_설정이면_지문이_같다():
    assert C.charter_fingerprint() == C.charter_fingerprint()


def test_가동전략이_늘면_지문이_바뀐다(monkeypatch):
    """이번 사고의 재발 방지 — 새 전략을 켜면 재승인이 필요해야 한다."""
    before = C.charter_fingerprint()
    monkeypatch.setattr(C, "ACTIVE_STRATEGIES", ("rsi2", "rsi2_15m", "easy_teaching"))
    assert C.charter_fingerprint() != before


def test_가동전략의_스펙이_바뀌면_지문이_바뀐다(monkeypatch):
    """손절폭·시간손절 같은 매매 규칙이 바뀌어도 재승인이 필요하다."""
    before = C.charter_fingerprint()
    specs = dict(C.STRATEGY_SPECS)
    from dataclasses import replace
    specs["rsi2"] = replace(specs["rsi2"], stop_pct=0.05)
    monkeypatch.setattr(C, "STRATEGY_SPECS", specs)
    assert C.charter_fingerprint() != before


def test_가동하지_않는_전략의_스펙은_지문에_영향이_없다(monkeypatch):
    """easy_teaching 을 고쳐도, 꺼져 있는 한 실거래 승인은 유효하다."""
    before = C.charter_fingerprint()
    specs = dict(C.STRATEGY_SPECS)
    from dataclasses import replace
    specs["easy_teaching"] = replace(specs["easy_teaching"], time_stop_bars=999)
    monkeypatch.setattr(C, "STRATEGY_SPECS", specs)
    assert C.charter_fingerprint() == before


def test_승인값이_지문과_다르면_모의로_강제된다(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("LIVE_CHARTER_ACK", "v2.5-deadbeef")
    from config import settings as S
    importlib.reload(S)
    assert S._resolve_dry_run() is True
    assert "재승인 필요" in S.forced_paper_reason()


def test_승인값이_지문과_같으면_실거래(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("LIVE_CHARTER_ACK", C.charter_fingerprint())
    from config import settings as S
    importlib.reload(S)
    assert S._resolve_dry_run() is False
    assert S.forced_paper_reason() == ""


def test_구버전_승인값은_더이상_통과하지_않는다(monkeypatch):
    """
    버전 문자열만으로 통과시키면 그 값이 스펙 변경과 무관하게 영원히 유효해져
    이 장치가 다시 무의미해진다 — 정확히 이번에 뚫린 구멍이다.
    """
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("LIVE_CHARTER_ACK", C.CHARTER_VERSION)
    from config import settings as S
    importlib.reload(S)
    assert S._resolve_dry_run() is True


# ── §11 검증 단계가 코드에 새겨져 있는가 ─────────────────────
def test_가동전략은_검증·인큐베이션·실험_중_하나여야_한다():
    """
    easy_teaching 은 §11 백테스트도 인큐베이션도 없이 **자본의 1/3 로** 실계좌에 들어갔다.
    그 경로를 막는다. 새 전략을 켜려면 VALIDATED / INCUBATING / EXPERIMENTAL(v4.0) 중
    하나에 먼저 올려야 하고, EXPERIMENTAL 은 주문금액 상한이 코드로 강제되어야 한다 —
    '검증 없이 크게'가 사고였지 '작게 실험'까지 막자는 게 아니다.
    """
    allowed = (C.VALIDATED_STRATEGIES | C.INCUBATING_STRATEGIES
               | C.EXPERIMENTAL_STRATEGIES)
    unproven = set(C.ACTIVE_STRATEGIES) - allowed
    assert not unproven, f"§11 근거 없이 가동 중인 전략: {unproven}"
    # 실험 트랙은 반드시 상한이 강제된다 (사이징이 얼마를 내놓든 EXPERIMENT_MAX_ORDER_KRW)
    for name in C.EXPERIMENTAL_STRATEGIES:
        assert C.position_cap_for(name, 1e9) == C.EXPERIMENT_MAX_ORDER_KRW


def test_easy_teaching은_아직_검증전략이_아니다():
    """백테스트 518거래 승률 26.6% PF 0.58 t −4.04 — §11 미통과."""
    assert "easy_teaching" not in C.VALIDATED_STRATEGIES
    assert "easy_teaching" not in C.ACTIVE_STRATEGIES


def test_인큐베이션_전략은_최소주문금액으로_묶인다(monkeypatch):
    monkeypatch.setattr(C, "INCUBATING_STRATEGIES", frozenset({"easy_teaching"}))
    assert C.position_cap_for("easy_teaching", 30_000) == C.MIN_ORDER_KRW
    assert C.position_cap_for("rsi2", 30_000) == 30_000

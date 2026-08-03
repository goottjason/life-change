"""
런타임 설정: 환경변수(.env) 로드 + 운영 파라미터.
매매 규칙 상수는 charter.py 에 있고, 여기서는 '환경/인프라' 설정만 다룬다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from config.charter import CHARTER_VERSION, charter_fingerprint

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # python-dotenv 미설치 시에도 동작
    pass


def _live_ack() -> str:
    return os.getenv("LIVE_CHARTER_ACK", "").strip()


def _resolve_dry_run() -> bool:
    """
    모의/실거래 판정 (§9.7, v1.3).

    DRY_RUN=false 만으로는 실거래가 시작되지 않는다. 매매 규칙(헌장)이 개정되면
    **그 버전을 명시적으로 승인**해야 한다: 환경변수 `LIVE_CHARTER_ACK=<헌장버전>`.
    승인이 없거나 버전이 다르면 자동으로 모의 모드로 강제한다.

    이유: 전략 교체(v1.3: macd/rsi/cvd → rsi2)처럼 매매 로직이 바뀐 코드가 배포되면
    이전 승인으로 실계좌가 계속 돌아가는 것이 가장 위험하다. 새 규칙은 모의로 먼저 검증한다.

    v2.7 — 비교 대상이 버전 문자열에서 **지문(charter_fingerprint)** 으로 바뀌었다.
    버전만 보던 시절, easy_teaching 을 새로 켜면서 CHARTER_VERSION 을 안 올리는 바람에
    검증 한 번 없는 전략이 이전 승인으로 실계좌에서 돌았다. 이제 가동 전략이나 그 스펙이
    바뀌면 사람이 버전을 잊어도 승인이 자동 무효화된다.

    ⚠ 버전 문자열만 넣는 구버전 승인("v2.5")은 **더 이상 통과하지 않는다**. 통과시키면
      그 값이 스펙 변경과 무관하게 영원히 유효해져 이 장치가 다시 무의미해진다.
      → 이 코드를 배포하면 서버는 모의 모드로 강제된다. 실거래를 이어가려면 서버 .env 의
        LIVE_CHARTER_ACK 을 charter_fingerprint() 값으로 갱신해야 한다(의도된 동작이다).
    """
    if os.getenv("DRY_RUN", "true").lower() == "true":
        return True
    return not _ack_matches()


def _ack_matches() -> bool:
    return _live_ack() == charter_fingerprint()


def forced_paper_reason() -> str:
    """실거래를 요청했는데 모의로 강제된 경우의 사유(알림용). 아니면 빈 문자열."""
    if os.getenv("DRY_RUN", "true").lower() == "true":
        return ""
    if _ack_matches():
        return ""
    fp = charter_fingerprint()
    ack = _live_ack()
    if not ack:
        return (f"LIVE_CHARTER_ACK 미설정 — 헌장 {fp} 규칙을 모의로 검증 후 "
                f"`LIVE_CHARTER_ACK={fp}` 설정 시 실거래 전환")
    return (f"LIVE_CHARTER_ACK={ack} 이지만 현재 헌장 지문은 {fp} — "
            f"헌장 또는 **가동 전략 스펙**이 바뀌었으므로 모의로 강제 (재승인 필요)")


@dataclass(frozen=True)
class Settings:
    # 업비트 Open API 키 (.env) — 헌장 §9.6: 출금 권한 미부여, 절대 커밋 금지
    upbit_access_key: str = os.getenv("UPBIT_ACCESS_KEY", "")
    upbit_secret_key: str = os.getenv("UPBIT_SECRET_KEY", "")

    # 텔레그램 알림 (헌장 §9.5, §10.2)
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # 거래 유니버스 (헌장 §1: 고유동성 코인 한정)
    universe: tuple[str, ...] = ("KRW-BTC", "KRW-ETH")

    # 모의(DRY_RUN) 및 계좌조회 실패 시 사용할 폴백 자본. 실전은 계좌 잔고를 읽는다.
    paper_capital_krw: float = float(os.getenv("PAPER_CAPITAL_KRW", "90000"))

    # 운영 모드 — DRY_RUN=false + LIVE_CHARTER_ACK=<헌장버전> 둘 다여야 실거래 (§9.7)
    dry_run: bool = _resolve_dry_run()
    loop_interval_sec: int = int(os.getenv("LOOP_INTERVAL_SEC", "10"))
    feed_stale_sec: int = int(os.getenv("FEED_STALE_SEC", "60"))    # 피드 감시 (§9.4)

    # 로깅
    log_dir: str = os.getenv("LOG_DIR", "logs")
    db_path: str = os.getenv("DB_PATH", "logs/trades.sqlite")

    def validate(self) -> None:
        if not self.dry_run and (not self.upbit_access_key or not self.upbit_secret_key):
            raise RuntimeError("실거래 모드인데 업비트 API 키가 없습니다 (.env 확인).")


settings = Settings()

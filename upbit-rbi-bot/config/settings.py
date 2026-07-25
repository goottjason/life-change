"""
런타임 설정: 환경변수(.env) 로드 + 운영 파라미터.
매매 규칙 상수는 charter.py 에 있고, 여기서는 '환경/인프라' 설정만 다룬다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # python-dotenv 미설치 시에도 동작
    pass


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

    # 운영 모드
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() == "true"  # 기본은 모의(안전)
    loop_interval_sec: int = int(os.getenv("LOOP_INTERVAL_SEC", "10"))
    feed_stale_sec: int = int(os.getenv("FEED_STALE_SEC", "60"))    # 피드 감시 (§9.4)

    # 로깅
    log_dir: str = os.getenv("LOG_DIR", "logs")
    db_path: str = os.getenv("DB_PATH", "logs/trades.sqlite")

    def validate(self) -> None:
        if not self.dry_run and (not self.upbit_access_key or not self.upbit_secret_key):
            raise RuntimeError("실거래 모드인데 업비트 API 키가 없습니다 (.env 확인).")


settings = Settings()

"""
시간대 유틸 — 모든 사용자 표기는 KST(Asia/Seoul, UTC+9)로 통일한다.
KST 는 DST가 없어 고정 오프셋(+9)으로 안전하게 계산한다(추가 의존성 불필요).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), name="KST")


def now_kst() -> datetime:
    return datetime.now(KST)


def now_kst_iso(timespec: str = "seconds") -> str:
    return now_kst().isoformat(timespec=timespec)

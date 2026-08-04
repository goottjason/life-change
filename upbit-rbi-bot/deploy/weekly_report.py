"""
주간 인큐베이션 리포트 발송 (헌장 §10.3 주간 리뷰 + §11-3).

rsi2 계열이 실전에서 백테스트를 재현하는지 **운영자가 아무것도 안 해도** 알 수 있게,
주 1회 텔레그램으로 진행 리포트를 보낸다. 운영자는 숫자를 계산할 필요 없이 판정만 보면 된다.

서버 등록 (매주 월요일 09:00 KST):
    crontab -e
    0 9 * * 1 cd ~/projects/life-change && docker compose exec -T life-change \\
        python deploy/weekly_report.py >> ~/weekly_report.log 2>&1

수동 실행: docker compose exec life-change python deploy/weekly_report.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from incubation.progress import report, format_text          # noqa: E402
from safety.notifier import TelegramNotifier                  # noqa: E402


def main() -> int:
    rep = report()
    text = format_text(rep)
    TelegramNotifier().send(text)
    print(text)
    # 판정이 '중단 검토'면 종료코드 1 → cron 로그/모니터링에서 눈에 띈다
    return 1 if "중단" in rep["verdict"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

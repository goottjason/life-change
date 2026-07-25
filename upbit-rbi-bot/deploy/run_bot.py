"""
봇 실행 엔트리 (헌장 §11 인큐베이션).
    python deploy/run_bot.py
기본은 DRY_RUN(모의). 실거래는 .env 에서 DRY_RUN=false + API 키 설정.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings          # noqa: E402
from bot.trader import Trader                  # noqa: E402


def main() -> None:
    trader = Trader()
    trader.boot()
    print(f"루프 시작 (interval={settings.loop_interval_sec}s). Ctrl+C 로 종료.")
    try:
        while True:
            trader.tick()
            time.sleep(settings.loop_interval_sec)
    except KeyboardInterrupt:
        print("\n종료 신호 — 킬 스위치로 안전 청산합니다 (§9.1).")
        trader.failsafe.kill_switch(
            list(trader.positions.values()),
            get_price=trader.client.get_price,
        )


if __name__ == "__main__":
    main()

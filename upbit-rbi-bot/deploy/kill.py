"""
킬 스위치 독립 실행 (헌장 §9.1).
    python deploy/kill.py
봇 프로세스와 별개로, 즉시 전 포지션을 시장가 청산한다(긴급용).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.upbit_client import UpbitClient      # noqa: E402
from safety.notifier import TelegramNotifier   # noqa: E402


def main() -> None:
    client = UpbitClient()
    notifier = TelegramNotifier()
    balances = client.get_balances()
    krw_positions = [b for b in balances if b.get("currency") != "KRW"
                     and float(b.get("balance", 0)) > 0]
    for b in krw_positions:
        market = f"KRW-{b['currency']}"
        vol = float(b["balance"])
        client.sell_market(market, vol)
        print(f"청산: {market} {vol}")
    notifier.send(f"🛑 수동 킬 스위치 — {len(krw_positions)}개 포지션 시장가 청산 (§9.1)")


if __name__ == "__main__":
    main()

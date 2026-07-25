"""
백테스트 실행 (헌장 §11).
    python deploy/run_backtest.py
업비트 캔들로 각 전략을 수수료 반영해 검증하고 통과 여부를 출력한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings          # noqa: E402
from config.charter import BASE_TIMEFRAME     # noqa: E402
from data.upbit_client import UpbitClient     # noqa: E402
from bot.trader import build_strategies       # noqa: E402
from backtesting import engine                # noqa: E402


def main() -> None:
    client = UpbitClient()
    strategies = build_strategies()
    for market in settings.universe:
        print(f"\n=== {market} ({BASE_TIMEFRAME}) ===")
        df = client.get_candles(market, count=200)   # 표본 확대는 candles 수집기로
        for name, strat in strategies.items():
            result = engine.run(strat, df)
            print(f"  {name:5s} {result.summary()}")


if __name__ == "__main__":
    main()

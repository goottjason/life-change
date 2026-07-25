"""
메인 매매 루프 (헌장 전 조항의 조립점).
실행 순서(§14): 안전장치 → 서킷 브레이커 → 사이징 → 주문집행.

  for each tick:
    0. 안전장치: 피드 살아있나? 정지 상태 아닌가? (§9, §5.4)
    1. 보유 포지션 청산 판정: TP/SL(§4.1~2) → 죽은포지션(§4-A) → 역방향(§4.3)
    2. 레짐 판별(§8) → 활성 전략만
    3. 진입 신호(§3) + can_enter(§5) 통과 시 사이징(§7) → 주문(§6)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from config.settings import settings
from config.charter import STRATEGY_SPECS
from data.upbit_client import UpbitClient
from bot.order_manager import OrderManager
from bot.risk_manager import RiskManager
from bot.position import Position, ExitReason
from bot import dead_position
from strategies.base import BaseStrategy, Action
from strategies.macd import MacdStrategy
from strategies.rsi_mean_reversion import RsiMeanReversionStrategy
from strategies.cvd import CvdStrategy
from strategies.regime import detect_regime, is_strategy_active
from indicators import ta
from safety.failsafe import Failsafe
from safety.notifier import TelegramNotifier
from incubation.logger import TradeLogger


def build_strategies() -> dict[str, BaseStrategy]:
    return {
        "macd": MacdStrategy(STRATEGY_SPECS["macd"]),
        "rsi": RsiMeanReversionStrategy(STRATEGY_SPECS["rsi"]),
        "cvd": CvdStrategy(STRATEGY_SPECS["cvd"]),
    }


class Trader:
    def __init__(self):
        self.client = UpbitClient()
        self.orders = OrderManager(self.client)
        self.risk = RiskManager()
        self.strategies = build_strategies()
        self.notifier = TelegramNotifier()
        self.failsafe = Failsafe(self.client, self.orders, self.notifier)
        self.logger = TradeLogger()
        self.positions: dict[str, Position] = {}   # key: strategy name (전략당 1포지션 §3.4)
        self.last_prices: dict[str, float] = {}    # 대시보드용 최신가 캐시
        self.regimes: dict[str, str] = {}          # 대시보드용 레짐 캐시

    # ── 부팅 (§9.3 상태 복구) ────────────────────────────────
    def boot(self) -> None:
        settings.validate()
        self.failsafe.recover_state()
        self.notifier.send(f"🤖 봇 시작 (dry_run={settings.dry_run})")

    # ── 1 tick ───────────────────────────────────────────────
    def tick(self) -> None:
        # 0. 안전장치
        if self.risk.s.halted:
            return
        self.failsafe.heartbeat.beat()
        if not self.failsafe.check_feed():
            return

        for market in settings.universe:
            try:
                df = self.client.get_candles(market)
            except Exception as e:
                self.failsafe.on_api_error(e)
                continue
            self._process_market(market, df)

    def _process_market(self, market: str, df: pd.DataFrame) -> None:
        price = df["close"].iloc[-1]
        regime = detect_regime(df)
        self.last_prices[market] = float(price)     # 대시보드 캐시
        self.regimes[market] = regime.value

        # 1. 보유 포지션 청산 판정
        for name, pos in list(self.positions.items()):
            if pos.market != market:
                continue
            pos.update_high(price)
            reason = pos.check_price_exit(price)           # TP/SL (§4.1~2)
            if reason == ExitReason.NONE:
                dead, why = dead_position.is_dead(pos, df)  # §4-A
                if dead:
                    reason = ExitReason.DEAD_POSITION
                else:
                    sig = self.strategies[name].signal(df)  # 역방향 (§4.3)
                    if sig.action == Action.EXIT:
                        reason = ExitReason.REVERSE_SIGNAL
            if reason != ExitReason.NONE:
                self._close(pos, price, reason, note=why if reason == ExitReason.DEAD_POSITION else reason.value)

        # 2~3. 진입: 레짐 활성 전략만 (§8) + 리스크 통과(§5) + 사이징(§7)
        for name, strat in self.strategies.items():
            if name in self.positions:            # 전략당 1포지션 (§3.4)
                continue
            if not is_strategy_active(strat.regime, regime):
                continue
            ok, _ = self.risk.can_enter()
            if not ok:
                continue
            sig = strat.signal(df)
            if sig.action != Action.ENTER_LONG:
                continue
            if self._is_duplicate(market):         # 동일코인 중복 금지 (§3.3)
                continue
            self._open(name, strat, market, price, df)

    # ── 진입/청산 실행 ───────────────────────────────────────
    def _open(self, name: str, strat: BaseStrategy, market: str,
              price: float, df: pd.DataFrame) -> None:
        krw = self.risk.size_for(strat.spec.stop_loss)     # §7.2
        res = self.orders.enter_long(market, price, krw)   # §6
        if not res.ok:
            self.logger.log("entry_fail", strategy=name, market=market, reason=res.error)
            return
        entry_atr = float(ta.atr(df).iloc[-1]) if len(df) >= 14 else 0.0
        self.positions[name] = Position(
            strategy=name, market=market, entry_price=res.avg_price or price,
            size_krw=krw, volume=res.filled_volume, entry_time=df.index[-1],
            entry_atr=entry_atr,
        )
        self.risk.on_open()
        self.logger.log("entry", strategy=name, market=market, price=price,
                        volume=res.filled_volume, size_krw=krw, reason=strat.signal(df).reason)

    def _close(self, pos: Position, price: float, reason: ExitReason, note: str = "") -> None:
        res = self.orders.exit_position(pos, price, reason)    # §6
        fill_price = res.avg_price or price
        pnl = pos.pnl_krw(fill_price)
        self.risk.on_close(pnl)                                 # §5 상태 갱신
        self.positions.pop(pos.strategy, None)
        self.logger.log("exit", strategy=pos.strategy, market=pos.market,
                        price=fill_price, volume=pos.volume, pnl_krw=pnl,
                        reason=f"{reason.value}:{note}")
        # 서킷 브레이커 발동 알림
        ok, why = self.risk.can_enter()
        if not ok:
            self.notifier.send(f"⛔ 신규 진입 차단: {why}")

    def _is_duplicate(self, market: str) -> bool:
        return any(p.market == market for p in self.positions.values())

    # ── 대시보드용 상태 스냅샷 (헌장 §10 관찰) ────────────────
    def snapshot(self) -> dict:
        s = self.risk.s
        equity = s.equity_high + s.daily_pnl
        drawdown = (s.equity_high - equity) / s.equity_high if s.equity_high else 0.0
        positions = []
        for name, pos in list(self.positions.items()):
            price = self.last_prices.get(pos.market, pos.entry_price)
            positions.append({
                "strategy": name,
                "market": pos.market,
                "entry_price": pos.entry_price,
                "current_price": price,
                "size_krw": pos.size_krw,
                "pnl_krw": round(pos.pnl_krw(price)),
                "pnl_pct": round(pos.pnl_ratio(price) * 100, 2),
                "sl": -pos.spec.stop_loss * 100,
                "tp": pos.spec.take_profit * 100,
            })
        return {
            "risk": {
                "daily_pnl": round(s.daily_pnl),
                "consecutive_losses": s.consecutive_losses,
                "open_positions": s.open_positions,
                "equity": round(equity),
                "equity_high": round(s.equity_high),
                "drawdown_pct": round(drawdown * 100, 2),
                "halted": s.halted,
                "halt_reason": s.halt_reason,
            },
            "can_enter": self.risk.can_enter()[1],
            "positions": positions,
            "regimes": dict(self.regimes),
            "prices": {m: round(p) for m, p in self.last_prices.items()},
        }

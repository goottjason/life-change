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
from config import charter as C
from config.charter import STRATEGY_SPECS
from config.timeutil import now_kst
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
        self._recover_positions()
        self.notifier.send(f"🤖 봇 시작 (dry_run={settings.dry_run})")

    def _recover_positions(self) -> None:
        """
        재시작 시 실계좌 잔고를 읽어 '봇이 모르는 보유 코인(오펀)'을 포지션으로 복원한다 (§9.3).
        진입가는 avg_buy_price, 진입시각은 최근 매수체결 시각(없으면 현재봉)으로 채운다.
        복원된 포지션은 빈 전략 슬롯에 배정되어 TP/SL·죽은포지션 관리를 받는다.
        """
        known = {p.market for p in self.positions.values()}
        orphans = self.failsafe.recover_state(known_markets=known)
        for o in orphans:
            slot = next((n for n in self.strategies if n not in self.positions), None)
            if slot is None:
                self.notifier.send(
                    f"⚠️ 오펀 코인 {o['market']} 복원 실패: 빈 전략 슬롯 없음 — 수동 확인 필요")
                continue
            # pyupbit 캔들 인덱스는 tz-naive KST 이므로 entry_time 도 동일 기준으로 맞춘다.
            if o.get("entry_time"):
                entry_time = pd.Timestamp(o["entry_time"] + 9 * 3600, unit="s")  # epoch→KST naive
            else:
                entry_time = pd.Timestamp(now_kst().replace(tzinfo=None))
            self.positions[slot] = Position(
                strategy=slot, market=o["market"], entry_price=o["avg_price"],
                size_krw=o["avg_price"] * o["volume"], volume=o["volume"],
                entry_time=entry_time, entry_atr=0.0,
            )
            self.risk.on_open()
            self.notifier.send(
                f"🔧 오펀 복원: {o['market']} {o['volume']:.8f}개 @ {o['avg_price']:.0f} "
                f"→ '{slot}' 슬롯에서 관리 (§9.3)")
            self.logger.log("recover", strategy=slot, market=o["market"],
                            price=o["avg_price"], volume=o["volume"],
                            size_krw=o["avg_price"] * o["volume"], reason="orphan_recovered")

    # ── 1 tick ───────────────────────────────────────────────
    def tick(self) -> None:
        # 0. 안전장치
        if self.risk.s.halted:
            return
        self.failsafe.heartbeat.beat()
        if not self.failsafe.check_feed():
            return

        # 자본 갱신: 실계좌 잔고(총 자산)를 읽어 사이징·서킷의 기준으로 삼는다 (§7.1)
        try:
            avail, equity = self.client.get_account_equity(self.last_prices.get)
            self.risk.update_capital(avail, equity)
        except Exception as e:
            self.failsafe.on_api_error(e)

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
        # 잔고 부족 등으로 주문금액이 최소주문금액 미만이면 조용히 스킵(로그 스팸 방지)
        if krw < C.MIN_ORDER_KRW:
            return
        res = self.orders.enter_long(market, price, krw)   # §6
        if not res.ok or res.filled_volume <= 0:
            self.logger.log("entry_fail", strategy=name, market=market,
                            reason=res.error or "no fill")
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
        filled = res.filled_volume

        # 청산 실패(체결 0) → 포지션 유지. exit 로 기록하지 않는다(오펀 desync 방지).
        if not res.ok or filled <= 0:
            self.logger.log("exit_fail", strategy=pos.strategy, market=pos.market,
                            volume=pos.volume, reason=f"{reason.value}:{res.error}")
            self.notifier.send(
                f"⚠️ 청산 실패 {pos.market} ({reason.value}): {res.error or '체결 0'} "
                f"— 포지션 유지, 다음 tick 재시도")
            return

        # 부분 체결 판정. 남은 잔량의 평가액이 최소주문금액 미만이면 '먼지'로 보고 청산 완료 처리
        # (거래 불가능한 잔량으로 매 tick 무한 재청산·로그 스팸 방지).
        fill_price = res.avg_price or price
        remaining = pos.volume - filled
        if remaining > pos.volume * 1e-9 and remaining * fill_price >= C.MIN_ORDER_KRW:
            pos.volume = remaining
            pos.size_krw = pos.entry_price * pos.volume
            self.logger.log("exit_partial", strategy=pos.strategy, market=pos.market,
                            price=fill_price, volume=filled,
                            reason=f"{reason.value}:부분체결 잔량 {pos.volume:.8f}")
            self.notifier.send(
                f"⚠️ 부분 청산 {pos.market}: {filled:.8f} 체결, 잔량 {pos.volume:.8f} 유지")
            return

        fill_price = res.avg_price or price
        pnl = pos.pnl_krw(fill_price)
        self.risk.on_close(pnl)                                 # §5 상태 갱신
        self.positions.pop(pos.strategy, None)
        self.logger.log("exit", strategy=pos.strategy, market=pos.market,
                        price=fill_price, volume=filled, pnl_krw=pnl,
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
        equity = s.capital  # 실계좌 총 자산 (§7.1)
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
                "available_krw": round(s.available_krw),
                "max_loss_per_trade": round(C.max_loss_per_trade_krw(s.capital)),
                "daily_loss_limit": round(C.daily_loss_limit_krw(s.capital)),
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

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

from config.settings import settings, forced_paper_reason
from config import charter as C
from config.charter import STRATEGY_SPECS
from config.timeutil import now_kst
from data.upbit_client import UpbitClient
from data.screener import Screener
from bot.order_manager import OrderManager
from bot.risk_manager import RiskManager
from bot.position import Position, ExitReason
from bot import dead_position
from strategies.base import BaseStrategy, Action
from strategies.macd import MacdStrategy
from strategies.rsi_mean_reversion import RsiMeanReversionStrategy
from strategies.cvd import CvdStrategy
from strategies.rsi2_pullback import Rsi2PullbackStrategy, trend_up_from_hourly
from strategies.regime import detect_regime, is_strategy_active
from indicators import ta
from safety.failsafe import Failsafe
from safety.notifier import TelegramNotifier
from incubation.logger import TradeLogger


ALL_STRATEGIES = {
    "macd": MacdStrategy,
    "rsi": RsiMeanReversionStrategy,
    "cvd": CvdStrategy,
    "rsi2": Rsi2PullbackStrategy,
    "rsi2_15m": Rsi2PullbackStrategy,      # 같은 신호, 15분봉 (스펙의 timeframe 으로 구분)
}


def build_strategies(names: tuple[str, ...] | None = None) -> dict[str, BaseStrategy]:
    """
    가동 전략 생성. 기본은 헌장 ACTIVE_STRATEGIES (v1.3: rsi2 단독).

    macd/rsi/cvd 는 장기·walk-forward 검증에서 모두 음의 기댓값으로 확인돼 비활성 상태다.
    클래스는 남겨두므로 재검증 후 ACTIVE_STRATEGIES 에 추가하면 즉시 되살릴 수 있다.
    """
    picked = names if names is not None else C.ACTIVE_STRATEGIES
    return {n: ALL_STRATEGIES[n](STRATEGY_SPECS[n]) for n in picked}


class Trader:
    def __init__(self):
        self.client = UpbitClient()
        self.orders = OrderManager(self.client)
        self.risk = RiskManager()
        self.strategies = build_strategies()
        self.notifier = TelegramNotifier()
        self.screener = Screener(notifier=self.notifier)
        self.failsafe = Failsafe(self.client, self.orders, self.notifier)
        self.logger = TradeLogger()
        self.positions: dict[str, Position] = {}   # key: strategy name (전략당 1포지션 §3.4)
        self.last_prices: dict[str, float] = {}    # 대시보드용 최신가 캐시
        self.regimes: dict[str, str] = {}          # 대시보드용 레짐 캐시
        self.trend_up: dict[str, bool] = {}        # 1시간봉 EMA200 추세 캐시 (rsi2 §2, v1.3)
        self._trend_at: dict[str, float] = {}      # 종목별 추세 갱신 시각(monotonic)
        # {코인: {전략: {action, reason, rsi2, atr_pct, trend_up, gate}}} — 진입 진단 (v1.5)
        self.signal_view: dict[str, dict] = {}

    # ── 부팅 (§9.3 상태 복구) ────────────────────────────────
    def boot(self) -> None:
        settings.validate()
        self._recover_positions()
        forced = forced_paper_reason()
        strategies = ", ".join(self.strategies)
        self.notifier.send(
            f"🤖 봇 시작 · 헌장 {C.CHARTER_VERSION} · 전략 [{strategies}] · "
            f"dry_run={settings.dry_run}")
        if forced:
            self.notifier.send(
                f"🧪 **모의 모드로 강제됨** — {forced}\n"
                f"실거래 주문은 전송되지 않습니다. 신호·손익은 그대로 기록되므로 "
                f"대시보드로 검증할 수 있습니다.")

    def _recover_positions(self) -> None:
        """
        재시작 시 실계좌 잔고를 읽어 '봇이 모르는 보유 코인(오펀)'을 포지션으로 복원한다 (§9.3).
        진입가는 avg_buy_price, 진입시각은 최근 매수체결 시각(없으면 현재봉)으로 채운다.
        복원된 포지션은 빈 전략 슬롯에 배정되어 TP/SL·죽은포지션 관리를 받는다.
        """
        known = {p.market for p in self.positions.values()}
        orphans = self.failsafe.recover_state(known_markets=known)
        for o in orphans:
            # v1.4: 포지션 키가 '전략:코인'이라 같은 코인만 아니면 어느 전략에든 붙일 수 있다.
            # 기준봉 전략(5분)에 먼저 배정하고, 이미 그 코인을 들고 있으면 다른 전략을 찾는다.
            slot = next((n for n, s in self.strategies.items()
                         if f"{n}:{o['market']}" not in self.positions), None)
            if slot is None:
                self.notifier.send(
                    f"⚠️ 오펀 코인 {o['market']} 복원 실패: 빈 전략 슬롯 없음 — 수동 확인 필요")
                continue
            # pyupbit 캔들 인덱스는 tz-naive KST 이므로 entry_time 도 동일 기준으로 맞춘다.
            if o.get("entry_time"):
                entry_time = pd.Timestamp(o["entry_time"] + 9 * 3600, unit="s")  # epoch→KST naive
            else:
                entry_time = pd.Timestamp(now_kst().replace(tzinfo=None))
            try:
                rec_df = self.client.get_candles(o["market"])
                entry_atr = float(ta.atr(rec_df).iloc[-1]) if len(rec_df) >= 14 else 0.0
            except Exception as e:
                entry_atr = 0.0
                self.logger.log("recover", strategy=slot, market=o["market"],
                                reason=f"atr_lookup_failed: {e}")
            rec_pos = Position(
                strategy=slot, market=o["market"], entry_price=o["avg_price"],
                size_krw=o["avg_price"] * o["volume"], volume=o["volume"],
                entry_time=entry_time, entry_atr=entry_atr,
            )
            self.positions[rec_pos.key] = rec_pos
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

        # 가동 전략이 쓰는 타임프레임을 모아 종목별로 한 번씩 조회한다 (v1.4: 5분+15분 병행)
        timeframes = sorted({s.spec.timeframe for s in self.strategies.values()})
        for market in self.screener.eligible():
            frames: dict[str, pd.DataFrame] = {}
            for tf in timeframes:
                try:
                    frames[tf] = self.client.get_candles(market, interval=tf)
                except Exception as e:
                    self.failsafe.on_api_error(e)
            if not frames:
                continue
            self._process_market(market, frames)

    # ── 상위 타임프레임 추세 (rsi2 §2, v1.3) ────────────────
    def _trend_ctx(self, market: str) -> dict:
        """
        1시간봉 EMA200 추세를 조회해 캐시한다. 5분봉 200봉으로는 200시간 EMA를 계산할 수 없고
        매 tick 2400봉을 받으면 API 호출이 12배가 되므로, 1시간봉을 별도로 받아 캐시한다.
        판정은 '직전에 완성된 1시간봉' 기준이라 1시간에 한 번만 갱신하면 충분하다.
        """
        import time as _time
        last = self._trend_at.get(market, 0.0)
        if market in self.trend_up and (_time.monotonic() - last) < C.TREND_REFRESH_SEC:
            return {"trend_up": self.trend_up[market]}
        try:
            hourly = self.client.get_candles(market, interval="minute60", count=210)
            up = trend_up_from_hourly(hourly)
        except Exception as e:
            self.failsafe.on_api_error(e)
            up = None
        self._trend_at[market] = _time.monotonic()
        if up is None:
            # 판정 불가 → 캐시를 지워 '진입 보류'가 되게 한다(추세 확인 없는 진입 금지)
            self.trend_up.pop(market, None)
            return {}
        self.trend_up[market] = up
        return {"trend_up": up}

    def _process_market(self, market: str, frames: dict) -> None:
        """
        frames: {타임프레임: 캔들} — 전략마다 자기 타임프레임의 캔들로 판단한다 (v1.4).
        청산도 그 포지션을 만든 전략의 타임프레임으로 판정해야 백테스트와 일치한다
        (시간손절 봉 수·ATR·신호가 모두 봉 단위이므로).
        """
        # DataFrame 은 `or` 로 평가할 수 없다(진리값 모호) → 명시적 None 검사
        base = frames.get(C.BASE_TIMEFRAME)
        if base is None:
            base = next(iter(frames.values()))
        self.last_prices[market] = float(base["close"].iloc[-1])   # 대시보드 캐시
        regime = detect_regime(base)
        self.regimes[market] = regime.value
        ctx = self._trend_ctx(market)

        # 1. 보유 포지션 청산 판정
        for key, pos in list(self.positions.items()):
            if pos.market != market:
                continue
            df = frames.get(pos.spec.timeframe)
            if df is None:                    # 해당 봉 조회 실패 → 이번 tick 판정 보류
                continue
            price = float(df["close"].iloc[-1])
            pos.update_high(price)
            reason = pos.check_price_exit(price)           # TP/SL (§4.1~2)
            why = ""
            if reason == ExitReason.NONE:
                dead, why = dead_position.is_dead(pos, df)  # §4-A
                if dead:
                    reason = ExitReason.DEAD_POSITION
                elif pos.strategy in self.strategies:
                    sig = self.strategies[pos.strategy].signal(df, ctx)   # 역방향 (§4.3)
                    if sig.action == Action.EXIT:
                        reason = ExitReason.REVERSE_SIGNAL
            if reason != ExitReason.NONE:
                # note 는 '추가 설명'만 담는다. 청산 종류(reason.value)를 여기 다시 넣으면
                # 로그에 "take_profit:take_profit" 처럼 중복 기록된다.
                self._close(pos, price, reason,
                            note=why if reason == ExitReason.DEAD_POSITION else "")

        # 2~3. 진입: 레짐 활성 전략만 (§8) + 리스크 통과(§5) + 사이징(§7)
        for name, strat in self.strategies.items():
            df = frames.get(strat.spec.timeframe)
            if df is None:
                continue
            if f"{name}:{market}" in self.positions:     # 같은 전략·같은 코인 중복 금지
                continue
            # 전략별 종목 제외 (§3.2-h, v2.1): 5분봉에서 음수로 확인된 종목은 5분봉 진입 금지
            if market.split("-", 1)[-1] in C.STRATEGY_BLACKLIST.get(name, set()):
                continue
            # always_active 전략은 레짐 필터를 통과시킨다 (§8, v1.3):
            # ADX 레짐 필터는 백테스트에서 기댓값 개선이 확인되지 않았고, rsi2 검증 시에도
            # 쓰지 않았으므로 적용하면 '검증되지 않은 다른 전략'이 된다.
            if not strat.spec.always_active and not is_strategy_active(strat.regime, regime):
                continue
            # 동시 포지션 한도(§5.5, 3개)는 risk.can_enter() 가 본다. 전략당 1포지션 제약은
            # v1.4에서 제거 — 백테스트 포트폴리오가 동시 3포지션을 가정했으므로 맞춘다.
            ok, _ = self.risk.can_enter()
            if not ok:
                continue
            sig = strat.signal(df, ctx)
            # 관찰용: 왜 진입하지 않았는지 대시보드에 노출 (§10, v1.5)
            self.signal_view.setdefault(market, {})[name] = {
                "action": sig.action.value, "reason": sig.reason, **sig.meta}
            if sig.action != Action.ENTER_LONG:
                continue
            if self._is_duplicate(market):         # 동일코인 중복 금지 (§3.3)
                continue
            self._open(name, strat, market, float(df["close"].iloc[-1]), df, note=sig.reason)

    # ── 진입/청산 실행 ───────────────────────────────────────
    def _open(self, name: str, strat: BaseStrategy, market: str,
              price: float, df: pd.DataFrame, note: str = "") -> None:
        entry_atr = float(ta.atr(df).iloc[-1]) if len(df) >= 14 else 0.0
        if not (entry_atr > 0):
            self.logger.log("entry_fail", strategy=name, market=market, reason="no atr")
            return
        # 주문 직전 스프레드 재확인 (§6, v1.6). 유니버스는 10분 주기로 갱신되므로
        # 스크리닝 시점의 스프레드가 최신이 아니다. 넓어졌으면 진입하지 않는다
        # (거래당 기댓값이 0.26% 수준이라 스프레드 0.1%p 차이가 손익을 가른다).
        if C.VERIFY_SPREAD_ON_ENTRY:
            ok, why = self.screener.tradable_now(market, C.strategy_spread_cap(name))
            if not ok:
                self.logger.log("entry_skip", strategy=name, market=market,
                                reason=f"진입 취소: {why}")
                return

        # 고정 손절(spec.stop_pct)이 있으면 그 값, 없으면 ATR 정규화 (§7, v1.3)
        stop_ratio = C.stop_ratio_for(strat.spec, entry_atr, price)
        krw = self.risk.size_for(stop_ratio)                # §7.2 (ATR 정규화 v1.2)
        # 잔고 부족 등으로 주문금액이 최소주문금액 미만이면 조용히 스킵(로그 스팸 방지)
        if krw < C.MIN_ORDER_KRW:
            return
        res = self.orders.enter_long(market, price, krw)   # §6
        if not res.ok or res.filled_volume <= 0:
            self.logger.log("entry_fail", strategy=name, market=market,
                            reason=res.error or "no fill")
            return
        pos = Position(
            strategy=name, market=market, entry_price=res.avg_price or price,
            size_krw=krw, volume=res.filled_volume, entry_time=df.index[-1],
            entry_atr=entry_atr,
        )
        self.positions[pos.key] = pos          # '전략:코인' 키 (v1.4)
        self.risk.on_open()
        self.logger.log("entry", strategy=name, market=market, price=price,
                        volume=res.filled_volume, size_krw=krw, reason=note)

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
        self.positions.pop(pos.key, None)
        self.logger.log("exit", strategy=pos.strategy, market=pos.market,
                        price=fill_price, volume=filled, pnl_krw=pnl,
                        reason=f"{reason.value}:{note}" if note else reason.value)
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
        for key, pos in list(self.positions.items()):
            price = self.last_prices.get(pos.market, pos.entry_price)
            positions.append({
                "strategy": pos.strategy,
                "timeframe": pos.spec.timeframe,
                "market": pos.market,
                "entry_price": pos.entry_price,
                "current_price": price,
                "size_krw": pos.size_krw,
                "pnl_krw": round(pos.pnl_krw(price)),
                "pnl_pct": round(pos.pnl_ratio(price) * 100, 2),
                "sl": -pos.sl_ratio * 100,
                "tp": pos.tp_ratio * 100,
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
            "universe": self.screener.eligible(),
            # v1.3 운영 관찰 항목
            "charter_version": C.CHARTER_VERSION,
            "strategies": list(self.strategies),
            "dry_run": settings.dry_run,
            "forced_paper": forced_paper_reason(),
            "trend_up": dict(self.trend_up),        # 1시간봉 EMA200 위 여부 (진입 전제조건)
            "signals": {m: dict(v) for m, v in self.signal_view.items()},   # 진입 진단 (v1.5)
            "spread_rejected_new": list(getattr(self.screener, "rejected_new", [])),
            "spread_unstable": list(getattr(self.screener, "rejected_unstable", [])),
            "spread_stats": (self.screener.spread_stats()
                             if hasattr(self.screener, "spread_stats") else {}),
            "spreads": {m: round(v * 100, 3) for m, v in
                        getattr(self.screener, "spreads", {}).items()},
            "spread_rejected": {m: round(v * 100, 3) for m, v in
                                getattr(self.screener, "rejected", {}).items()},
        }

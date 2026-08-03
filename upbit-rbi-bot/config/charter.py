"""
헌장(Trading Charter) 규칙을 코드 상수로 고정한 단일 기준(SSOT).

이 파일의 값은 docs/TRADING_CHARTER_KR.md 의 §13 파라미터 표와 1:1로 대응한다.
코드 어디서든 매매 파라미터가 필요하면 반드시 여기서 import 한다.
값을 바꾸려면 먼저 헌장을 개정(버전업)한 뒤 이 파일에 반영한다. (헌장 §14)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

CHARTER_VERSION = "v2.5"

# ── 자본·수수료 (헌장 §1, §13) ────────────────────────────────
# 자본은 더 이상 고정값이 아니라 '실계좌 잔고(총 자산)'를 런타임에 읽어서 쓴다 (헌장 v1.1 §7.1).
# 아래 값은 DRY_RUN(모의) 및 계좌 조회 실패 시의 폴백 기본값일 뿐이다.
DEFAULT_CAPITAL_KRW = 90_000        # 폴백 기본 자본 (실전에선 계좌 잔고로 대체됨)
ALLOC_PER_STRATEGY_RATIO = 1 / 3    # 전략당 배분 ≈33% = 30,000원 (§7.1)
FEE_ROUNDTRIP = 0.001               # 왕복 수수료 0.1% (0.05% × 2) (§6, 업비트 KRW)
MIN_ORDER_KRW = 5_000               # 업비트 최소주문금액 (§6.5)

# ── 리스크·서킷 브레이커 (헌장 §5) ───────────────────────────
RISK_PER_TRADE_RATIO = 0.01         # 1거래 최대손실 = 자본 1% (§5.1)
DAILY_LOSS_LIMIT_RATIO = 0.03       # 일일 손실 한도 = 자본 -3% (§5.2)
MAX_CONSECUTIVE_LOSSES = 5          # 연속 손절 차단 (§5.3)
MAX_DRAWDOWN_RATIO = 0.15           # MDD 전면정지 = 고점 대비 -15% (§5.4)
MAX_CONCURRENT_POSITIONS = 3        # 동시 최대 포지션 (§3.4, §5.5)

# ── 봉·시간 손절 (헌장 §1, §4-A) ─────────────────────────────
BASE_TIMEFRAME = "minute5"          # 기준 봉 (pyupbit interval)
TIME_STOP_BARS = 24                 # 시간 손절: 24봉(2시간) (§4-A)
FLAT_BAND_RATIO = 0.005             # 횡보 판정 밴드 ±0.5% (§4-A)
FLAT_BARS = 12                      # 횡보 지속 봉 수 (§4-A)
ATR_SHRINK_RATIO = 0.5              # 변동성 축소 판정: 진입시 ATR의 50% 이하 (§4-A, 백테스트 후 채택)

# ── 주문 집행 (헌장 §6) ──────────────────────────────────────
LIMIT_UNFILLED_TIMEOUT_SEC = 30     # 지정가 미체결 취소 시간 (§6.3)
LIMIT_REORDER_MAX = 1               # 미체결 재주문 허용 횟수 (§6.3)

# ── 청산 실패 백오프 (§6.7, v2.7) ────────────────────────────
# 실매매에서 청산 실패를 **매 tick(13초)** 재시도해 10시간 42분 동안 2,854건이 쌓였다.
# 원인은 5,000원 미만 먼지 잔량 — 몇 번을 다시 보내도 성공할 수 없는 주문이었다.
# 실패가 반복되면 간격을 지수적으로 벌려 API·로그를 아끼고, 임계치를 넘으면 한 번만 경보한다.
EXIT_RETRY_BASE_SEC = 30            # 1차 실패 후 대기
EXIT_RETRY_MAX_SEC = 1800           # 백오프 상한 30분
EXIT_FAIL_ALERT_AFTER = 5           # 이 횟수를 넘으면 '수동 확인 필요' 경보(1회)


def exit_retry_delay_sec(failures: int) -> float:
    """연속 청산 실패 횟수 → 다음 재시도까지 대기(초). 30s→60s→120s… 최대 30분."""
    if failures <= 0:
        return 0.0
    return min(EXIT_RETRY_BASE_SEC * (2 ** (failures - 1)), EXIT_RETRY_MAX_SEC)

# ── 백테스트 통과 기준 (헌장 §11) ────────────────────────────
BACKTEST_MIN_WINRATE = 0.55
BACKTEST_MIN_PROFIT_FACTOR = 1.5
BACKTEST_MAX_DRAWDOWN = 0.20
BACKTEST_MIN_TRADES = 100

# ── 레짐 필터 (헌장 §8) ──────────────────────────────────────
ADX_TREND_THRESHOLD = 25            # ADX 이 값 이상이면 추세장 (초기값, 백테스트로 확정)

# ── 종목 스크리닝 (헌장 §3, v1.2 / 스프레드 필터 v1.3 / 풀 확대 v1.6) ───────
# v1.6에서 추적 종목 풀을 6 → 15로 늘렸다. 실측(2026-07-26): 스프레드 ≤0.1%를 통과하는
# KRW 종목이 18개인데, 거래대금 100억 하한이 그중 5개만 남기고 후보 스캔 범위(상위 24개)가
# 12개를 아예 검사하지 않아 실제 풀이 3~5개에 불과했다.
# 거래대금 하한을 30억으로 낮춘 근거: 주문금액 30,000원은 하루 거래대금 30억의 0.001%로
# 시장충격이 없고, 실제 체결 품질은 스프레드(≤0.1%)와 최우선호가 잔량(≥30,000원)이 보장한다.
# 동시 추적 종목 수 (동시 보유는 §5.5로 3개 제한).
# v2.5에서 15 → 60. 15는 **근거 없이 잡은 값**이었다(365 상장일과 같은 실패). 이 값을
# 실제로 제약하는 것이 무엇인지 실측해서 다시 정한다.
#
# ① 무엇이 비용인가 — tick 1회당 캔들 왕복 실측 **0.215초**(n=6, 중앙값 0.215, 최대 0.234).
#    이는 클라이언트 자체 스로틀 `_QUOTE_MIN_INTERVAL = 0.15`(data/upbit_client.py:26)보다
#    크다 → 스로틀이 아니라 **네트워크 지연이 지배한다**.
#    종목당 2회(minute5 + minute15) ≈ 0.43초. 1시간봉 추세는 TREND_REFRESH_SEC=900 캐시라
#    갱신되는 tick 에서만 종목당 +0.215초가 더 붙는다.
# ② 늦어지면 무슨 일이 나는가 — deploy/run_bot.py:23-25 는 `tick(); sleep(interval)` 이다.
#    즉 interval 은 **고정 주기가 아니라 tick 사이의 지연**이라, tick 이 길어져도 실패하거나
#    겹치지 않고 주기만 늘어난다.
#    주기 ≈ 0.43·N + 10초, 추세 갱신 tick 은 ≈ 0.645·N + 10초.
#    N=60 → 약 36초 / 49초. 5분봉 300초의 **12~16%**이고 진입은 다음 봉 시가에 체결되므로
#    이 지연은 신호에 영향이 없다.
# ③ 레이트리밋은 N 과 무관하다 — 호출이 직렬이므로 처리율은 N 에 상관없이 ~4.65 req/s 로
#    일정하다(업비트 시세 한도 10 req/s). N 을 키우면 **속도가 아니라 소요시간**만 늘어난다.
# ④ 후보 스캔량도 안 늘어난다 — screener 는 `top_n × SPREAD_CANDIDATE_MULT` 로 후보를 뽑는데
#    이미 15×20=300 ≥ KRW 전체(270)라 **전수 스캔으로 포화**돼 있다. 늘어나는 것은 마지막
#    `[:top_n]` 슬라이스뿐이다.
#
# 실제 상한은 이 상수가 아니라 **스프레드 필터**다. 2026-07-28 라이브 실측:
# 스캔 225종목 중 **219개가 스프레드로 탈락**, 5개가 신규상장으로 탈락 → 유니버스 **9개**.
# 즉 15칸조차 다 차지 않았고, 60으로 올려도 오늘 당장 편입되는 종목은 없다.
# 60 은 '도달 가능한 수'가 아니라 **다시는 이 값이 제약이 되지 않게 하는 수**로 고른 것이다
# (스프레드가 좁아지는 국면에서 통과 종목이 18~20개로 늘어도 칸이 모자라지 않게).
UNIVERSE_TOP_N = 60
# 거래대금 하한 (v2.0에서 30억 → 1억): 30억·100억은 **근거 없이 잡은 보수적 값**이었다.
# 손익에 직접 영향을 주는 것은 ①스프레드 ②최우선호가 잔량 ③종목별 기댓값이고, 거래대금은
# 그 대리 지표에 불과하다 — 우리는 셋 다 직접 측정한다. 거래대금이 실제로 대리하던 위험
# (시세조작·급등락)은 업비트 **투자경고/투자주의 플래그**로 직접 차단한다(아래).
# 실측 통과 종목 수: 30억 7개 → 10억 12개 → 1억 **18개**(더 낮춰도 스프레드가 막아 안 늘어남).
# 1억을 남긴 이유: 하루 1억(분당 약 7만원)은 '실제로 거래가 일어나는지'의 최소 확인선.
MIN_TURNOVER_24H_KRW = 100_000_000       # 24h 거래대금 하한(1억) 미달 제외
UNIVERSE_REFRESH_SEC = 600               # 적격 유니버스 재조회 주기(10분)
STABLECOINS = {"USDT", "USDC", "DAI", "TUSD", "BUSD"}
# 수동 제외 심볼 (측정 증거 기반). 동적 안정성 필터(§3.2-d)는 관측 3회가 쌓여야 작동하므로,
# 이미 8회 샘플링으로 비용 초과가 확인된 종목은 여기서 즉시 차단한다 (2026-07-26 실측 중앙값):
#   LPT 0.245% · ICP 0.190% · AXS 0.153%  → 왕복비용 0.25~0.35% > 거래당 기댓값 0.26%
# 스프레드가 개선되면 재측정 후 해제한다(`spread_check.py --sample mid`).
# KAITO (v1.9): 스프레드는 0.085%로 통과하지만 **2년 백테스트에서 음의 기댓값**
#   (5분봉 −0.066%/247거래, 15분봉 −0.001%/106거래, 가중 −0.046%/353거래 — lab_universe.py).
#   비용이 아니라 신호가 이 종목에서 안 먹히는 경우다.
UNIVERSE_BLACKLIST: set[str] = {
    # 비용 초과(스프레드 중앙값이 상한 초과, 8회 샘플링): LPT 0.245% · ICP 0.190% · AXS 0.153%
    "LPT", "ICP", "AXS",
    # 신호 부적합 — 스프레드는 통과하나 2년 백테스트에서 **양쪽 타임프레임 모두 음수** (v2.1):
    #   KAITO 5분 −0.065%/15분 −0.000% · GAS 15분 −0.019% · TRUMP 15분 −0.524% · TRX 15분 −0.265%
    "KAITO", "GAS", "TRUMP", "TRX",
}

# 호가 스프레드 상한 (v1.3) — 거래대금만 보면 안 되는 이유:
# 업비트 KRW는 가격대별 호가 단위(tick)가 고정이라 **가격이 낮은 코인은 한 틱이 이미 0.2~0.9%**다.
# 2026-07-26 실측: DOGE 0.930%, ADA 0.412%, TRX 0.206% vs BTC 0.067%, BCH 0.033%.
# 왕복 스프레드가 거래당 기댓값(≈0.18%)을 넘으면 어떤 신호로도 구조적 손실이므로
# 거래대금 상위여도 스프레드가 넓은 종목은 유니버스에서 제외한다.
MAX_SPREAD_RATIO = 0.001                 # (매도호가−매수호가)/중간가 상한 0.1%
# 후보 스캔 배수 (v1.6에서 4 → 8 → v2.0에서 20): 스프레드를 통과하는 종목은 거래대금 순위와
# 무관하게 흩어져 있다(실측: SOL 27위, AVAX 29위, SUI 36위, **ATOM 120위**). 120종목까지
# 넓혔을 때도 ATOM/ENS/GAS 가 순위 밖으로 빠졌다 → 거래대금 하한을 넘는 종목은 **전부** 확인한다.
# 비용: 오더북 30종목/요청 = 최대 9회 호출, 10분 주기라 부담 없다(상장일 판정은 캐시된다).
# v2.5(UNIVERSE_TOP_N 15→60) 이후에도 이 값은 그대로 둔다: 15×20=300 시점에 이미 KRW
# 전체(270)를 넘어 **포화**했으므로, 60×20=1200 이 돼도 스캔 대상은 전 종목으로 동일하다.
SPREAD_CANDIDATE_MULT = 20    # top_n×20 ≥ KRW 전체(270) → 전수 스캔 (v2.0 이후 포화)
# 진입 직전 스프레드 재확인 (v1.6) — 유니버스는 10분 주기로 갱신되므로 스크리닝 시점의
# 스프레드가 최신이 아니다. 실제 주문 전에 한 번 더 확인해 넓어졌으면 진입을 취소한다.
# (풀을 넓히면 스프레드 변동이 큰 종목이 섞이므로 이 확인이 필수가 된다)
VERIFY_SPREAD_ON_ENTRY = True

# 스프레드 '안정성' 요건 (v1.7) — 순간 스냅샷으로는 판단할 수 없다.
# 실측(2026-07-26): LPT 스프레드가 스냅샷 0.044% vs 8회 중앙값 **0.245%** 로 크게 흔들렸다.
# 진입 직전 확인(§6.7)은 진입 쪽만 보장한다 — **청산 시점 스프레드는 진입할 때 알 수 없다**.
# 따라서 최근 관측치의 중앙값과 최댓값을 함께 보고, 변동이 큰 종목은 유니버스에서 뺀다.
SPREAD_HISTORY_LEN = 6                   # 유니버스 갱신 주기(10분)×6 = 최근 1시간 관측
SPREAD_HISTORY_MIN_SAMPLES = 3           # 이 개수 이상 모이면 안정성 판정 적용
SPREAD_MAX_MULT = 2.0                    # 관측 최댓값이 상한의 이 배수를 넘으면 제외

# 최우선 호가 잔량 하한 (v1.3) — 스프레드가 좁아도 잔량이 주문금액보다 작으면 호가를 타고
# 올라가며 체결돼(= walking the book) 측정한 스프레드보다 큰 비용을 낸다. 전략당 배분
# (자본의 1/3 ≈ 30,000원)을 최우선 호가에서 소화할 수 있는 종목만 거래한다.
MIN_TOP_DEPTH_KRW = 30_000

# 최소 상장 경과일 (v1.5: 365 → v2.4: 10) — '거래가 불가능한 종목'만 배제한다.
# 거래대금 상위만 보면 **상장 당일 코인이 1위로 올라온다**(2026-07-26 실측: EUL 상장 0일, 587억 1위).
#
# 진짜 하한은 이 값이 아니라 **1시간봉 201개 요건**이다(하드 게이트):
#   strategies/rsi2_pullback.py:trend_up_from_hourly() 는 1시간봉이 201개 미만이면 None 을 반환하고,
#   bot/trader.py:_trend_ctx() 는 None 이면 ctx 를 비워 전략이 "추세 판정 불가 → 진입 보류"로 간다.
#   (라이브 창은 200봉이고 ctx 없는 폴백은 TREND_EMA_BARS=2400 봉을 요구하므로 우회 경로도 없다.)
# 즉 상장 201시간(= 8.4일) 미만 종목은 **애초에 진입 자체가 불가능**하다. 잘못된 추세 판정으로
# 진입할 경로가 없으므로, 365일 배제는 '나쁜 신호'를 막고 있던 게 아니었다.
# 10일 = 8.4일(201시간) + 거래소 캔들 공백 여유(실측 최대 공백 415분).
#
# 원래의 선택 편향 우려는 이제 다른 장치가 담당한다:
#   ① UNIVERSE_BLACKLIST — 신호가 음수로 **측정된** 종목을 직접 차단
#   ② 스프레드 상한·안정성 필터·최우선호가 잔량 (§3.2-c/d, §6.7)
#   ③ 투자경고/투자주의 플래그 제외 (§3.2-g)
# 또한 운영자는 v2.2에서 REQUIRE_VALIDATED_MARKET 을 **의도적으로 off** 했다 — 미검증 종목을
# 허용하면서 나이만으로 일괄 배제하는 것은 그 결정과 앞뒤가 맞지 않는다.
#
# v2.4에서 감수했던 대가 — "UNIVERSE_TOP_N=15 는 유한한데 신규 상장 코인은 거래대금 순위
# 최상단에 오므로, 상장 10~365일 종목이 15칸을 두고 경쟁해 **기존 검증 종목이 밀려날 수 있다**" —
# 는 v2.5에서 상한을 60으로 올리며 사실상 해소됐다. 스프레드를 통과하는 종목이 실측 18~20개라
# 자리 경쟁 자체가 일어나지 않는다(칸이 아니라 스프레드가 유니버스를 정한다).
MIN_LISTING_DAYS = 10

# ── 검증된 종목만 거래 (§3.2-i, v2.1) ────────────────────────
# 헌장 §0.3 "검증 없이 투입 없음". 스프레드가 좁아도 신호가 안 먹히는 종목이 있으므로
# (KAITO: 스프레드 0.084%인데 5분 −0.065%·15분 −0.000%), **개별 백테스트를 통과한 종목만**
# 거래한다. 새 종목을 추가하려면 `backtesting/research/lab_universe.py` 로 검증한 뒤 등록한다.
# 값 = 그 종목에 허용하는 스프레드 상한(검증에 사용한 보수적 스프레드 이상으로는 두지 않는다).
# v2.2 (운영자 결정): 미검증 종목도 **허용**한다. 비용 필터(스프레드·잔량·안정성·경고/주의)와
# 블랙리스트는 그대로 작동하므로 보호는 유지되지만, KAITO 처럼 '스프레드는 좁은데 신호가
# 안 먹히는 종목'이 섞일 수 있다 — 백로그의 개별 검증으로 확인해 블랙리스트에 추가한다.
# 검증 완료 종목 목록(VALIDATED_MARKETS)은 스프레드 상한 완화 근거로 계속 쓰인다.
REQUIRE_VALIDATED_MARKET = False
VALIDATED_MARKETS: dict[str, float] = {
    # 5분봉·15분봉 모두 양수 (검증 스프레드 ≤0.1%)
    "BTC": 0.001, "ETH": 0.001, "XRP": 0.001, "SOL": 0.001,
    "BCH": 0.001, "LINK": 0.001, "ATOM": 0.001, "ORCA": 0.001,
    # 15분봉만 양수 (5분봉은 STRATEGY_BLACKLIST 로 금지)
    "AVAX": 0.0025, "ETC": 0.0025, "DOT": 0.0025, "ENS": 0.0025, "SUI": 0.0025,
    "NEAR": 0.001,
}

# ── 종목별 허용 타임프레임 (§3.2-h, v2.1) ─────────────────────
# 발견: **스프레드가 0.1%를 넘는 종목은 5분봉에서 전부 음수, 15분봉에서는 대부분 양수**다.
# 5분봉은 한 봉 움직임(0.196%)이 작아 비용에 민감하고, 15분봉(0.323%)은 비용을 감당한다.
# 그래서 종목을 차단하는 대신 **허용 타임프레임을 종목별로 다르게** 둔다.
#
# 2년 백테스트 결과(보수적 스프레드 = 관측 중 더 넓은 값, 라이브와 동일 파라미터):
#   5분+15분 모두 양수: ATOM +0.165/+0.908 · BCH +0.194/+0.670 · LINK +0.092/+0.395
#                       SOL +0.209/+0.739 · XRP +0.158/+0.361 · ORCA +0.029/+0.061
#                       BTC +0.659/+2.833 · ETH +0.568/+0.644 (표본 적으나 강한 양수)
#   15분봉만 양수:      AVAX −0.096/+0.560 · ETC −0.034/+0.507 · DOT −0.047/+0.371
#                       ENS −0.066/+0.174 · SUI −0.077/+0.103 · NEAR −0.034/+0.149
STRATEGY_BLACKLIST: dict[str, set[str]] = {
    "rsi2": {"AVAX", "ETC", "DOT", "ENS", "SUI", "NEAR"},   # 5분봉 음수 종목
}

# 15분봉 검증을 통과한 종목은 스프레드 상한을 완화한다(5분봉은 위 STRATEGY_BLACKLIST 로 금지).
# 미검증 종목에는 적용하지 않는다 — 검증 없이 넓은 스프레드를 허용하면 TRUMP(−0.524%) 같은
# 종목이 들어온다.
# (스프레드 상한은 VALIDATED_MARKETS 값으로 통합했다 — 종목별 상한이 곧 검증 조건이다)
WIDE_SPREAD_ALLOWED: dict[str, float] = {
    k: v for k, v in VALIDATED_MARKETS.items() if v > MAX_SPREAD_RATIO
}

# 투자경고·투자주의 종목 제외 (v2.0) — 업비트가 `market/all?isDetails=true` 로 제공한다.
# 경고(warning): 상장폐지 검토 등. 주의(caution): 가격급등락·거래량급증·소수계정 집중 등 조작 징후.
# 거래대금 하한이 어설프게 막고 있던 위험을 직접·정확하게 차단한다(실측: 경고 4개·주의 11개).
EXCLUDE_MARKET_WARNING = True            # 투자경고 종목 제외
EXCLUDE_MARKET_CAUTION = True            # 투자주의 종목 제외

# ── 상위 타임프레임 추세 캐시 (헌장 §2, v1.3) ─────────────────
# rsi2 는 1시간봉 EMA200 위에서만 진입한다. 판정 기준이 '직전에 완성된 1시간봉'이므로
# 15분마다 갱신하면 충분하다(매 tick 조회하면 API 낭비).
TREND_REFRESH_SEC = 900


class Regime(str, Enum):
    TREND = "trend"      # 추세장 → MACD
    RANGE = "range"      # 횡보장 → RSI 평균회귀
    REVERSAL = "reversal"  # 반전 → CVD 보조


@dataclass(frozen=True)
class StrategySpec:
    """전략별 진입/청산 파라미터 (헌장 §2, §4). SL/TP는 ATR 배수로 정규화(v1.2)."""
    name: str
    atr_stop_mult: float  # k: 손절거리 = k × ATR
    rr: float             # 손익비: 익절거리 = rr × (k × ATR)
    regime: Regime        # 이 전략이 유리한 레짐
    # ── v1.3 추가: 백테스트로 검증된 설정을 라이브에 그대로 재현하기 위한 필드 ──
    stop_pct: float | None = None   # 고정 손절거리비율. 지정하면 ATR 대신 이 값을 쓴다
    min_atr_ratio: float = 0.0      # 진입 변동성 게이트: ATR/가격이 이 값 미만이면 진입 금지
    # ── v2.3 추가: RSI(2) 진입 임계값을 전략별로 둔다 ──
    # v2.2까지는 strategies/rsi2_pullback.ENTRY_LEVEL(=3.0) 모듈 상수를 두 전략이 공유했다.
    # 15분봉은 봉 길이에 맞춰 진입선을 재측정(7.0)했으므로 스펙에서 분리해야 한다.
    # 기본값 3.0 = 기존 상수와 동일 → 지정하지 않은 전략의 동작은 바뀌지 않는다.
    entry_level: float = 3.0        # RSI(2)가 이 값 이하일 때만 진입 (청산선 70.0은 공용)
    time_stop_bars: int | None = None   # 전략별 시간손절(봉). None이면 전역 TIME_STOP_BARS
    use_dead_extras: bool = True    # §4-A의 부가 규칙(횡보·신호중립·ATR축소) 사용 여부
    always_active: bool = False     # True면 레짐 필터(§8)를 통과시킨다
    timeframe: str = BASE_TIMEFRAME  # 이 전략이 판단에 쓰는 봉 (pyupbit interval)
    # ── v2.7 추가: 구조 기반 청산(반익반본). easy_teaching 실매매 실패 분석의 결과 ──
    # 0.0 이면 미사용(기존 전량 단일 청산). 0.5 면 1차 목표에서 절반 청산 후
    # 나머지는 진입가 본절 스탑으로 추세를 태운다(원문 '반익반본').
    partial_tp_ratio: float = 0.0

    @property
    def risk_reward(self) -> float:
        return self.rr


# 전략 스펙 — 헌장 §4 표와 1:1 대응
STRATEGY_SPECS: dict[str, StrategySpec] = {
    "macd": StrategySpec("macd", atr_stop_mult=1.5, rr=2.0, regime=Regime.TREND),
    "rsi":  StrategySpec("rsi",  atr_stop_mult=1.2, rr=1.6, regime=Regime.RANGE),
    "cvd":  StrategySpec("cvd",  atr_stop_mult=1.3, rr=1.7, regime=Regime.REVERSAL),
    # rsi2 (v1.3) — 2년 5분봉·홀드아웃 17개월에서 §11을 통과한 유일한 설정.
    # 검증 성적: 553거래 승률 69.1% PF 1.49 거래당 +0.180%(수수료+실측스프레드 차감) t+3.48
    #            계좌 +32.7% MDD 5.9% (backtesting/research/README.md)
    # 손절/익절은 ATR이 아니라 고정 2.5%다(검증된 값). 레짐 필터는 적용하지 않는다
    # (ADX 필터는 개선 근거가 확인되지 않았고, 검증 시에도 쓰지 않았다).
    # ⚠ v2.6(테스트) — 변동성 게이트 0.6% → 0.3%. 실매매 매수-매도 왕복을 실제로 관측하려고
    #   거래를 일부러 늘린 값이다. 백테스트로 검증한 값이 아니다. 테스트가 끝나면 0.006 으로 되돌린다.
    "rsi2": StrategySpec("rsi2", atr_stop_mult=0.0, rr=1.0, regime=Regime.RANGE,
                         stop_pct=0.025, min_atr_ratio=0.003, time_stop_bars=96,
                         use_dead_extras=False, always_active=True,
                         timeframe="minute5"),
    # rsi2_15m (v1.4) — 같은 신호를 15분봉에 적용. 15분봉은 '한 봉 평균 움직임 0.323% vs
    # 왕복비용 0.15%'로 5분봉(0.196% vs 0.15%)보다 비용 부담이 절반이라 거래당 엣지가 크다.
    # 검증(홀드아웃 17개월): 188거래 승률 73.9% PF 1.96 거래당 +0.489% t+3.75
    # 5분봉과 병행 시(동시 3포지션·동일코인 중복 금지): 741신호 PF 1.64 t+5.06 계좌 +51.4% MDD 6.2%
    #
    # v2.3 — 진입선 3→7, 게이트 1.0%→0.83%. 근거(선택구간 209일·보수적 스프레드):
    #   기존 th=3/gate 1.00%  →  7거래 승률 42.9% 보수적 기댓값 **−0.3378%**
    #   신규 th=7/gate 0.83%  → 59거래 승률 81.4% 보수적 기댓값 **+0.2585%**
    #                           (왕복 0.25% 스트레스에서도 +0.2649%, 하루 0.28회)
    #   walk-forward 8폴드 중 6폴드가 독립적으로 gate 0.0083 을 선택.
    # ⚠ 통계적으로 입증된 값은 아니다 — walk-forward t=+1.08(입증에는 표본 약 8.5배 필요)이고
    #   양의 결과가 특정 한 폴드에 상당히 의존한다. '측정된 최선'일 뿐이다(헌장 §2 개정 주석).
    #
    # ⚠ v2.6(테스트) — 게이트 0.83% → 0.4%. 위와 같은 이유(왕복 체결 관측)로 낮춘 값이며
    #   검증되지 않았다. walk-forward가 고른 값은 0.0083 이다 — 테스트 후 되돌린다.
    "rsi2_15m": StrategySpec("rsi2_15m", atr_stop_mult=0.0, rr=1.0, regime=Regime.RANGE,
                             stop_pct=0.030, min_atr_ratio=0.004, time_stop_bars=32,
                             use_dead_extras=False, always_active=True,
                             timeframe="minute15", entry_level=7.0),
    # easy_teaching (v2.7 재설계) — 청산을 원문(docs/strategies/easy-teaching-man)대로 되돌렸다.
    # 실매매 실패의 1차 원인은 신호가 아니라 청산 설계였다(ACTIVE_STRATEGIES 주석 참조):
    #   ① 손절 = 오더블록 생성 캔들의 저점(구조적 무효화 지점). ATR 배수·1% 하한이 아니다.
    #      → 전략이 Signal.stop_price 로 자리마다 다른 값을 넘긴다. atr_stop_mult 는 폴백일 뿐.
    #   ② 익절 = 직전 스윙 고점에서 **절반 청산** → 나머지는 진입가 본절 스탑(partial_tp_ratio).
    #      고정 rr 배수(1.5 → 최소 +1.5%)는 15분봉 MFE(평균 +0.44%)로는 닿을 수 없었다.
    #   ③ 횡보청산(±0.5%/12봉)은 원문에 없는 규칙이고 13건 중 4건을 죽였다 → use_dead_extras=False.
    #   ④ 시간손절 8봉(2시간)은 '익절 못 가면 비용만 내고 나감'이었다 → 48봉(12시간)으로 넓힌다.
    #      원문에 시간손절은 없지만 오펀 방지 안전장치로 남긴다.
    # ⚠ **§11 미통과 — 폐기 상태다.** ACTIVE_STRATEGIES 에 넣지 않는다.
    #   위 재설계를 마친 뒤 백테스트한 결과(backtesting/research/lab_easy_teaching.py,
    #   14종목·비용 = 수수료 0.1% + 실측 스프레드 전액):
    #     15분봉 518거래 승률 26.6% PF 0.58 거래당 −0.211% t −4.04 (13/14 종목 음수)
    #     1시간봉  98거래 승률 32.7% PF 0.80 거래당 −0.209% t −0.69
    #     4시간봉  16거래 승률 37.5% PF 0.94 거래당 −0.107% t −0.10
    #   핵심: **거래당 gross 엣지 +0.04~0.12% vs 왕복비용 0.23~0.26%** — 비용이 엣지의 2~6배다.
    #   청산을 원문대로 고쳐도 결과가 바뀌지 않았다 → 문제는 청산이 아니라 진입 신호의 엣지 크기다.
    #   되살리려면 backtesting/research/README.md 의 그 표를 먼저 뒤집어야 한다.
    "easy_teaching": StrategySpec("easy_teaching", atr_stop_mult=1.5, rr=1.5, regime=Regime.RANGE,
                                  timeframe="minute15", always_active=True, time_stop_bars=48,
                                  use_dead_extras=False, partial_tp_ratio=0.5)
}

# 가동 전략 (v1.4) — macd/rsi/cvd 는 장기·walk-forward·국면분해에서 모두 음의 기댓값으로
# 확인되어 비활성화한다(backtesting/research/README.md). 되살리려면 §11 기준을 먼저 통과해야 한다.
#
# easy_teaching 은 2026-08-03 에 **비활성화**했다 (실매매 결과 분석).
#   실적: 2026-07-30~08-01 진입 16건 / 청산 15건 / 1승, 거래당 평균 −0.379%, 합계 약 −1,886원.
#   청산 15건이 **전부 dead(시간손절·횡보)** 였다 — 익절·손절·역신호 청산 0건.
#   원인은 신호가 아니라 청산 설계다: 익절선 = rr 1.5 × max(1.5×ATR/가격, 1%) 로 **최소 +1.5%**
#   인데 시간손절 8봉(2시간)·횡보청산 12봉이 먼저 발동한다. 실측 MFE 평균 +0.44%, 최대 +1.42%
#   → **13건 중 익절 도달 0건**. 이길 수 있는 경로가 존재하지 않는 조합이었다(통계가 아니라 산술).
#   배경: §11 백테스트 0건·인큐베이션 생략으로 실계좌 투입됐고, ACTIVE_STRATEGIES 를 바꾸면서
#   CHARTER_VERSION 을 올리지 않아 §9.7 승인 게이트도 통과했다(→ charter_fingerprint 로 수정).
#   복귀 조건: §11 통과(승률>55%·PF>1.5·MDD<20%·100거래+) → 5,000원 인큐베이션 2~4주.
ACTIVE_STRATEGIES: tuple[str, ...] = ("rsi2", "rsi2_15m")


# ── §11 검증 단계 (v2.7) ─────────────────────────────────────
# §11 은 백테스트 통과 → **5,000원 인큐베이션 2~4주** → 점진 확대 순서를 규정한다.
# 그런데 코드에 이 단계가 없어서 easy_teaching 은 백테스트도 인큐베이션도 없이 첫 거래부터
# 30,000원(자본의 1/3)으로 들어갔다. 이제 단계를 코드에 새긴다.
#
# VALIDATED — §11 백테스트를 통과했고 근거가 문서로 남은 전략(위 스펙 주석 참조).
# INCUBATING — 통과했지만 아직 실전 관찰 중. 주문금액을 최소주문금액으로 강제한다.
# 둘 중 어디에도 없는 전략은 ACTIVE_STRATEGIES 에 넣을 수 없다(tests/test_charter.py 가 막는다).
VALIDATED_STRATEGIES: frozenset[str] = frozenset({"rsi2", "rsi2_15m"})
INCUBATING_STRATEGIES: frozenset[str] = frozenset()


def position_cap_for(strategy: str, krw: float) -> float:
    """인큐베이션 중인 전략은 최소주문금액으로 묶는다 (§11-3)."""
    return min(krw, float(MIN_ORDER_KRW)) if strategy in INCUBATING_STRATEGIES else krw


# ── 실거래 승인 지문 (§9.7, v2.7) ────────────────────────────
# §9.7 은 "헌장이 개정되면 실거래 승인(LIVE_CHARTER_ACK)이 무효화된다"는 장치인데,
# **버전 문자열만** 비교했다. 그래서 2026-07-30 에 easy_teaching 을 ACTIVE_STRATEGIES 에
# 새로 넣고 스펙을 두 번 고치는 동안 CHARTER_VERSION 은 "v2.5" 그대로였고, 서버의
# LIVE_CHARTER_ACK=v2.5 가 그대로 통과해 **검증 한 번 없는 전략이 실계좌에서 돌았다**.
# 이제 '가동 전략 목록 + 그 전략들의 스펙'까지 지문에 넣는다 — 사람이 버전을 올리는 것을
# 잊어도 매매 규칙이 바뀌면 승인이 자동으로 무효화된다.
def charter_fingerprint() -> str:
    """실거래 승인 대상 = 헌장 버전 + 가동 전략과 그 스펙. 바뀌면 재승인이 필요하다."""
    import hashlib

    parts = [CHARTER_VERSION]
    for name in sorted(ACTIVE_STRATEGIES):
        spec = STRATEGY_SPECS[name]
        fields = sorted(f"{k}={v!r}" for k, v in vars(spec).items())
        parts.append(f"{name}({','.join(fields)})")
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:8]
    return f"{CHARTER_VERSION}-{digest}"

# ── 파생 계산 헬퍼 (모두 '현재 자본(capital)'을 인자로 받는다) ──────────────
# capital = 실계좌 총 자산(원화 + 보유코인 평가액). 입금하면 자동으로 커지고,
# 출금/손실이면 작아진다 → 모든 리스크 한도·포지션 크기가 잔고에 비례해 스케일된다.

def max_loss_per_trade_krw(capital: float) -> float:
    """1거래 최대 손실 금액 (§5.1). capital × 1%. 예: 90,000 → 900원."""
    return capital * RISK_PER_TRADE_RATIO


def daily_loss_limit_krw(capital: float) -> float:
    """일일 손실 한도 금액 (§5.2). capital × 3%. 예: 90,000 → 2,700원."""
    return capital * DAILY_LOSS_LIMIT_RATIO


def position_size_krw(stop_ratio: float, capital: float,
                      available_krw: float | None = None) -> float:
    """
    리스크 상한 기반 포지션 크기 (§7.2).
        포지션 크기 = 1거래최대손실 ÷ 손절거리비율(stop_ratio)
    stop_ratio = k × ATR / 진입가 (ATR 정규화, v1.2). 코인 변동성이 크면 stop_ratio↑ → 크기↓
    → 모든 코인이 동일 KRW 리스크(자본 1%). 전략당 배분·주문가능원화로 clamp.
    """
    if stop_ratio <= 0:
        return 0.0
    raw = max_loss_per_trade_krw(capital) / stop_ratio
    alloc_cap = capital * ALLOC_PER_STRATEGY_RATIO
    size = min(raw, alloc_cap)
    if available_krw is not None:
        size = min(size, available_krw)
    return max(0.0, size)


# ── ATR 사이징 폴백 (헌장 §7, v1.2) ──────────────────────────
FALLBACK_STOP_RATIO = 0.03   # entry_atr 없을 때(봉 부족/복원) SL 거리 기본값
MIN_STOP_RATIO = 0.01        # 손절거리 하한 1% (§7). 5분봉 저변동 시 ATR 스톱이 수수료(0.1%) 밑으로
                             # 내려가 수수료로 잔고를 갉아먹는 것을 방지(초단타 스캘핑 금지 원칙).


def stop_ratio_from_atr(atr_stop_mult: float, entry_atr: float, entry_price: float) -> float:
    """
    손절거리비율 = k×ATR/진입가 (§7). 단 MIN_STOP_RATIO(수수료 방어 하한)보다 작아지지 않게 바닥.
    ATR/가격이 유효하지 않으면 FALLBACK_STOP_RATIO.
    """
    if entry_price > 0 and entry_atr > 0:
        return max(atr_stop_mult * entry_atr / entry_price, MIN_STOP_RATIO)
    return FALLBACK_STOP_RATIO


# ── 구조 기반 진입 필터 (v2.7, easy_teaching 실패 분석) ──────
# 손절을 '근거가 깨지는 지점'에 두면 손익비가 자리마다 달라진다. 그래서 손익비 자체가
# 자리 필터가 된다 — 목표까지의 거리가 손절거리의 MIN_ENTRY_RR 배에 못 미치면 진입하지 않는다.
# 이 필터가 있으면 "익절이 산술적으로 도달 불가능한 자리"를 진입 단계에서 걸러낸다.
MIN_ENTRY_RR = 1.2           # 진입 최소 손익비 (목표거리 ÷ 손절거리)
MAX_STRUCT_STOP_RATIO = 0.05  # 구조적 손절거리 상한 5%. 이보다 멀면 '자리'로 보지 않는다


def entry_rr(entry: float, stop: float, target: float) -> float:
    """구조 레벨로 계산한 손익비. 손절거리가 유효하지 않으면 0.0."""
    risk = entry - stop
    if entry <= 0 or risk <= 0:
        return 0.0
    return (target - entry) / risk


def entry_rr_ok(entry: float, stop: float, target: float) -> bool:
    """진입 손익비 게이트 (§7, v2.7). 손절이 진입가 위이거나 너무 멀면 진입 불가."""
    risk = entry - stop
    if entry <= 0 or risk <= 0:
        return False
    if risk / entry > MAX_STRUCT_STOP_RATIO:
        return False
    return entry_rr(entry, stop, target) >= MIN_ENTRY_RR


def strategy_spread_cap(strategy: str) -> float:
    """전략별 스프레드 상한 (§3.2-h, v2.1).

    5분봉(rsi2)은 한 봉 움직임이 작아 비용에 민감하므로 기본 상한(0.1%)을 그대로 쓴다.
    15분봉(rsi2_15m)은 검증 통과 종목에 한해 완화된 상한(WIDE_SPREAD_ALLOWED)까지 허용한다 —
    상한 자체는 종목별로 `screener.cap_for` 가 판단하므로 여기서는 기본값을 반환한다.
    """
    return MAX_SPREAD_RATIO if strategy == "rsi2" else max(
        [MAX_SPREAD_RATIO, *WIDE_SPREAD_ALLOWED.values()])


def stop_ratio_for(spec: StrategySpec, entry_atr: float, entry_price: float) -> float:
    """전략 스펙에 맞는 손절거리비율 (v1.3).

    spec.stop_pct 가 있으면 그 고정값(백테스트에서 검증된 값)을 쓰고, 없으면 ATR 정규화(§7).
    """
    if spec.stop_pct is not None:
        return spec.stop_pct
    return stop_ratio_from_atr(spec.atr_stop_mult, entry_atr, entry_price)


def time_stop_bars_for(spec: StrategySpec) -> int:
    """전략별 시간손절 봉 수 (§4-A). 지정이 없으면 전역 기본값."""
    return spec.time_stop_bars if spec.time_stop_bars is not None else TIME_STOP_BARS

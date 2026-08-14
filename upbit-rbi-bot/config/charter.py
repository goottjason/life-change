"""
헌장(Trading Charter) 규칙을 코드 상수로 고정한 단일 기준(SSOT).

이 파일의 값은 docs/TRADING_CHARTER_KR.md 의 §13 파라미터 표와 1:1로 대응한다.
코드 어디서든 매매 파라미터가 필요하면 반드시 여기서 import 한다.
값을 바꾸려면 먼저 헌장을 개정(버전업)한 뒤 이 파일에 반영한다. (헌장 §14)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

CHARTER_VERSION = "v4.0"

# ── 자본·수수료 (헌장 §1, §13) ────────────────────────────────
# 자본은 더 이상 고정값이 아니라 '실계좌 잔고(총 자산)'를 런타임에 읽어서 쓴다 (헌장 v1.1 §7.1).
# 아래 값은 DRY_RUN(모의) 및 계좌 조회 실패 시의 폴백 기본값일 뿐이다.
DEFAULT_CAPITAL_KRW = 90_000        # 폴백 기본 자본 (실전에선 계좌 잔고로 대체됨)
ALLOC_PER_STRATEGY_RATIO = 2 / 3    # 검증 트랙 배분 상한 (§7.1, v4.0). 실험 예산(3×10,000원)
                                    # 침범 방지. ⚠ 실효 리스크 = min(f, 배분×손절거리)
                                    # = min(2.4%, 0.667×2.5%) ≈ **1.67%** — f 2.4% 가 온전히
                                    # 실리지 않음을 알고 감수한다. 두 트랙이 한 계좌를 공유하는
                                    # 대가다 (스펙 2026-08-14 §1, 운영자 승인).
                                    # 검증트랙 배분×동시보유 + 실험예산 ≤ 자본 (test_charter 가 강제)
FEE_ROUNDTRIP = 0.001               # 왕복 수수료 0.1% (0.05% × 2) (§6, 업비트 KRW)
MIN_ORDER_KRW = 5_000               # 업비트 최소주문금액 (§6.5)

# ── 리스크·서킷 브레이커 (헌장 §5, v4.0 절대값 전환 2026-08-14) ────
# v3.0 은 서킷을 R 배수로 정의했다(lab_circuit.py 13차 — f 를 바꿔도 오발률 유지).
# v4.0 에서 실험 트랙(고정금액 사이징)이 생기며 R 정의가 계좌 전체에 성립하지 않아
# **절대 %** 로 전환했다. 값은 운영자가 학습 지속성 기준으로 직접 골랐다(아래).
# 12연패 차단의 오발률 근거(13차: 연 3.7회)는 rsi2 단독 기준 — breakout 합산 시 재측정 대상.
RISK_PER_TRADE_RATIO = 0.024        # 1거래 최대손실 = 자본 2.4% (§5.1) = 1R
# ── f = 2.4% 채택 (2026-08-06, 운영자 결정) ───────────────────
#   근거: lab_kelly.py — 켈리 f* = 11.61%(동시보유 3 반영). 2.4% 는 그 **1/4.8** 로
#   정정된 쿼터켈리(2.90%)보다도 낮다. 실측 경로 통계:
#       f=1.0%  연 +27.8% · 중앙 MDD 11.1% · 반토막 0.0%
#       f=2.4%  연 **+74.1%** · 중앙 MDD **25.4%** · 반토막 **0.0%**   ← 채택
#       f=2.9%  연 +92.7% · 중앙 MDD 30.2% · 반토막 0.5%
#   (v4.0: 서킷은 더 이상 f 를 따라오지 않는다 — 절대값 §5 블록 참조.)
#
# ⚠ **감수하는 것**: 인큐베이션 100건으로 실전 엣지가 아직 확인되지 않았다.
#   실전 엣지가 백테스트보다 작으면 손실도 2.4배가 된다. 운영자가 이를 알고 선택했다.
#   되돌리려면 이 한 줄만 0.01 로 바꾸면 서킷도 함께 되돌아온다.
# ── 서킷 (v4.0, 운영자 결정 2026-08-14): R 단위 → **절대 %** ─────────
# v3.0 은 서킷을 R(=f 배수)로 정의했다. 그러나 v4.0 부터 두 트랙(검증 f 기반 사이징 +
# 실험 고정금액 사이징)이 한 계좌를 공유하므로 R 정의가 성립하지 않는다.
# 값은 운영자가 직접 골랐다: "천천히 잃으면서 배우는 것과 하루에 다 잃는 것은
# 배움의 양이 다르다." (일 −5% ≈ 하루 최대 약 4,500원, MDD −50% = 시드 절반 보전)
DAILY_LOSS_LIMIT_RATIO = 0.05       # 일일 손실 한도 −5% (§5.2). 당일 신규 진입 정지
MAX_DRAWDOWN_RATIO = 0.50           # 고점 대비 −50% 전면 정지 (§5.4). 재가동은 수동
MAX_CONSECUTIVE_LOSSES = 12         # 연속 손절 차단 (§5.3, v3.0 값 유지. 일 경계 자동 해제)
# ⚠ 12연패 차단은 두 트랙 **합산**이다. breakout 은 승률이 낮고 드문 큰 승리로 버는
#   구조라 12연패가 월 1~2회 나올 수 있다 — 발동 빈도를 주간 리포트로 관찰하고,
#   잦으면 트랙별 분리를 다음 개정에서 검토한다 (스펙 §3).

# ── 트랙별 동시 포지션 (§5.5, v4.0) ──────────────────────────
MAX_POSITIONS_VALIDATED = 1         # 검증 트랙 (v3.0 의 MAX_CONCURRENT_POSITIONS=1 승계.
                                    # 1 로 줄인 근거는 2026-08-06 실효 리스크 분석 — git 이력 참조)
MAX_POSITIONS_EXPERIMENTAL = 3      # 실험 트랙 (예산 = 3 × EXPERIMENT_MAX_ORDER_KRW)
MAX_CONCURRENT_POSITIONS = MAX_POSITIONS_VALIDATED + MAX_POSITIONS_EXPERIMENTAL

# 검증 트랙 동시 최대 위험. 실험 트랙 히트는 f 가 아니라 주문 상한으로 바운드된다
# (3 × 10,000원 × 손절거리 ≈ 자본의 0.5% 수준 — f 사이징이 아니므로 여기 안 넣는다).
TOTAL_HEAT_RATIO = RISK_PER_TRADE_RATIO * MAX_POSITIONS_VALIDATED

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
    # DOGE (2026-08-13 편입) — 스프레드가 0.930% → 0.102% 로 9배 좁아지면서 거래 가능해졌다.
    #   §11 재검정(477거래, 실측 스프레드 0.102% 반영): 승률 66.5% · PF 1.55 ·
    #   거래당 +0.152% · 월클러스터 t +2.23 → **5개 항목 전부 통과**.
    #   ⚠ 성과가 홀드아웃에 몰려 있다(탐색 −0.043% / 홀드아웃 +0.291%, 양수월 7/7).
    #     탐색 구간엔 스프레드가 0.93% 여서 애초에 거래 불가였으므로 그 구간을 반증으로
    #     쓰기 어렵지만, "최근 7개월만 좋았을" 가능성도 배제할 수 없다. 인큐베이션에서 확인한다.
    #   상한 0.0011 = 실측 0.102% + 소폭 여유. 넓어지면 스크리너가 자동으로 뺀다.
    "DOGE": 0.0011,
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

# 5분봉(rsi2)에서 허용하는 **완화 상한의 천장** (§3.2-h, v3.1).
# 기본 상한(0.10%)보다 넓은 종목은 원칙적으로 5분봉 금지다 — 한 봉 움직임이 작아
# 비용에 민감하기 때문이다(0.25% 종목들이 STRATEGY_BLACKLIST["rsi2"] 에 있는 이유).
# 다만 **소폭 완화**는 그 스프레드에서 §11 을 통과했다면 허용한다.
#   실측 근거: rsi2 의 손익분기 스프레드(= gross − 수수료) 중앙값 0.079% · 상위 25% 0.179%.
#   DOGE 는 손익분기 0.254% 로 0.11% 상한에 충분한 여유가 있다(§11 5개 항목 통과).
# 이 천장을 넘기려면 그 종목의 손익분기 스프레드를 먼저 측정할 것.
RSI2_MAX_RELAXED_SPREAD = 0.0012

# 투자경고·투자주의 종목 제외 (v2.0) — 업비트가 `market/all?isDetails=true` 로 제공한다.
# 경고(warning): 상장폐지 검토 등. 주의(caution): 가격급등락·거래량급증·소수계정 집중 등 조작 징후.
# 거래대금 하한이 어설프게 막고 있던 위험을 직접·정확하게 차단한다(실측: 경고 4개·주의 11개).
EXCLUDE_MARKET_WARNING = True            # 투자경고 종목 제외
EXCLUDE_MARKET_CAUTION = True            # 투자주의 종목 제외
# ⚠ 이 필터는 §11 백테스트에 **모델링되어 있지 않다** (2026-08-13 확인).
#   백테스트는 신호가 나면 다 잡을 수 있다고 가정하지만, 실제로는 업비트가 투자주의로
#   지정한 기간에는 진입이 차단된다. 그래서 **실전 거래 수는 백테스트보다 적다.**
#   실례: DOGE 를 §11 통과로 편입(v3.1)했으나 편입 시점에 이미 투자주의
#   (DEPOSIT_AMOUNT_SOARING) 라 거래가 시작되지 않았다. 플래그가 풀리면 스크리너가
#   10분 주기로 자동 편입한다.
#   ★ 이 필터를 특정 종목을 넣으려고 완화하지 말 것 — 조작 징후 차단이 목적이고,
#     원하는 종목이 걸렸다고 푸는 것은 목적을 뒤집는 것이다.

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
    # ── v2.8 추가: easy_teaching 의 진입 근거 조합 ──
    # 원문의 5가지 근거 중 어떤 조합을 '2개 이상'으로 인정할지. 백테스트로 고르기 위한 축이다.
    #   "ob_fvg"       — 오더블록 되돌림 + 인접 FVG (v2.7, §11 미통과)
    #   "fakeout"      — 지지 스윕 후 되찾기(페이크아웃/트랩). 지지구조 + 함정 = 근거 2개
    #   "fakeout_zone" — 위에 더해 스윕 저점이 오더블록/FVG 와 겹칠 것 (근거 3개)
    #   "any"          — ob_fvg 또는 fakeout 중 성립하는 쪽
    confluence_mode: str = "ob_fvg"
    # 지지 구조물로 인정할 스윙 저점의 유의성(좌우 몇 봉보다 낮아야 하는가).
    # 작으면 3봉 피벗마다 '지지'가 생겨 원문의 '채널 하단'과 전혀 다른 수준이 된다.
    pivot_k: int = 3
    # ── v2.9: 원저자 강의(2025.09, docs/strategies/easy-teaching-man/)에서 새로 확인한 규칙 ──
    # 강의가 반복 강조하는 공통 원칙: **"손절은 복마감(봉마감)을 보고 한다."**
    # 손절 기준을 둘로 제시한다 — ①꼬리 끝 터치 즉시 ②꼬리 끝을 벗어나 **봉마감**할 때.
    # 그리고 "②가 ①보다 승률이 확실히 높다"고 명시한다. v2.7까지는 ①만 구현돼 있었고
    # 백테스트 승률이 25~30%로 나온 것이 정확히 그 증상이다.
    stop_on_close: bool = False
    # 오더블록 신뢰도(강의): "각 캔들의 몸통 크기가 **2배 이상** 차이 날 경우 신뢰할 만한
    # 오더블록으로 식별된다." 0.0 이면 필터 없음(v2.7 동작).
    min_ob_body_mult: float = 0.0
    # 강의: 손익비를 1:1로 고정하는 건 "핸디캡", "필수가 아니라 권장", "손익비의 함정을
    # 조심하라 — 따지다가 먹을 것도 못 먹고 나오는 경우가 대다수". v2.7의 RR 게이트는
    # 원저자가 명시적으로 경계한 것이므로 끌 수 있게 한다.
    use_rr_gate: bool = True
    # 강의가 가장 자주 반복하는 신뢰도 주장: "15분봉 오더블록과 5분봉 오더블록이 겹치는
    # 자리는 상당히 신뢰도가 높다", "1분·5분·15분·1시간·4시간 전부 다 체크해야 한다".
    # True 면 상위 타임프레임 오더블록과 겹치는 자리에서만 진입한다(ctx["htf_obs"]).
    require_mtf_overlap: bool = False
    # 상위 타임프레임 추세 상승을 요구할지. 원문의 페이크아웃 예시는 **하락 채널 하단**이라
    # 추세를 요구하면 원문 취지와 어긋날 수 있다 → 백테스트로 확인할 축으로 둔다.
    require_trend: bool = True
    # ── v4.0: 돌파(실험 트랙) 파라미터. 기본값(0/None)은 기존 전략의 동작을 바꾸지 않는다 ──
    breakout_bars: int = 0            # 직전 N봉 최고가 돌파 진입. 0 = 미사용
    vol_mult: float = 0.0             # 거래량 확인: 현재봉 ≥ 직전 N봉 평균 × 이 값. 0 = 끔
    trail_atr_mult: float = 0.0       # 트레일링 스톱: 고점 − 이 값×진입ATR. 0 = 미사용
    time_stop_min_profit: float | None = None  # 시간손절 시 이 수익률 이상이면 청산 유예

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
    # v2.9 — 변동성 게이트를 **검증값 0.6% 로 되돌렸다**. v2.6에서 왕복 체결을 관측하려고
    #   0.3%로 낮춰 뒀는데, 그 값은 백테스트에서 **거래당 −0.050%(음수)** 인 지점이다:
    #       게이트 0.30% → −0.050% · 0.50% → +0.142% · 0.60% → +0.199% · 0.70% → +0.206%
    #   관측 목적은 달성됐고(실전 왕복 체결 확보), 이제부터는 §11-3 인큐베이션 표본을 쌓는
    #   단계다. 검증되지 않은 값으로 표본을 모으면 '다른 전략'의 실적을 모으는 셈이 된다.
    "rsi2": StrategySpec("rsi2", atr_stop_mult=0.0, rr=1.0, regime=Regime.RANGE,
                         stop_pct=0.025, min_atr_ratio=0.006, time_stop_bars=96,
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
    # v3.0 — **v2.3 개정을 취소하고 개정 전 값(진입선 3 · 게이트 1.0%)으로 되돌렸다.**
    #   v2.3은 209일 '선택구간'에서 고른 값이고, 당시 주석 스스로 "통계적으로 입증된 값이
    #   아니다 — walk-forward t=+1.08, 양수가 특정 한 폴드에 상당히 의존한다"고 경고했다.
    #   2년 전체로 재측정하니 그 우려가 그대로 확인됐다(lab_exit_timing.py, 비용=수수료+실측 스프레드):
    #       개정 전 진입3·게이트1.00% : 저스프레드 6종목 +0.457%(t+3.58) · 전체 +0.243%(t+3.65)
    #       v2.3   진입7·게이트0.83% : 저스프레드 6종목 +0.130%(t+2.16) · 전체 **−0.026%(t−0.82)**
    #   개정 전이 모든 조합에서 낫고 v2.3 값은 전체 종목에서 음수다.
    #   ⚠ 이것은 '결과를 보고 새로 고른 것'이 아니라 **입증되지 않은 변경의 취소**다.
    #     되돌린 값은 원래 2년·홀드아웃으로 검증된 값이다(§11 통과 근거).
    "rsi2_15m": StrategySpec("rsi2_15m", atr_stop_mult=0.0, rr=1.0, regime=Regime.RANGE,
                             stop_pct=0.030, min_atr_ratio=0.010, time_stop_bars=32,
                             use_dead_extras=False, always_active=True,
                             timeframe="minute15", entry_level=3.0),
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
                                  use_dead_extras=False, partial_tp_ratio=0.5),
    # breakout (v4.0 실험 트랙, §3.5) — **§11 검증 없이 가동한다** (운영자 결정, 스펙 §0 비목적).
    # "조용히 있다가 움직이기 시작하는 순간 올라타서, 움직임이 끝나면 바로 내린다":
    # 직전 20봉(5분×20=100분) 최고가를 종가가 돌파 + 거래량 1.5배 확인 → 진입.
    # 청산은 전부 가격 기반: 트레일링(고점−1.5×진입ATR) · 고정손절 백스톱(1×ATR, 하한 1%) ·
    # 시간손절 12봉(단 +0.3% 이상 수익 중이면 유예 — 트레일링이 마무리).
    # rr=0.0: 고정 익절이 없다(트레일링이 대체). Position 이 trail_atr_mult>0 이면 비율 익절을
    # 건너뛴다. 변동성 게이트·추세 필터는 **판단에 쓰지 않고 기록만 한다**(주간 코호트가 재판단).
    # ⚠ 과거 연구(14차 H4)에서 돌파 계열 롱은 유의하게 음수였다. 이 트랙의 산출물은 수익이
    #   아니라 "어떤 조건의 돌파가 손실인가"의 실거래 데이터다. 주문은 EXPERIMENT_MAX_ORDER_KRW
    #   (10,000원)로 강제 제한된다. 시작값 근거: lab_breakout.py 캘리브레이션(2026-08-14).
    "breakout": StrategySpec("breakout", atr_stop_mult=1.0, rr=0.0, regime=Regime.TREND,
                             min_atr_ratio=0.0, time_stop_bars=12,
                             use_dead_extras=False, always_active=True,
                             timeframe="minute5",
                             breakout_bars=20, vol_mult=1.5, trail_atr_mult=1.5,
                             time_stop_min_profit=0.003),
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
# breakout 은 v4.0 실험 트랙(EXPERIMENTAL) — §11 미통과 상태로 가동하되 주문 상한이 강제된다.
ACTIVE_STRATEGIES: tuple[str, ...] = ("rsi2", "rsi2_15m", "breakout")


# ── §11 검증 단계 (v2.7) ─────────────────────────────────────
# §11 은 백테스트 통과 → **5,000원 인큐베이션 2~4주** → 점진 확대 순서를 규정한다.
# 그런데 코드에 이 단계가 없어서 easy_teaching 은 백테스트도 인큐베이션도 없이 첫 거래부터
# 30,000원(자본의 1/3)으로 들어갔다. 이제 단계를 코드에 새긴다.
#
# VALIDATED — §11 백테스트를 통과했고 근거가 문서로 남은 전략(위 스펙 주석 참조).
# INCUBATING — 통과했지만 아직 실전 관찰 중. 주문금액을 최소주문금액으로 강제한다.
# 둘 중 어디에도 없는 전략은 ACTIVE_STRATEGIES 에 넣을 수 없다(tests/test_charter.py 가 막는다).
VALIDATED_STRATEGIES: frozenset[str] = frozenset({"rsi2", "rsi2_15m"})

# ── 인큐베이션 표본 시작 기준일 (§11-3, v2.9) ────────────────
# 이 시각 **이후**의 거래만 인큐베이션 표본으로 센다. 그 전 기록은 조건이 달라 섞으면 안 된다:
#   ① 변동성 게이트가 미검증 테스트값(0.3%/0.4%)이었다 — 백테스트에서 음수인 지점
#   ② 오펀 복구가 아무 슬롯에나 붙어 easy_teaching 손실이 rsi2 장부에 기록됐다(v2.7에서 수정)
# 2026-08-04 16:15 KST = 검증값(0.6%/0.83%)으로 되돌린 컨테이너가 실제로 뜬 시각.
# ⚠ 날짜(00:00)가 아니라 **시각**이어야 한다. 같은 날 13:33 DOT·15:41 AVAX 진입 2건은
#   아직 옛 게이트(0.4%)로 들어간 거래였다 — 각각 ATR 0.42%/0.65% 로 검증 게이트 0.83%에
#   미달이라 **검증된 설정이었다면 아예 사지 않았을** 거래다. 날짜로 자르면 그 2건이
#   표본에 섞여 "검증된 전략의 실적"을 오염시킨다.
INCUBATION_START = "2026-08-04T16:15:00+09:00"
INCUBATING_STRATEGIES: frozenset[str] = frozenset()

# ── 실험 트랙 (§3.5, v4.0. 스펙 docs/superpowers/specs/2026-08-14-…) ──
# EXPERIMENTAL — §11 검증 **없이** 가동할 수 있는 공식 실험 차선. 대신 주문금액 상한을
# 코드로 강제한다. easy_teaching 사고(검증 0건 전략이 자본 1/3 로 실계좌 진입)의 재발
# 방지 장치를 유지하면서 "작게는 실험해도 된다"를 규칙으로 만든 것이다.
# 이 트랙의 1차 산출물은 수익이 아니라 **튜닝 데이터**다(진입 컨텍스트 전수 기록 → 주간
# 코호트 리포트). 운영자 의도: "자주 거래하며 그 안에서 문제점을 튜닝하며 다듬어간다."
EXPERIMENTAL_STRATEGIES: frozenset[str] = frozenset({"breakout"})
EXPERIMENT_MAX_ORDER_KRW = 10_000   # 실험 트랙 건당 주문 상한 (지문 포함 — 승인 대상)


def track_of(strategy: str) -> str:
    """전략 → 트랙. 모르는 전략(오펀 복구 등)은 보수적으로 검증 트랙 취급(더 좁은 한도)."""
    return "experimental" if strategy in EXPERIMENTAL_STRATEGIES else "validated"


def position_cap_for(strategy: str, krw: float) -> float:
    """인큐베이션은 최소주문금액(§11-3), 실험 트랙은 EXPERIMENT_MAX_ORDER_KRW(v4.0)로 묶는다."""
    if strategy in INCUBATING_STRATEGIES:
        return min(krw, float(MIN_ORDER_KRW))
    if strategy in EXPERIMENTAL_STRATEGIES:
        return min(krw, float(EXPERIMENT_MAX_ORDER_KRW))
    return krw


# ── 실거래 승인 지문 (§9.7, v2.7) ────────────────────────────
# §9.7 은 "헌장이 개정되면 실거래 승인(LIVE_CHARTER_ACK)이 무효화된다"는 장치인데,
# **버전 문자열만** 비교했다. 그래서 2026-07-30 에 easy_teaching 을 ACTIVE_STRATEGIES 에
# 새로 넣고 스펙을 두 번 고치는 동안 CHARTER_VERSION 은 "v2.5" 그대로였고, 서버의
# LIVE_CHARTER_ACK=v2.5 가 그대로 통과해 **검증 한 번 없는 전략이 실계좌에서 돌았다**.
# 이제 '가동 전략 목록 + 그 전략들의 스펙'까지 지문에 넣는다 — 사람이 버전을 올리는 것을
# 잊어도 매매 규칙이 바뀌면 승인이 자동으로 무효화된다.
def charter_fingerprint() -> str:
    """
    실거래 승인 대상 = 헌장 버전 + 가동 전략·스펙 + **§5 리스크/서킷 파라미터**.
    바뀌면 재승인이 필요하다(그전까지 자동으로 모의 모드).

    ⚠ v3.0 에서 리스크 파라미터를 추가했다. 이전 버전은 **전략 스펙만** 해싱해서,
      `RISK_PER_TRADE_RATIO` 나 MDD 정지선 같은 §5 값을 바꿔도 승인 게이트가 눈치채지
      못했다 — 이 게이트를 만든 이유(검증 안 된 설정이 이전 승인으로 실계좌에서 도는 것)가
      정확히 그런 경우이므로 같은 구멍을 남겨둘 수 없다.
    """
    import hashlib

    parts = [CHARTER_VERSION]
    for name in sorted(ACTIVE_STRATEGIES):
        spec = STRATEGY_SPECS[name]
        fields = sorted(f"{k}={v!r}" for k, v in vars(spec).items())
        parts.append(f"{name}({','.join(fields)})")
    # §5 리스크·서킷 — 자본을 직접 위험에 노출시키는 값들이므로 승인 대상이다
    parts.append("risk(" + ",".join([
        f"f={RISK_PER_TRADE_RATIO!r}",
        f"daily={DAILY_LOSS_LIMIT_RATIO!r}",       # v4.0: R 배수 → 절대값
        f"streak={MAX_CONSECUTIVE_LOSSES!r}",
        f"mdd={MAX_DRAWDOWN_RATIO!r}",             # v4.0: R 배수 → 절대값
        f"maxpos={MAX_CONCURRENT_POSITIONS!r}",
        # ★ 배분비율은 **실효 리스크를 직접 결정**한다(실효 f = min(f, 배분×손절거리)).
        #   2026-08-06 에 이걸로 데인 적이 있다 — f 를 2.4배 올렸는데 배분상한이 먼저 걸려
        #   실효는 0.83% 그대로였다. 승인 대상에서 빠지면 같은 일이 조용히 반복된다.
        f"alloc={ALLOC_PER_STRATEGY_RATIO!r}",
        # v4.0 실험 트랙 — 주문 상한·트랙별 동시보유도 실계좌 노출을 직접 결정한다
        f"exp_cap={EXPERIMENT_MAX_ORDER_KRW!r}",
        f"maxpos_v={MAX_POSITIONS_VALIDATED!r}",
        f"maxpos_e={MAX_POSITIONS_EXPERIMENTAL!r}",
    ]) + ")")
    # ★ 거래 대상 유니버스도 승인 대상이다 (v3.1, 2026-08-13).
    #   종목을 추가하거나 스프레드 상한을 바꾸면 **실제로 무엇을 사는지**가 달라진다.
    #   v3.0 까지는 이게 지문에서 빠져 있어, DOGE 를 넣어도 이전 승인이 그대로 통했다.
    parts.append("universe(" + ",".join([
        f"cap={MAX_SPREAD_RATIO!r}",
        "validated=" + ";".join(f"{k}:{v}" for k, v in sorted(VALIDATED_MARKETS.items())),
        "blacklist=" + ";".join(f"{k}:{sorted(v)}" for k, v in sorted(STRATEGY_BLACKLIST.items())),
    ]) + ")")
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:8]
    return f"{CHARTER_VERSION}-{digest}"

# ── 파생 계산 헬퍼 (모두 '현재 자본(capital)'을 인자로 받는다) ──────────────
# capital = 실계좌 총 자산(원화 + 보유코인 평가액). 입금하면 자동으로 커지고,
# 출금/손실이면 작아진다 → 모든 리스크 한도·포지션 크기가 잔고에 비례해 스케일된다.

def max_loss_per_trade_krw(capital: float) -> float:
    """1거래 최대 손실 금액 (§5.1). capital × RISK_PER_TRADE_RATIO."""
    return capital * RISK_PER_TRADE_RATIO


def daily_loss_limit_krw(capital: float) -> float:
    """일일 손실 한도 금액 (§5.2). capital × DAILY_LOSS_LIMIT_RATIO(= 5R)."""
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


def strategy_spread_cap(strategy: str, market: str | None = None) -> float:
    """
    전략별·종목별 스프레드 상한 (§3.2-h, v2.1 → v3.1).

    market 을 주면 **그 종목의 검증된 상한**(VALIDATED_MARKETS)을 쓴다.
    '검증됐다'는 것은 그 스프레드에서 §11 을 통과했다는 뜻이므로, 검증값보다 좁은
    기본 상한을 강요할 이유가 없다.

    ⚠ v3.1(2026-08-13) 이전에는 rsi2 가 **종목과 무관하게** 고정 0.1% 를 썼다. 그래서
      DOGE(실측 0.102%, §11 통과)가 **0.002%p 차이로** 영구 배제됐다.
      완화해도 rsi2 의 다른 종목은 영향이 없다 — 상한이 넓은 종목
      (AVAX·ETC·DOT·ENS·SUI 0.25%)은 전부 STRATEGY_BLACKLIST["rsi2"] 로 이미 막혀 있다.
    """
    base = MAX_SPREAD_RATIO if strategy == "rsi2" else max(
        [MAX_SPREAD_RATIO, *WIDE_SPREAD_ALLOWED.values()])
    if market:
        sym = market.split("-", 1)[-1]
        if strategy in STRATEGY_BLACKLIST and sym in STRATEGY_BLACKLIST[strategy]:
            return base                      # 이 전략에 금지된 종목은 완화하지 않는다
        return max(base, VALIDATED_MARKETS.get(sym, 0.0))
    return base


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

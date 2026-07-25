# 인생 역전 (LIFE CHANGE)

![아티클 커버 이미지](https://pbs.twimg.com/media/HECzXphbMAAjfFg.jpg)

# **싫어하는 직장을 그만두는 법. 나만의 트레이딩 봇을 만드는 법. 완벽 가이드.**

### ***나는 하버드를 나오지 않았다. 단지 AI를 원래 의도된 방식대로 사용하는 법을 배웠을 뿐이다***

---

# **왜 수동 매매는 알고리즘에게 지는가**

스탠퍼드 연구에 따르면, 뇌의 감정 중추인 편도체(amygdala)는 12밀리초 만에 반응한다. 논리를 담당하는 전전두엽 피질(prefrontal cortex)은 500밀리초가 걸린다. 40배 차이다.

트레이더가 빨간 캔들을 보는 순간, 뇌가 "이건 그냥 평범한 변동성일 뿐이야"라고 생각하기도 전에 몸은 이미 "매도" 버튼을 누르고 있다. 이것이 바닥에서의 공포성 매도, 복수 매매, 고점에서의 FOMO 매수, 그리고 "혹시 반등하지 않을까" 하며 손실 포지션을 청산하지 못하고 얼어붙는 현상으로 이어진다.

짐 시먼스(Jim Simons)는 르네상스 테크놀로지스(Renaissance Technologies)를 통해 310억 달러를 벌었다. 시타델(Citadel), 제인 스트리트(Jane Street), 투 시그마(Two Sigma) — 이 모든 펀드들은 알고리즘을 개발하는 퀀트(quant) 팀을 고용한다. 하지만 그들 중 누구도 손으로 매매하지 않는다 — 실행은 완전히 자동화되어 있다. 퀀트가 시스템을 만들고, 기계가 매매를 실행한다. 수동 트레이더의 99%는 돈을 잃는다. 성공한 펀드는 모두 알고리즘 기반이다.

봇에게는 편도체가 없다. 공포에 빠지지 않고, 복수 매매를 하지 않으며, 화면을 뚫어지게 쳐다보지 않는다. 그저 전략을 실행할 뿐이다.

# **RBI 시스템: 작동하는 봇을 만드는 세 단계**

*모든 트레이딩 봇은 출시 전에 세 단계를 거쳐야 한다:*

**리서치(Research) -> 백테스트(Backtest) -> 인큐베이트(Incubate)**

### **리서치: 트레이딩 아이디어를 어디서 찾을 것인가**

*가장 흔한 실수는 처음부터 전략을 발명하려는 것이다. 최고의 전략들은 이미 문서화되어 있다 — 어디를 봐야 하는지만 알면 된다.*

> ***Market Wizards (잭 슈웨거의 책 시리즈)***
>

세계 최고의 검증된 트레이더들과의 인터뷰를 담은 3~4권의 책. 검증됨(Verified) — 즉, 그들의 수익이 확인되었다는 뜻이다. 그들은 자신의 전략을 공개적으로 설명한다. Audible에서 이용 가능하다.

> **Chat with Traders (팟캐스트)**
>

무료, 300개 이상의 에피소드. 실제 트레이더들이 무엇이 효과가 있는지 설명한다. 순수 아이디어 분량으로만 따지면 — 방대한 에피소드 수 덕분에 책보다도 많다.

> **Google Scholar**
>

학술 논문의 무료 데이터베이스. "mean reversion trading strategies"(평균 회귀 트레이딩 전략)나 "momentum crypto strategies"(모멘텀 크립토 전략)를 검색하면 실제로 작동하는 전략을 담은 박사급 연구를 공개 접근으로 얻을 수 있다.

> **소액 매매 (Trading with Small Size)**
>

최소 금액($1~10)으로 하는 매매 — 수익이 아니라 관찰을 위한 것이다. 이렇게 아이디어가 태어나고, 나중에 공식화하고 테스트할 수 있다.

트레이딩에 새로운 것은 없다. 모든 아이디어는 이미 존재한다 — 찾고, 검증하고, 자동화하라.

### **백테스트: 전략을 꿰뚫어 보는 엑스레이 시력**

***백테스팅이란 전략을 과거 데이터에 대해 실행해 보는 것이다. "이게 될 것 같아"가 아니라 — 실제 숫자다.***

**왜 중요한가:**

과거 데이터에서 94% 수익률을 내는 전략이 있다고 하자. 백테스트 없이는 트레이더가 이를 알 수 없다 — 그냥 매매하고, 잃고, 계속한다. 백테스트는 몇 분 만에 진실을 드러낸다.

Polymarket 5분 마켓에서의 예시 결과:

- MACD (3/15/3): **승률 60%**
- RSI + VWAP: **승률 59%**
- CVD 다이버전스: **승률 63%**

필요한 것**:** 데이터(OHLCV), 백테스트 엔진, 그리고 명확한 전략 규칙.

보여주는 것**:** 승률, 손익비(profit factor), 최대 낙폭(max drawdown), 샤프 비율(Sharpe ratio).

*백테스트는 보장이 아니다. 과거에 통했던 것이 미래에 통하지 않을 수도 있다. 하지만 추측하는 것보다는 100배 낫다.*

몇 시간마다 확인하라: 에러는 없는가? 주문이 체결되고 있는가? 손익(P&L)이 백테스트 기대치와 일치하는가?

### **인큐베이트: 최소 리스크로 시작하라**

90%의 사람들이 여기서 잘못한다 — 좋은 백테스트 숫자를 보고 곧바로 1만 달러를 넣는다.

올바른 접근법: Polymarket에서 $1로 시작하여 2~4주간 관찰한 뒤, 천천히 규모를 키운다 — $1 -> $5 -> $10 -> $50 -> $100.

*트레이딩은 마라톤이다.*

---

# **실전편: 봇 만들기**

### **1단계: Claude Code 설치**

bash

```
npm install -g @anthropic-ai/claude-code
```

*Claude Code는 터미널에서 직접 코딩하는 AI 에이전트다.*

*입력: 평범한 언어로 된 작업 지시. 출력: 작동하는 코드.*

### **2단계: 프로젝트 생성**

bash

```
mkdir polymarket-rbi-bot && cd polymarket-rbi-bot
claude
```

*이제 Claude Code가 활성화되었다. 여기서부터는 — 평범한 텍스트로 작업을 지시한다.*

### **3단계: 프로젝트 구조 생성**

**Claude Code에 입력할 명령:**

"Polymarket용 Python 트레이딩 봇 프로젝트를 만들어줘. 구조: 세 가지 전략(MACD, RSI, CVD)이 담긴 strategies/ 폴더, 백테스트 엔진이 담긴 backtesting/ 폴더, 트레이더와 리스크 매니저가 담긴 bot/ 폴더, 진입점(entry point)이 담긴 deploy/ 폴더. Polymarket API에는 py-clob-client를 사용해. 지정가 주문(limit order)만 사용해. .env.example, requirements.txt, .gitignore, README를 추가해."

결과 — 15~20분 만에 완성된 프로젝트 구조:

text

```
polymarket-rbi-bot/
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── config/
│   ├── settings.py          # 설정: 포지션 크기, 타임프레임, 리스크
│   └── accounts.py          # 다중 계정 Polymarket 설정
├── data/
│   ├── downloader.py        # ccxt를 통한 OHLCV 데이터 다운로드
│   ├── polymarket_client.py # Polymarket CLOB API 클라이언트
│   └── storage.py           # 데이터 저장 CSV/SQLite
├── strategies/
│   ├── base_strategy.py     # 추상 전략 클래스
│   ├── macd_strategy.py     # MACD 히스토그램 (3/15/3)
│   ├── rsi_mean_reversion.py# RSI 평균 회귀 + VWAP
│   └── cvd_strategy.py      # 누적 거래량 델타 (Cumulative Volume Delta)
├── backtesting/
│   ├── engine.py            # 백테스트 엔진
│   ├── metrics.py           # 승률, 손익비, 샤프, 낙폭
│   └── runner.py            # 병렬 백테스트 러너
├── bot/
│   ├── trader.py            # 매매 실행
│   ├── risk_manager.py      # 리스크 관리
│   ├── order_manager.py     # 지정가 주문, 중복 확인
│   └── position_tracker.py  # 포지션 및 손익 추적
├── incubation/
│   ├── monitor.py           # 봇 모니터링
│   ├── scaler.py            # 규모 스케일링
│   └── logger.py            # 매매 로깅
├── deploy/
│   ├── run_bot.py           # 봇 실행
│   ├── run_backtest.py      # 백테스트 실행
│   └── run_monitor.py       # 인큐베이션 모니터링
└── tests/
    ├── test_strategies.py
    ├── test_backtesting.py
    └── test_risk_manager.py
```

### **4단계: Polymarket 연결**

핵심 파일 — `polymarket_client.py`. 공식 SDK인 `py-clob-client`를 사용한다:

bash

```
pip install py-clob-client
```

python

```
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

# 클라이언트 초기화
client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY,        # 지갑 개인키 (.env에서)
    chain_id=137,           # Polygon 메인넷
    funder=FUNDER_ADDRESS,  # 지갑 주소 (.env에서)
    signature_type=2        # EIP-1271
)

# API 자격 증명 생성 (한 번만)
client.set_api_creds(client.create_or_derive_api_creds())
```

**지정가 주문 넣기:**

python

```
order_args = OrderArgs(
    price=0.50,        # 가격 (0.01 - 0.99)
    size=1.0,          # 크기 (달러 단위)
    side=BUY,          # BUY 또는 SELL
    token_id=TOKEN_ID  # 마켓 토큰 ID
)
signed_order = client.create_order(order_args)
response = client.post_order(signed_order, OrderType.GTC)
```

**주문 취소 (새 주문을 넣기 전 필수):**

python

```
open_orders = client.get_orders(market=MARKET_ID, asset_id=TOKEN_ID)
for order in open_orders:
    client.cancel(order_id=order["id"])
```

**환경 변수 (.env):**

text

```
POLYMARKET_PRIVATE_KEY=0x...
POLYMARKET_FUNDER_ADDRESS=0x...
```

### **5단계: 세 가지 스타터 전략**

text

```
**MACD 히스토그램 (fast=3, slow=15, signal=3)**
- 진입: MACD 라인이 시그널 라인을 교차
- 청산: 역방향 교차 또는 손절/익절
- 5분 창 내의 추세성 움직임에서 잘 작동

**RSI 평균 회귀 (RSI 14)**
- RSI < 30 -> 과매도 -> 롱 진입
- RSI > 50 또는 가격이 VWAP에 도달하면 청산
- 급격한 움직임 이후의 되돌림에 적합

**CVD (누적 거래량 델타)**
- 가격-거래량 다이버전스 -> 신호
- 가격은 하락하지만 CVD는 상승 -> 매수 압력 -> 롱
- 반전 지점을 식별하는 데 유용
```

### **6단계: 백테스트 실행**

bash

```
python deploy/run_backtest.py
```

text

```
수익성 있는 전략의 기준선:
- 승률 > 55%
- 손익비 > 1.5
- 최대 낙폭 < 20%
- 샘플 내 최소 100회 이상의 매매
```

*전략이 기준을 통과하지 못하면 — 넘어가라. 아이디어에 집착하지 마라.*

### **7단계: 인큐베이션 모드로 봇 실행**

bash

```
python deploy/run_bot.py --strategy macd --size 1 --account account_1
```

*봇이 $1 크기로 가동 중이다. 이제 관찰하라.*

### **8단계: 병렬 봇**

여러 봇을 동시에, 각각 자신의 터미널에서:

bash

```
# 터미널 1 — MACD 봇
python deploy/run_bot.py --strategy macd --account account_1

# 터미널 2 — RSI 봇
python deploy/run_bot.py --strategy rsi --account account_2

# 터미널 3 — CVD 봇
python deploy/run_bot.py --strategy cvd --account account_3

# 터미널 4 — 모니터링
python deploy/run_monitor.py
```

*각 봇은 — 자신의 Polymarket 계정, 자신의 전략, 자신의 포지션 크기를 갖는다.*

---

### **수수료에 관한 결정적 사실**

Polymarket에서 지정가 주문(limit order)은 **무료**다. 시장가 주문(market order)은 그렇지 않다. 봇은 항상 지정가 주문만 사용해야 한다.

Claude Code는 한 사람이 팀 전체의 일을 해낼 수 있게 한다: 며칠 대신 한 시간 만에 끝나는 백테스트, 몇 주 대신 하루 만에 완성되는 봇, 몇 분 만에 끝나는 버그 수정. 3~6개의 에이전트를 병렬로 실행할 수 있다 — 각각 자신의 작업을 수행한다.

시장은 노력에 보상하지 않는다. 시스템에 보상한다.

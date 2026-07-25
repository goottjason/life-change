# upbit-rbi-bot

업비트(Upbit) 자동매매 봇. **매매 규칙 헌장(`../docs/TRADING_CHARTER_KR.md`)** 을 코드로 강제한다.
LIFE_CHANGE(Polymarket) 문서를 참고자료로 하되, 업비트에 맞지 않는 요소는 폐기·변형했다(헌장 §12).

## 핵심 원칙 (요약)
- 감정이 아니라 시스템 / **안전장치가 전략보다 먼저** / 검증 없이 투입 없음
- 수익 = `(승률 × 익절폭) − (패율 × 손절폭) − 수수료(왕복 0.1%)` 가 (+)여야만 운용
- 전략: MACD(추세) · RSI 평균회귀(횡보) · CVD(반전) — 레짐 필터로 선택 활성화

## 프로젝트 구조
```
upbit-rbi-bot/
├── config/
│   ├── charter.py        # ★ 헌장 파라미터 SSOT (§13 표와 1:1)
│   └── settings.py       # 환경변수(.env)·인프라 설정
├── indicators/ta.py      # MACD/RSI/VWAP/ATR/CVD/ADX 계산
├── strategies/
│   ├── base.py           # 전략 추상 + Signal
│   ├── macd.py / rsi_mean_reversion.py / cvd.py
│   └── regime.py         # 레짐 필터 (§8)
├── bot/
│   ├── trader.py         # ★ 메인 루프 (전 조항 조립점)
│   ├── position.py       # 포지션 + 가격기반 청산 (§4)
│   ├── dead_position.py  # 죽은 포지션/시간손절 (§4-A)
│   ├── risk_manager.py   # 서킷 브레이커(§5)+사이징(§7)
│   └── order_manager.py  # 주문 집행 (§6)
├── safety/
│   ├── failsafe.py       # 킬스위치·에러대응·상태복구 (§9)
│   └── notifier.py       # 텔레그램 알림
├── data/upbit_client.py  # pyupbit 래퍼 (DRY_RUN 지원)
├── backtesting/          # 수수료 반영 백테스트 (§11)
├── incubation/logger.py  # 거래 로깅 (§10)
├── deploy/
│   ├── run_bot.py        # 봇 실행
│   ├── run_backtest.py   # 백테스트 실행
│   └── kill.py           # 긴급 킬 스위치
└── tests/                # 헌장 값 회귀 테스트
```

## 실행 순서 (헌장 §14)
안전장치(§9) → 서킷 브레이커(§5) → 포지션 사이징(§7) → 주문 집행(§6) 순으로 검증한 뒤 실거래.

```bash
pip install -r requirements.txt
cp .env.example .env          # 키 입력 (실거래 시). 기본은 DRY_RUN=true

python deploy/run_backtest.py # 1) 백테스트로 §11 기준 통과 확인
python deploy/run_bot.py      # 2) DRY_RUN 모의 → 이상 없으면 소액 실거래
pytest tests/                 # 헌장 값 정합성 검증
```

## 안전 기본값
- **DRY_RUN=true** 가 기본. 실거래는 명시적으로 꺼야 함.
- API 키는 **출금 권한 미부여**, `.env`로 분리(커밋 금지).
- 손절만 시장가, 나머지는 지정가. 킬 스위치는 `deploy/kill.py` 로 언제든 실행.

> ⚠️ 모든 파라미터는 백테스트로 검증할 **출발 기본값**이다. 검증 전 실자본 투입 금지.
> 투자 손실 위험이 있으며 본 코드는 교육·연구 목적의 스캐폴드다.

# 백테스트 리서치 스크립트 (2026-07-26)

현 전략(v1.2: ATR 정규화 SL/TP + 1% 하한 + 수수료 0.1%)의 기댓값 검증용. pyupbit로 캔들 수집(네트워크 필요).
로컬 실행: `cd upbit-rbi-bot && .venv/bin/python backtesting/research/<script>.py`

- `backtest_now.py [candles]` — 동적 유니버스+majors, 전략×마켓 + 전략별 합산. 기본 6000봉(5m≈21일).
- `sweep_tf.py` — BTC/ETH/XRP를 5m/15m/1h/4h로 전략별 기댓값 스윕.
- `sweep_filters.py` — 추세필터(EMA200)/역방향청산제거 조합 실험.

## 2026-07-26 결과 요약
세 전략 모두 **수수료 반영 후 음의 기댓값**(모든 TF). MACD가 최악(whipsaw). rsi/cvd 15m이 그나마 손익분기 근처(그래도 −).
robust 발견: **역방향청산 제거(noRev)** 시 churn 감소로 macd/rsi 15m이 ~손익분기(PF 1.0~1.1, 수익 아님). 추세필터(EMA200)는 표본만 굶김.
'+' 뜬 조합(rsi noRev +0.036%/57거래, rsi+trend+noRev +0.128%/8거래)은 노이즈/소표본 — 엣지 아님.
근본: 신호 거래당 엣지 < 수수료(0.1%). §11(PF>1.5·승률>55%·≥100거래) 근처도 못 감.

## 다음 세션 TODO
1. **더 긴 히스토리(6개월+, 추세장 국면 포함)** 재검증 — 각 스크립트 candles/TF 늘리기(예: 1h count 4000≈166일, 4h count 4000). day봉도.
2. **walk-forward / out-of-sample** 틀 — 앞 구간 튜닝 → 뒤 구간 검증(과최적화 방지). 아직 미구현.
3. 역방향청산 제거를 코드 반영할지 결정(robust 개선이나 여전히 손익분기).
4. 신호 재설계(수수료 넘는 저빈도·고확신 엣지) 브레인스토밍.

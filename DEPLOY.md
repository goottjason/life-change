# life-change 배포 가이드

sbshop-agent / can-agent 와 **동일한 인프라 규칙**으로 배포한다.
- 서버(Oracle): `168.107.31.154`, user `ubuntu`
- 공유 네트워크 `shared-net`, 공유 nginx `projects-nginx-1` (경로 프리픽스 라우팅)
- 호스트 포트 미publish → 컨테이너명/경로로만 접근
- 컨테이너: `projects-life-change-1` · URL: `https://168.107.31.154/life-change/`
- 자동 배포: `main` 브랜치 push → GitHub Actions(`.github/workflows/deploy.yml`)

## 구성 요소
```
life-change/
├── Dockerfile              # python:3.11-slim + FastAPI 대시보드 + 봇
├── docker-compose.yml      # projects-life-change-1, shared-net, 포트 비공개
├── .env.example            # → .env 로 복사 후 키 입력
├── .github/workflows/deploy.yml   # validate → tests → 서버 ops/deploy.sh (secret: LIFECHANGE_DEPLOY_KEY)
├── .github/workflows/tests.yml    # pytest (배포 전 게이트)
├── .github/workflows/images.yml   # arm64 이미지 빌드 → GHCR (서버는 pull 만)
├── ops/deploy.sh, ops/deploy.conf # 서버 배포 스크립트(잠금·빌드 선행·지문·자동 롤백) + 대상 설정
├── ops/test/deploy_test.sh        # 배포 스크립트 셸 테스트(배포 때 서버에서 먼저 실행)
├── deploy.sh               # 수동/긴급 배포 = 위 워크플로 수동 실행 래퍼
├── nginx/life-change.location.conf # 공유 nginx 에 추가할 location 블록
└── upbit-rbi-bot/          # 앱 코드 (dashboard/, bot/, strategies/ ...)
```

## 최초 1회 부트스트랩 (수동 — 내 계정/서버 권한 필요)

### 1) GitHub 리포 생성 & 시크릿 등록
```bash
# 로컬 리포 루트(/Users/jasonair/Projects/life-change)에서
git init && git add . && git commit -m "life-change: dashboard + bot + CI/CD"
git branch -M main
git remote add origin git@github.com:goottjason/life-change.git
git push -u origin main
```
- GitHub 리포 **Settings → Secrets and variables → Actions** 에서
  **`LIFECHANGE_DEPLOY_KEY`** = 배포용 개인키 전체 내용(다른 두 프로젝트와 동일 키 사용)

### 2) 서버에 리포 클론 + .env 작성 (SSH 접속)
```bash
ssh -i ssh-key-2026-06-25.key ubuntu@168.107.31.154
cd ~/projects
git clone git@github.com:goottjason/life-change.git
cd life-change
cp .env.example .env
# .env 편집: DRY_RUN=false, UPBIT_ACCESS_KEY/SECRET_KEY, DASHBOARD_TOKEN, (선택)TELEGRAM_*
```
> ⚠️ 업비트 API 키는 **자산 조회 + 주문 권한만**, **출금 권한 미부여**. 허용 IP에 서버 공인 IP 등록.

### 3) 공유 nginx 에 경로 추가
`~/projects/infra` 의 nginx 설정 server{} 블록에 `nginx/life-change.location.conf` 내용을 추가 후:
```bash
docker exec projects-nginx-1 nginx -t && docker exec projects-nginx-1 nginx -s reload
```

### 4) 첫 기동
```bash
cd ~/projects/life-change
docker compose up -d --build
curl -k https://168.107.31.154/life-change/health   # → ok
```
브라우저: `https://168.107.31.154/life-change/`

## 이후 배포
- **자동**: `git push origin main` → Actions: 입력 검증 → `pytest` ∥ GitHub arm64 러너가 이미지를 빌드해 `ghcr.io/goottjason/life-change:<커밋 SHA 12자>` 로 푸시 → 둘 다 통과하면 서버에서 커밋 SHA 로 `git reset --hard` → 배포 스크립트 셸 테스트 → `ops/deploy.sh`.
  - 서버는 **빌드하지 않는다**. 이미지를 pull 해 기존 로컬 이름(`life-change-life-change:latest`)으로 다시 태그한 뒤, 이미지 내용이 바뀐 경우에만 교체한다(문서·워크플로만 바뀐 push 는 이미지가 같아 재시작하지 않음).
  - 서버 스크립트는 공용 잠금(정비 타이머·다른 프로젝트 배포와 겹치지 않음)을 잡고, 컨테이너를 정상 종료(SIGTERM, 30초)한 뒤 교체한다.
  - 교체 뒤 nginx 를 통해 `/life-change/health` 를 점검하고, 실패하면 직전 이미지로 자동 롤백한다(종료코드 7=롤백 성공·서비스 정상, 8=롤백도 실패).
- **수동**: 로컬에서 `./deploy.sh` (워크플로 수동 실행). `./deploy.sh rollback [prev-태그]`, `./deploy.sh skip-tests`, `./deploy.sh recreate`.
- **롤백**: 서버에 배포마다 `prev-YYYYMMDD-HHMMSS` 이미지 태그가 남는다(최근 3개). 더 오래된 커밋으로는 `./deploy.sh rollback <커밋 SHA 12자>` — 레지스트리에서 그 커밋의 이미지를 받아 되돌린다(재빌드 없음).

## 헌장 v1.5 배포 시 주의 (전략 교체 · 스프레드/상장경과일 필터 · 15분봉 병행)

v1.3부터 실거래에는 `.env` 에 **두 값**이 모두 필요하다 (헌장 §9.7):

```
DRY_RUN=false
LIVE_CHARTER_ACK=v2.2     # ← 없으면 배포해도 자동으로 '모의 모드'로 강제된다
```

- 기존 서버 `.env` 에는 `LIVE_CHARTER_ACK` 이 없으므로, **이 버전을 배포하면 봇은 모의 모드로 전환된다**(주문 미전송). 부팅 시 텔레그램으로 사유를 알린다.
- 이는 의도된 동작이다: v1.3~v1.5는 전략을 macd/rsi/cvd → **rsi2(5분) + rsi2_15m(15분)** 으로 교체했으므로, 규칙 개정마다 명시적 승인을 요구한다.
- 모의 관찰 항목: 진입 신호 발생 빈도, 대시보드 `spreads`/`spread_rejected`(스프레드 필터 동작), `trend_up`(1시간봉 추세), 실제 체결가 vs 신호가 괴리.
- **rsi2는 변동성이 낮은 국면에서 아무 거래도 하지 않는 것이 정상**이다(ATR/가격 ≥0.6% 게이트). 2026년 7월 기준 최근 30일 신호 0건 — 봇이 멈춘 것이 아니다.

권고 절차는 모의 2~4주 후, 실제 체결 비용이 백테스트 가정(수수료 0.1% + 슬리피지 0.05~0.10%) 이내임을 확인한 다음 `LIVE_CHARTER_ACK=v2.2` 을 추가하고 재시작하는 것이다.

> **운영자 결정 기록 (2026-07-26)**: 시드 90,000원 전액 손실을 감수하고 **모의 단계를 건너뛰어 즉시 실거래**로 전환했다.
> 따라서 아래 항목을 실거래 중에 관찰해야 한다 — (1) 실제 체결가 vs 신호가 괴리(= 실효 슬리피지),
> (2) 진입 신호 발생 빈도, (3) 스프레드 필터가 거른 종목. 실효 왕복비용이 0.25%를 넘으면 검증된 엣지가
> 소멸하므로(exp +0.015%) 즉시 재검토한다. 서킷 브레이커(일 −3%, 연속손절 5회, MDD −15%)는 그대로 작동한다.

## 실전(LIVE) 전환 체크리스트
- [ ] `.env` 의 `DRY_RUN=false`
- [ ] `.env` 의 `LIVE_CHARTER_ACK=v2.2` (헌장 §9.7 — 없으면 모의로 강제)
- [ ] 업비트 키 등록 + 서버 IP 화이트리스트 + **출금 권한 없음** 확인
- [ ] `DASHBOARD_TOKEN` 을 강한 값으로 변경 (킬 스위치 보호)
- [ ] 소액으로 시작(헌장 §7-4: 5,000원 단위) — 사용자 지시대로 백테스트 생략, 소액 실전
- [ ] 텔레그램 알림 연결(서킷 브레이커/에러 통지 §9.5)
- [ ] 첫날 대시보드로 진입/청산·손익·서킷 동작 관찰

## 참고: 왜 포트를 고르지 않아도 되나
공유 nginx 만 외부 노출(HTTPS)하고, 각 앱 컨테이너는 `shared-net` 내부에서 **컨테이너명 + 경로 프리픽스**로 구분된다.
모든 앱이 내부적으로 8080 을 써도 충돌하지 않는다. 새 프로젝트가 확보해야 할 유일한 유니크 값은
**URL 프리픽스(`/life-change`) + 컨테이너명(`projects-life-change-1`) + 서비스 별칭(`life-change`)** 뿐이다.

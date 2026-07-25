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
├── .github/workflows/deploy.yml   # SSH 자동 배포 (secret: LIFECHANGE_DEPLOY_KEY)
├── deploy.sh               # 수동/긴급 배포
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
- **자동**: `git push origin main` → Actions 가 SSH 접속 → `git pull && docker compose up -d --build && nginx reload` → 헬스체크.
- **수동**: 로컬에서 `./deploy.sh`

## 실전(LIVE) 전환 체크리스트
- [ ] `.env` 의 `DRY_RUN=false`
- [ ] 업비트 키 등록 + 서버 IP 화이트리스트 + **출금 권한 없음** 확인
- [ ] `DASHBOARD_TOKEN` 을 강한 값으로 변경 (킬 스위치 보호)
- [ ] 소액으로 시작(헌장 §7-4: 5,000원 단위) — 사용자 지시대로 백테스트 생략, 소액 실전
- [ ] 텔레그램 알림 연결(서킷 브레이커/에러 통지 §9.5)
- [ ] 첫날 대시보드로 진입/청산·손익·서킷 동작 관찰

## 참고: 왜 포트를 고르지 않아도 되나
공유 nginx 만 외부 노출(HTTPS)하고, 각 앱 컨테이너는 `shared-net` 내부에서 **컨테이너명 + 경로 프리픽스**로 구분된다.
모든 앱이 내부적으로 8080 을 써도 충돌하지 않는다. 새 프로젝트가 확보해야 할 유일한 유니크 값은
**URL 프리픽스(`/life-change`) + 컨테이너명(`projects-life-change-1`) + 서비스 별칭(`life-change`)** 뿐이다.

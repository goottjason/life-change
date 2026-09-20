#!/usr/bin/env bash
# 수동/긴급 배포 = GitHub Actions 의 deploy.yml 을 수동 실행한다(서버에 직접 접속해 빌드하지 않는다).
# 같은 파이프라인(테스트 ∥ GitHub 러너의 이미지 빌드 → 서버는 GHCR 에서 pull 만, 잠금·지문·자동 롤백)을 그대로 탄다.
#
# ⚠️ 업비트 실거래 봇이다. 배포하면 컨테이너가 재시작된다(거래 로그·DB 는 볼륨에 유지).
#
# 사용:
#   ./deploy.sh                    origin/main 최신을 테스트 후 배포
#   ./deploy.sh skip-tests         테스트를 건너뛰고 배포(긴급)
#   ./deploy.sh recreate           이미지가 같아도 컨테이너를 다시 만든다
#   ./deploy.sh rollback [태그]    직전 prev-… 이미지로, 또는 커밋 SHA 12자를 주면 레지스트리에서 그 커밋 이미지를 받아 되돌린다
set -euo pipefail
cd "$(dirname "$0")"

cmd="${1:-deploy}"
args=()
case "$cmd" in
  deploy) ;;
  skip-tests) args+=(-f skip_tests=true) ;;
  recreate) args+=(-f recreate=true) ;;
  rollback) args+=(-f rollback=true); if [ -n "${2:-}" ]; then args+=(-f "rollback_tag=$2"); fi ;;
  *) echo "알 수 없는 명령: $cmd"; exit 2 ;;
esac

command -v gh >/dev/null || { echo "gh CLI 가 필요합니다(brew install gh && gh auth login)"; exit 1; }

echo "origin/main 최신: $(git ls-remote origin refs/heads/main | cut -c1-8) (아직 push 안 했다면 먼저 git push origin main — push 자체가 배포를 시작합니다)"
read -r -p "'$cmd' 배포를 시작합니다. 계속할까요? [y/N] " ans
[ "$ans" = "y" ] || { echo "취소됨"; exit 0; }

gh workflow run deploy.yml --ref main ${args[@]+"${args[@]}"}
sleep 3
gh run list --workflow deploy.yml --limit 1
echo "진행 확인: gh run watch \$(gh run list --workflow deploy.yml --limit 1 --json databaseId -q '.[0].databaseId')"

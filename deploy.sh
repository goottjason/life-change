#!/usr/bin/env bash
# 수동 배포 (can-agent/deploy.sh 패턴). 평소엔 git push → GitHub Actions 가 자동 배포.
# 이 스크립트는 긴급/수동 배포용.
set -euo pipefail

SERVER="${SERVER:-168.107.31.154}"
USER_NAME="${USER_NAME:-ubuntu}"
SSH_KEY="${LIFECHANGE_SSH_KEY:-ssh-key-2026-06-25.key}"

if [ ! -f "$SSH_KEY" ]; then
  echo "SSH 키가 없습니다: $SSH_KEY (리포 루트에 두거나 LIFECHANGE_SSH_KEY 로 지정)"
  exit 1
fi
chmod 600 "$SSH_KEY" || true

read -r -p "life-change 를 $SERVER 에 배포합니다. 계속할까요? [y/N] " ans
[ "$ans" = "y" ] || { echo "취소됨"; exit 0; }

ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$USER_NAME@$SERVER" bash -se <<'REMOTE'
  set -euo pipefail
  cd ~/projects/life-change
  git pull origin main
  docker compose up -d --build
  docker exec projects-nginx-1 nginx -s reload
REMOTE

echo "헬스체크..."
for i in $(seq 1 30); do
  sleep 5
  code=$(curl -s -o /dev/null -w '%{http_code}' -L -k --connect-timeout 5 "https://$SERVER/life-change/health" || echo 000)
  { [ "$code" = "200" ] || [ "$code" = "302" ]; } && { echo "✅ 배포 성공 (HTTP $code)"; exit 0; }
  echo "waiting... (HTTP $code, $i/30)"
done
echo "❌ 헬스체크 실패"; exit 1

#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="${COMPOSE_DIR:-$(cd "$HERE/.." && pwd)}"
if [ -f "$HERE/deploy.conf" ]; then . "$HERE/deploy.conf"; fi
SERVICE="${SERVICE:?deploy.conf 에 SERVICE 가 필요합니다}"
CONTAINER="${CONTAINER:?deploy.conf 에 CONTAINER 가 필요합니다}"
IMAGE="${IMAGE:?deploy.conf 에 IMAGE 가 필요합니다}"
ROUTE_PATH="${ROUTE_PATH:?deploy.conf 에 ROUTE_PATH 가 필요합니다}"
ROUTE_OK_CODES="${ROUTE_OK_CODES:-200 301 302}"
NGINX_CONTAINER="${NGINX_CONTAINER:-projects-nginx-1}"
LOCK_FILE="${LOCK_FILE:-$HOME/.sbshop-docker-maintenance.lock}"
LOCK_WAIT_SEC="${LOCK_WAIT_SEC:-900}"
STATE_DIR="${STATE_DIR:-$HOME/.deploy-state/$SERVICE}"
PENDING_TAG="pending-prev"
STOP_TIMEOUT="${STOP_TIMEOUT:-30}"
IMAGE_TAG="${IMAGE_TAG:-}"
IMAGE_PULL="${IMAGE_PULL:-1}"
REGISTRY_IMAGE="${REGISTRY_IMAGE:-}"
MIN_FREE_KB="${MIN_FREE_KB:-10485760}"
HEALTH_WAIT_SEC="${HEALTH_WAIT_SEC:-150}"
HEALTH_POLL_SEC="${HEALTH_POLL_SEC:-3}"
ROLLBACK_WAIT_SEC="${ROLLBACK_WAIT_SEC:-90}"
KEEP_PREV="${KEEP_PREV:-3}"
GUARD="${GUARD-$HERE/guard.sh}"
FORCE="${FORCE:-0}"
RECREATE="${RECREATE:-0}"
DRY_RUN="${DRY_RUN:-0}"
AUTO_ROLLBACK="${AUTO_ROLLBACK:-1}"
PREV_TAG=""

log() { echo "[deploy $(date +%H:%M:%S)] $*"; }
warn() { echo "[deploy $(date +%H:%M:%S)] 경고: $*" >&2; }
die() { echo "[deploy $(date +%H:%M:%S)] 실패: $1" >&2; exit "${2:-1}"; }

run() {
  if [ "$DRY_RUN" = 1 ]; then log "DRY_RUN: $*"; return 0; fi
  "$@"
}

leftover_containers() {
  docker ps -a --format '{{.Names}}' | grep -E "^[0-9a-f]{12}_${CONTAINER}\$" || true
}

built_image_id() { docker image inspect -f '{{.Id}}' "$IMAGE:latest" 2>/dev/null || true; }
running_image_id() { docker inspect -f '{{.Image}}' "$CONTAINER" 2>/dev/null || true; }
wanted_config_hash() {
  (cd "$COMPOSE_DIR" && docker compose config --hash "$SERVICE" 2>/dev/null | awk '{print $2}') || true
}

image_fingerprint() {
  local out
  out="$(docker image inspect -f '{{json .Config.Env}}{{json .Config.Cmd}}{{json .Config.Entrypoint}}{{json .Config.WorkingDir}}{{json .Config.User}}{{json .Config.ExposedPorts}}{{json .Config.Volumes}}{{json .Config.Healthcheck}}{{json .RootFS}}' "$IMAGE:latest" 2>/dev/null)" || true
  [ -n "$out" ] || return 0
  printf '%s' "$out" | sha256sum | cut -d' ' -f1
}

recorded_fingerprint() { cat "$STATE_DIR/$SERVICE.fp" 2>/dev/null || true; }
recorded_config_hash() { cat "$STATE_DIR/$SERVICE.cfg" 2>/dev/null || true; }

write_state() {
  local name="$1" value="$2"
  [ -n "$value" ] || return 0
  if [ "$DRY_RUN" = 1 ]; then log "DRY_RUN: 배포 기록($name)"; return 0; fi
  if ! { mkdir -p "$STATE_DIR" && printf '%s\n' "$value" > "$STATE_DIR/$SERVICE.$name"; } 2>/dev/null; then
    warn "배포 기록 실패: $STATE_DIR/$SERVICE.$name — 다음 배포에서 한 번 더 확인합니다"
  fi
}

record_fingerprint() {
  write_state fp "$(image_fingerprint)"
  write_state cfg "$(wanted_config_hash)"
}

service_changed() {
  local built recorded wanted recorded_cfg
  if [ "$RECREATE" = 1 ]; then return 0; fi
  if [ -z "$(running_image_id)" ]; then return 0; fi
  built="$(image_fingerprint)"
  recorded="$(recorded_fingerprint)"
  if [ -z "$built" ] || [ "$built" != "$recorded" ]; then return 0; fi
  wanted="$(wanted_config_hash)"
  recorded_cfg="$(recorded_config_hash)"
  if [ -n "$wanted" ] && [ -n "$recorded_cfg" ] && [ "$wanted" != "$recorded_cfg" ]; then return 0; fi
  return 1
}

check_disk() {
  local free_kb
  free_kb="$(df --output=avail / | tail -1 | tr -d ' ')"
  if [ "$free_kb" -lt "$MIN_FREE_KB" ]; then
    warn "디스크 여유가 부족합니다(${free_kb}KB < ${MIN_FREE_KB}KB)."
    return 1
  fi
}

run_guard() {
  if [ "$FORCE" = 1 ]; then log "FORCE=1: 배포 가드를 건너뜁니다"; return 0; fi
  if [ -z "$GUARD" ] || [ ! -e "$GUARD" ]; then return 0; fi
  if [ ! -x "$GUARD" ]; then warn "가드 $GUARD 는 있는데 실행 권한이 없습니다(chmod +x 필요). 조용히 건너뛰지 않고 배포를 막습니다"; return 1; fi
  "$GUARD" || return 1
}

valid_image_tag() { [[ "$1" =~ ^[a-z0-9][a-z0-9._-]{0,127}$ ]]; }

valid_registry_image() { [[ "$1" =~ ^[a-z0-9][a-z0-9./_-]*$ ]]; }

registry_ref() { echo "$REGISTRY_IMAGE:$1"; }

pull_image() {
  local ref
  ref="$(registry_ref "$1")"
  log "pull: $ref"
  if [ "$DRY_RUN" = 1 ]; then log "DRY_RUN: docker pull $ref"; return 0; fi
  if [ "$IMAGE_PULL" = 0 ]; then
    docker image inspect "$ref" >/dev/null 2>&1 || return 1
    return 0
  fi
  docker pull "$ref" >/dev/null || return 1
}

retag_pulled_image() {
  local ref
  ref="$(registry_ref "$1")"
  run docker tag "$ref" "$IMAGE:latest" || return 1
  run docker rmi "$ref" >/dev/null 2>&1 || true
}

drop_pulled_ref() {
  run docker rmi "$(registry_ref "$1")" >/dev/null 2>&1 || true
}

pull_and_retag() {
  local tag="$1"
  if ! pull_image "$tag"; then
    drop_pulled_ref "$tag"
    die "이미지 pull 실패: $(registry_ref "$tag") — 컨테이너는 변경하지 않았습니다"
  fi
  retag_pulled_image "$tag" || die "pull 한 이미지를 로컬 이름으로 태그하지 못했습니다 — 컨테이너는 변경하지 않았습니다"
}

remove_leftovers() {
  local name
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    log "찌꺼기 컨테이너 제거: $name"
    run docker rm -f "$name" >/dev/null
  done < <(leftover_containers)
}

snapshot_prev() {
  if [ -n "$(built_image_id)" ]; then
    run docker tag "$IMAGE:latest" "$IMAGE:$PENDING_TAG"
  fi
}

drop_pending() {
  run docker rmi "$IMAGE:$PENDING_TAG" >/dev/null 2>&1 || true
}

prune_prev_tags() {
  local tag
  while IFS= read -r tag; do
    [ -n "$tag" ] || continue
    log "오래된 롤백 태그 제거: $IMAGE:$tag"
    run docker rmi "$IMAGE:$tag" >/dev/null 2>&1 || true
  done < <(docker image ls "$IMAGE" --format '{{.Tag}}' | grep '^prev-' | sort -r | tail -n +"$((KEEP_PREV + 1))" || true)
}

tag_prev() {
  local tag pending="$IMAGE:$PENDING_TAG"
  if [ "$DRY_RUN" != 1 ] && ! docker image inspect "$pending" >/dev/null 2>&1; then return 0; fi
  tag="prev-$(date +%Y%m%d-%H%M%S)"
  log "롤백용 태그: $IMAGE:$tag"
  run docker tag "$pending" "$IMAGE:$tag" || return 1
  PREV_TAG="$tag"
  drop_pending
  prune_prev_tags
}

replace_service() {
  log "교체: $SERVICE"
  run docker stop -t "$STOP_TIMEOUT" "$CONTAINER" >/dev/null 2>&1 || true
  run docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  (cd "$COMPOSE_DIR" && run docker compose up -d --no-build --no-deps "$SERVICE") || return 4
}

verify_running_image() {
  if [ "$DRY_RUN" = 1 ]; then return 0; fi
  [ "$(built_image_id)" = "$(running_image_id)" ]
}

reload_nginx() {
  log "nginx reload (컨테이너 IP 변경 대응)"
  run docker exec "$NGINX_CONTAINER" nginx -s reload
}

http_code_via_nginx() {
  docker exec "$NGINX_CONTAINER" curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1$1" 2>/dev/null || true
}

route_ok() {
  local code c
  code="$(http_code_via_nginx "$ROUTE_PATH")"
  for c in $ROUTE_OK_CODES; do
    if [ "$code" = "$c" ]; then return 0; fi
  done
  return 1
}

wait_ok() {
  local label="$1"; shift
  if [ "$DRY_RUN" = 1 ]; then log "DRY_RUN: $label 점검 생략"; return 0; fi
  local waited=0
  while [ "$waited" -lt "$HEALTH_WAIT_SEC" ]; do
    if "$@"; then log "$label 통과"; return 0; fi
    sleep "$HEALTH_POLL_SEC"
    waited=$((waited + HEALTH_POLL_SEC))
  done
  warn "$label 이(가) ${HEALTH_WAIT_SEC}초 안에 정상이 되지 못했습니다. 최근 로그:"
  docker logs --tail 40 "$CONTAINER" >&2 2>&1 || true
  return 1
}

acquire_lock() {
  if [ "$DRY_RUN" = 1 ]; then return 0; fi
  exec 9>"$LOCK_FILE"
  flock -w "$LOCK_WAIT_SEC" 9 || die "배포/청소 잠금을 ${LOCK_WAIT_SEC}초 안에 얻지 못했습니다"
}

check_service() {
  local routes="${1:-1}"
  if [ "$routes" = 1 ]; then wait_ok "$SERVICE 라우팅(nginx→$SERVICE)" route_ok || return 1; fi
  return 0
}

auto_rollback() {
  local msg="$1" ok=1 routes=1
  local HEALTH_WAIT_SEC="$ROLLBACK_WAIT_SEC"
  [ "$AUTO_ROLLBACK" = 1 ] || return 1
  warn "$msg — 자동 롤백을 시작합니다"
  if [ -z "$PREV_TAG" ]; then
    warn "되돌릴 이전 이미지가 없습니다"
    ok=0
  elif ! { run docker tag "$IMAGE:$PREV_TAG" "$IMAGE:latest" && replace_service; }; then
    warn "이전 이미지로 되돌리지 못했습니다"; ok=0
  else
    reload_nginx || { warn "롤백 후 nginx reload 실패"; routes=0; }
    check_service "$routes" || ok=0
  fi
  if [ "$ok" = 1 ]; then
    die "$msg — 자동 롤백 완료: 이전 버전으로 정상 동작합니다(이번 배포는 실패로 기록됩니다)" 7
  fi
  die "$msg — 자동 롤백도 실패했습니다. 서비스가 내려갔을 수 있습니다: ./ops/deploy.sh rollback 을 실행하세요" 8
}

fail_deploy() {
  auto_rollback "$1" || true
  die "$1${3:+ — $3}" "$2"
}

rollback_service() {
  local tag="${1:-}" tags
  if [[ "$tag" =~ ^[0-9a-f]{12}$ ]]; then
    valid_registry_image "$REGISTRY_IMAGE" || die "레지스트리 이미지 이름 형식이 올바르지 않습니다: $REGISTRY_IMAGE" 2
    acquire_lock
    log "롤백: $SERVICE ← 레지스트리 $tag"
    pull_and_retag "$tag"
  else
    tags="$(docker image ls "$IMAGE" --format '{{.Tag}}' | grep '^prev-' | sort -r || true)"
    [ -n "$tags" ] || die "되돌릴 prev- 태그가 없습니다"
    if [ -z "$tag" ]; then
      tag="$(echo "$tags" | head -1)"
    elif ! echo "$tags" | grep -qx "$tag"; then
      die "태그 $tag 가 없습니다. 있는 태그: $(echo "$tags" | tr '\n' ' ')"
    fi
    acquire_lock
    log "롤백: $SERVICE ← $tag"
    run docker tag "$IMAGE:$tag" "$IMAGE:latest"
  fi
  replace_service || die "$SERVICE 기동 실패 — 컨테이너가 내려간 상태입니다" 4
  local nginx_failed=0 routes=1
  reload_nginx || nginx_failed=1
  if [ "$nginx_failed" = 1 ]; then routes=0; fi
  if ! check_service "$routes"; then
    die "롤백 후 $SERVICE 가 정상이 되지 못했습니다" 6
  fi
  record_fingerprint
  if [ "$nginx_failed" = 1 ]; then
    die "롤백 후 nginx reload 실패 — 새 컨테이너는 떠 있습니다. docker exec $NGINX_CONTAINER nginx -s reload 를 확인하세요" 5
  fi
  log "롤백 완료: $SERVICE"
}

main() {
  if [ "$DRY_RUN" = 1 ]; then log "DRY_RUN: 빌드를 건너뛰므로 변경 판정은 이미 있던 이미지 기준입니다(새 코드는 반영되지 않습니다)"; fi
  if [ -n "$IMAGE_TAG" ]; then
    valid_image_tag "$IMAGE_TAG" || die "이미지 태그 형식이 올바르지 않습니다: $IMAGE_TAG" 2
    valid_registry_image "$REGISTRY_IMAGE" || die "레지스트리 이미지 이름 형식이 올바르지 않습니다: $REGISTRY_IMAGE" 2
  fi
  acquire_lock
  check_disk || die "디스크 여유 부족" 1
  run_guard || die "배포 가드가 배포를 막았습니다(ops/guard.sh). 조건이 풀린 뒤 다시 실행하거나 force 로 실행하세요" 3

  snapshot_prev

  if [ -n "$IMAGE_TAG" ]; then
    log "이미지 받기(태그 $IMAGE_TAG, 실패하면 실행 중인 컨테이너는 그대로 둡니다)"
    pull_and_retag "$IMAGE_TAG"
  else
    log "빌드(실패하면 실행 중인 컨테이너는 그대로 둡니다)"
    (cd "$COMPOSE_DIR" && run docker compose build "$SERVICE") || die "이미지 빌드 실패 — 컨테이너는 변경하지 않았습니다"
  fi
  if [ "$DRY_RUN" != 1 ] && [ -z "$(built_image_id)" ]; then
    die "방금 빌드한 이미지를 찾을 수 없습니다 — 컨테이너는 변경하지 않았습니다"
  fi

  remove_leftovers

  if ! service_changed; then
    drop_pending
    log "이미지·설정이 바뀐 것이 없어 교체하지 않습니다"
    return 0
  fi

  tag_prev || die "롤백용 태그를 만들지 못했습니다 — 컨테이너는 교체하지 않았습니다" 1
  replace_service || fail_deploy "$(replace_failure_message)" 4
  verify_running_image || fail_deploy "실행 중인 이미지가 방금 빌드한 이미지와 다릅니다 — 서비스가 내려갔을 수 있습니다" 6

  local nginx_failed=0 routes=1
  reload_nginx || nginx_failed=1
  if [ "$nginx_failed" = 1 ]; then routes=0; fi
  if ! check_service "$routes"; then
    fail_deploy "$SERVICE 점검 실패" 6 "롤백하려면: ./ops/deploy.sh rollback"
  fi
  record_fingerprint
  if [ "$nginx_failed" = 1 ]; then
    die "nginx reload 실패 — 새 컨테이너는 떠 있습니다. docker exec $NGINX_CONTAINER nginx -s reload 를 확인하세요" 5
  fi
  log "배포 완료: $SERVICE"
}

replace_failure_message() {
  echo "$SERVICE 기동 실패 — 컨테이너가 내려간 상태입니다"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:-deploy}" in
    deploy) main ;;
    rollback) shift; rollback_service "$@" ;;
    *) die "알 수 없는 명령: $1 (deploy | rollback [태그])" 2 ;;
  esac
fi

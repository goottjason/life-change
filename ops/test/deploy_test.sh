#!/usr/bin/env bash
set -uo pipefail

TESTDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ok    $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  FAIL  $1"; [ -n "${2:-}" ] && echo "        $2"; }
assert_eq() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "기대 [$2] 실제 [$3]"; fi; }
assert_contains() { case "$3" in *"$2"*) ok "$1";; *) bad "$1" "[$2] 없음 ← $3";; esac; }
assert_not_contains() { case "$3" in *"$2"*) bad "$1" "[$2] 가 있으면 안 됨 ← $3";; *) ok "$1";; esac; }

TMPD="$(mktemp -d)"; CALLLOG="$TMPD/calls.log"; ROUTE_N_FILE="$TMPD/route.n"; LOCKSTATE="$TMPD/lockstate"
trap 'rm -rf "$TMPD"' EXIT

new_env() {
  : > "$CALLLOG"; : > "$LOCKSTATE"; echo 0 > "$ROUTE_N_FILE"
  rm -f "$TMPD"/replaced "$TMPD"/pending "$TMPD"/rolledback "$TMPD"/built; rm -rf "$TMPD/state"
  OUT_PS=""; BUILT=""; FPC=""; RUNNING=""; TAGS=""; HASH_REC=""; HASH_WANT=""; STOP_RC=0
  BUILD_RC=0; UP_RC=0; NGINX_RC=0; STALE_AFTER_UP=0; ROUTE_CODE=302; ROUTE_OK_AFTER=1
  LOCAL_REGISTRY_TAG=""; PULL_RC=0; RETAG_FAIL=0; BAD=0; BAD_STICKY=0; TAG_FAIL=0; NO_PREV=0; DF_AVAIL=99999999
  export IMAGE_TAG="" IMAGE_PULL=1 REGISTRY_IMAGE="ghcr.io/demo/demo" FORCE=0 RECREATE=0 DRY_RUN=0 AUTO_ROLLBACK=0 HEALTH_WAIT_SEC=4 HEALTH_POLL_SEC=1 ROLLBACK_WAIT_SEC=3
  export MIN_FREE_KB=10485760 KEEP_PREV=3 LOCK_WAIT_SEC=5 GUARD="" STOP_TIMEOUT=30
  export LOCK_FILE="$TMPD/lock" COMPOSE_DIR="$TESTDIR/.." STATE_DIR="$TMPD/state"
  export SERVICE=demo CONTAINER=projects-demo-1 IMAGE=demo-demo
  export ROUTE_PATH=/demo ROUTE_OK_CODES="200 301 302" NGINX_CONTAINER=projects-nginx-1
}

route_bad() {
  [ "$BAD" = 1 ] || return 1
  [ "$BAD_STICKY" = 1 ] && return 0
  [ ! -f "$TMPD/rolledback" ]
}

docker() {
  echo "docker $*" >> "$CALLLOG"
  case "$1" in
    ps) printf '%s' "$OUT_PS" ;;
    image)
      if [ "$2" = inspect ]; then
        local n="${*: -1}"
        case "$n" in *:pending-prev) [ -f "$TMPD/pending" ]; return $? ;; esac
        case "$n" in ghcr.io/*) [ -n "${LOCAL_REGISTRY_TAG:-}" ] && return 0 || return 1 ;; esac
        [ -n "$BUILT" ] || return 1
        if [ "$NO_PREV" = 1 ] && [ ! -f "$TMPD/built" ]; then return 1; fi
        case "$*" in *json*) echo "${FPC:-$BUILT}" ;; *) echo "$BUILT" ;; esac
      elif [ "$2" = ls ]; then printf '%s\n' "$TAGS"; fi ;;
    pull)
      if flock -n "$LOCK_FILE" true 2>/dev/null; then echo UNLOCKED >> "$LOCKSTATE"; else echo LOCKED >> "$LOCKSTATE"; fi
      return "$PULL_RC" ;;
    tag)
      case "${*: -1}" in
        *:pending-prev) touch "$TMPD/pending" ;;
        *:latest) if [ "$RETAG_FAIL" = 1 ]; then return 1; fi
                  case "$2" in *:prev-*) touch "$TMPD/rolledback" ;; esac ;;
        *:prev-*) if [ "$TAG_FAIL" = 1 ]; then return 1; fi ;;
      esac ;;
    stop) return "$STOP_RC" ;;
    rmi) case "${*: -1}" in *:pending-prev) rm -f "$TMPD/pending" ;; esac ;;
    inspect)
      if [ -f "$TMPD/replaced" ] && [ "$STALE_AFTER_UP" != 1 ]; then echo "$BUILT"; return 0; fi
      echo "$RUNNING"; [ -n "$RUNNING" ] || return 1 ;;
    exec)
      case "$*" in
        *"projects-nginx-1 curl"*)
          local rn; rn=$(( $(cat "$ROUTE_N_FILE") + 1 )); echo "$rn" > "$ROUTE_N_FILE"
          if [ "$rn" -lt "$ROUTE_OK_AFTER" ]; then echo 502; return 0; fi
          if route_bad; then echo 502; else echo "$ROUTE_CODE"; fi ;;
        *"nginx -s reload"*) return "$NGINX_RC" ;;
      esac ;;
    compose)
      case "$*" in
        *" config --hash"*) [ -n "$HASH_WANT" ] && echo "$SERVICE $HASH_WANT"; return 0 ;;
        *" build"*)
          touch "$TMPD/built"
          if flock -n "$LOCK_FILE" true 2>/dev/null; then echo UNLOCKED >> "$LOCKSTATE"; else echo LOCKED >> "$LOCKSTATE"; fi
          return "$BUILD_RC" ;;
        *" up "*|*" up -d"*)
          if flock -n "$LOCK_FILE" true 2>/dev/null; then echo UNLOCKED >> "$LOCKSTATE"; else echo LOCKED >> "$LOCKSTATE"; fi
          [ "$UP_RC" = 0 ] && touch "$TMPD/replaced"; return "$UP_RC" ;;
      esac ;;
    logs) echo "fake-log-line" ;;
    *) : ;;
  esac
}
df() { echo "Avail"; echo " $DF_AVAIL "; }
sleep() { :; }

source "$TESTDIR/../deploy.sh"
set +e

calls_str() { cat "$CALLLOG"; }
index_of() { local n; n="$(grep -n -m1 -F -- "$1" "$CALLLOG" | cut -d: -f1)"; echo "${n:--1}"; }
count_of() { grep -c -F -- "$1" "$CALLLOG"; }
fp_of() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }
sync_state() {
  mkdir -p "$STATE_DIR"
  if [ -n "$RUNNING" ]; then fp_of "$RUNNING" > "$STATE_DIR/$SERVICE.fp"; fi
  if [ -n "$HASH_REC" ]; then echo "$HASH_REC" > "$STATE_DIR/$SERVICE.cfg"; fi
}
run_main() { sync_state; ( set -euo pipefail; main ) >"$TMPD/out" 2>&1; }
same_state() { BUILT="img"; RUNNING="img"; }
changed_state() { BUILT="new-img"; RUNNING="old-img"; }

echo "[기본값] 환경변수 없이 소스했을 때(deploy.conf 포함)"
d="$(env -i HOME=/h PATH="$PATH" bash -c 'source "$1"; echo "$SERVICE|$CONTAINER|$IMAGE|$ROUTE_PATH|$ROUTE_OK_CODES"' _ "$TESTDIR/../deploy.sh")"
IFS='|' read -r c_svc c_ctr c_img c_route c_codes <<< "$d"
[ -n "$c_svc" ] && [ -n "$c_route" ] && [ -n "$c_codes" ] && ok "deploy.conf 가 서비스·경로·허용 코드를 정의한다" || bad "deploy.conf 가 비어 있다" "$d"
assert_eq "컨테이너 이름 규칙 projects-<서비스>-1" "projects-$c_svc-1" "$c_ctr"
assert_eq "compose 이미지 이름 규칙 <프로젝트>-<서비스>" "$c_svc-$c_svc" "$c_img"
case "$c_route" in /*) ok "라우팅 경로는 /로 시작한다";; *) bad "라우팅 경로는 /로 시작해야 함" "$c_route";; esac
d="$(env -i HOME=/h PATH="$PATH" bash -c 'source "$1"; echo "$LOCK_WAIT_SEC $MIN_FREE_KB $HEALTH_WAIT_SEC $HEALTH_POLL_SEC $ROLLBACK_WAIT_SEC $KEEP_PREV $FORCE $DRY_RUN $AUTO_ROLLBACK|$LOCK_FILE|$STATE_DIR|$NGINX_CONTAINER"' _ "$TESTDIR/../deploy.sh")"
assert_eq "운영 기본값" "900 10485760 150 3 90 3 0 0 1|/h/.sbshop-docker-maintenance.lock|/h/.deploy-state/$c_svc|projects-nginx-1" "$d"

echo "[die] 메시지에 종료코드가 섞이지 않는다"
o="$( ( die "테스트" 4 ) 2>&1 )"; rc=$?
assert_eq "종료코드는 4" 4 "$rc"
assert_contains "메시지" "실패: 테스트" "$o"
case "$o" in *"테스트 4") bad "메시지 끝에 종료코드가 붙으면 안 됨";; *) ok "메시지 끝에 종료코드가 붙지 않는다";; esac

echo "[leftover_containers] 대상 컨테이너의 해시 접두 찌꺼기만 고른다"
new_env
OUT_PS=$'projects-demo-1\na711f3f9c565_projects-demo-1\nprojects-nginx-1\n5c059c02371b_projects-other-1\nabc_projects-demo-1\n0123456789ab_projects-demo-1-extra\n'
r="$(leftover_containers | tr '\n' ' ')"
assert_contains "대상 찌꺼기를 잡는다" "a711f3f9c565_projects-demo-1" "$r"
assert_not_contains "정상 컨테이너는 건드리지 않는다" " projects-demo-1 " " $r "
assert_not_contains "다른 프로젝트 찌꺼기는 건드리지 않는다" "other" "$r"
assert_not_contains "12자리 16진이 아닌 접두는 제외" "abc_projects" "$r"
assert_not_contains "이름이 더 긴 컨테이너는 제외" "extra" "$r"

echo "[service_changed] 내용 지문·설정 해시·컨테이너 유무·RECREATE"
new_env; BUILT="x"; RUNNING="x"; sync_state
service_changed && bad "같은 내용은 변경 아님" || ok "같은 내용은 변경 아님"
RUNNING="y"; sync_state; BUILT="x"
service_changed && ok "내용이 다르면 변경" || bad "내용이 다르면 변경"
new_env; BUILT="x"; RUNNING="x"
service_changed && ok "상태 파일이 없으면 변경" || bad "상태 파일 없음은 변경"
new_env; BUILT="idx-2"; FPC="content-1"; RUNNING="idx-1"; mkdir -p "$STATE_DIR"; fp_of "content-1" > "$STATE_DIR/$SERVICE.fp"
service_changed && bad "빌드마다 ID 가 바뀌어도 내용이 같으면 변경 아님" || ok "빌드마다 ID 가 바뀌어도 내용이 같으면 변경 아님"
FPC="content-2"; service_changed && ok "내용이 바뀌면 변경" || bad "내용이 바뀌면 변경"
new_env; BUILT="x"; RUNNING="x"; sync_state; RUNNING=""
service_changed && ok "컨테이너가 없으면 변경" || bad "컨테이너 없음은 변경"
new_env; BUILT="x"; RUNNING="x"; HASH_REC="h1"; HASH_WANT="h1"; sync_state
service_changed && bad "내용·설정이 기록값과 같으면 변경 아님" || ok "내용·설정이 기록값과 같으면 변경 아님"
HASH_WANT="h2"; service_changed && ok ".env 등으로 설정 해시가 기록값과 달라지면 변경" || bad "설정 해시 차이는 변경"
HASH_WANT=""; service_changed && bad "설정 해시를 못 구하면 무시" || ok "설정 해시를 못 구하면 무시"
new_env; BUILT="x"; RUNNING="x"; HASH_WANT="h9"; sync_state
service_changed && bad "기록된 설정 해시가 없으면(기준 미상) 설정만으로는 변경 아님" || ok "기록된 설정 해시가 없으면(기준 미상) 설정만으로는 변경 아님"
new_env; BUILT="x"; RUNNING="x"; HASH_REC="55ebaf8b"; HASH_WANT="55ebaf8b"; sync_state
service_changed && bad "컨테이너 라벨과 무관하게 기록값만 본다" || ok "컨테이너 라벨과 무관하게 기록값만 본다"
new_env; BUILT="x"; RUNNING="x"; RECREATE=1; sync_state
service_changed && ok "RECREATE=1 이면 항상 변경" || bad "RECREATE=1"

echo "[image_fingerprint] 라벨은 지문에 넣지 않고 레이어는 넣는다"
new_env; BUILT="x"; image_fingerprint >/dev/null; c="$(calls_str)"
assert_contains "RootFS" "{{json .RootFS}}" "$c"
assert_contains "환경변수" "{{json .Config.Env}}" "$c"
assert_not_contains "설정 전체는 넣지 않는다" "{{json .Config}}" "$c"
assert_not_contains "라벨은 넣지 않는다" "Labels" "$c"

echo "[check_disk]"
new_env; DF_AVAIL=5000000; check_disk; assert_eq "5GB 여유는 실패" 1 $?
new_env; DF_AVAIL=20000000; check_disk; assert_eq "20GB 여유는 통과" 0 $?

echo "[guard] 배포 전 가드 훅"
new_env; printf '#!/bin/sh\nexit 1\n' > "$TMPD/guard.sh"; chmod +x "$TMPD/guard.sh"; GUARD="$TMPD/guard.sh"; changed_state
run_main; rc=$?; c="$(calls_str)"
assert_eq "가드가 막으면 코드 3" 3 "$rc"
assert_not_contains "빌드하지 않는다" "compose build" "$c"
assert_contains "이유를 알린다" "가드" "$(cat "$TMPD/out")"
new_env; GUARD="$TMPD/guard.sh"; FORCE=1; changed_state
run_main; rc=$?
assert_eq "FORCE=1 이면 가드를 건너뛰고 진행" 0 "$rc"
new_env; printf '#!/bin/sh\nexit 0\n' > "$TMPD/guard.sh"; GUARD="$TMPD/guard.sh"; changed_state
run_main; rc=$?
assert_eq "가드가 통과시키면 진행" 0 "$rc"
new_env; GUARD="$TMPD/nope.sh"; changed_state
run_main; rc=$?
assert_eq "가드 파일이 없으면 무시" 0 "$rc"

echo "[main] 정상 배포: 빌드 → prev 태그 → 교체 → nginx → 라우팅 → 지문 기록"
new_env; changed_state; OUT_PS=$'a711f3f9c565_projects-demo-1\n'
run_main; rc=$?; c="$(calls_str)"
assert_eq "정상 종료" 0 "$rc"
assert_contains "서비스만 빌드한다" "compose build demo" "$c"
assert_contains "찌꺼기를 지운다" "rm -f a711f3f9c565_projects-demo-1" "$c"
assert_contains "컨테이너를 교체한다" "up -d --no-build --no-deps demo" "$c"
assert_contains "nginx 를 reload 한다" "nginx -s reload" "$c"
assert_contains "사용자 경로로 라우팅을 점검한다" "projects-nginx-1 curl -s -o /dev/null -w %{http_code} -m 5 http://127.0.0.1/demo" "$c"
assert_eq "빌드하는 동안 배포 잠금을 쥔다" "LOCKED" "$(head -1 "$LOCKSTATE")"
assert_eq "지문이 새 내용으로 기록된다" "$(fp_of new-img)" "$(cat "$STATE_DIR/$SERVICE.fp")"
st=$(index_of "docker stop -t 30 projects-demo-1"); b=$(index_of "compose build"); t=$(index_of "docker tag demo-demo:pending-prev demo-demo:prev-"); rm_=$(index_of "rm -f projects-demo-1"); u=$(index_of "up -d --no-build"); n=$(index_of "nginx -s reload"); h=$(index_of "projects-nginx-1 curl")
sn=$(index_of "docker tag demo-demo:latest demo-demo:pending-prev")
[ "$sn" -ge 0 ] && [ "$sn" -lt "$b" ] && [ "$b" -lt "$t" ] && [ "$t" -lt "$st" ] && [ "$st" -lt "$rm_" ] && [ "$rm_" -lt "$u" ] && [ "$u" -lt "$n" ] && [ "$n" -lt "$h" ] && ok "순서: 스냅샷 → 빌드 → prev 태그 → 정상 종료(stop) → 제거 → 기동 → nginx → 라우팅" || bad "순서가 틀림" "snap=$sn build=$b tag=$t stop=$st rm=$rm_ up=$u nginx=$n route=$h"

echo "[main] 아무것도 안 바뀌면 컨테이너를 건드리지 않는다"
new_env; same_state
run_main; rc=$?; c="$(calls_str)"
assert_eq "정상 종료" 0 "$rc"
assert_not_contains "교체하지 않는다" "up -d --no-build" "$c"
assert_not_contains "컨테이너를 지우지 않는다" "rm -f projects-demo-1" "$c"
assert_not_contains "라우팅 점검도 하지 않는다" "projects-nginx-1 curl" "$c"
assert_contains "임시 태그는 치운다" "rmi demo-demo:pending-prev" "$c"
assert_contains "바뀐 게 없다고 알린다" "바뀐 것이 없어" "$(cat "$TMPD/out")"

echo "[main] RECREATE=1 이면 같아도 다시 만든다"
new_env; same_state; RECREATE=1
run_main; rc=$?
assert_eq "정상 종료" 0 "$rc"
assert_contains "다시 만든다" "up -d --no-build --no-deps demo" "$(calls_str)"

echo "[main] 빌드가 실패하면 실행 중인 컨테이너를 건드리지 않는다"
new_env; changed_state; BUILD_RC=1
run_main; rc=$?; c="$(calls_str)"
assert_eq "코드 1" 1 "$rc"
assert_not_contains "컨테이너를 지우지 않는다" "rm -f projects-demo-1" "$c"
assert_not_contains "새로 띄우지 않는다" "up -d" "$c"

echo "[main] 빌드 산출 이미지를 못 찾으면 컨테이너를 건드리지 않는다"
new_env; RUNNING="old"; BUILT=""
run_main; rc=$?
assert_eq "코드 1" 1 "$rc"
assert_not_contains "새로 띄우지 않는다" "up -d" "$(calls_str)"

echo "[main] 디스크 부족·잠금·가드는 빌드 전에 멈춘다"
new_env; changed_state; DF_AVAIL=1000
run_main; rc=$?
assert_eq "디스크 부족은 코드 1" 1 "$rc"
assert_not_contains "빌드하지 않는다" "compose build" "$(calls_str)"

echo "[main] 새 컨테이너가 비정상이면 직전 이미지로 자동 롤백한다"
new_env; changed_state; AUTO_ROLLBACK=1; BAD=1
run_main; rc=$?; c="$(calls_str)"; o="$(cat "$TMPD/out")"
assert_eq "코드 7(롤백 성공)" 7 "$rc"
assert_contains "자동 롤백 완료를 알린다" "자동 롤백 완료" "$o"
assert_contains "직전 이미지를 latest 로 되돌린다" " demo-demo:latest" "$(echo "$c" | grep -E "^docker tag demo-demo:prev-")"
assert_eq "컨테이너를 두 번 띄운다(교체+롤백)" 2 "$(count_of 'up -d --no-build --no-deps demo')"
assert_eq "nginx 를 두 번 읽힌다" 2 "$(count_of 'nginx -s reload')"
assert_eq "지문은 옛 값 그대로(다음 배포에서 재시도)" "$(fp_of old-img)" "$(cat "$STATE_DIR/$SERVICE.fp")"
assert_not_contains "이미 롤백했는데 수동 롤백 안내를 붙이지 않는다" "롤백하려면" "$o"
assert_not_contains "배포 완료라고 하지 않는다" "배포 완료" "$o"

echo "[main] 롤백 뒤에도 비정상이면 코드 8"
new_env; changed_state; AUTO_ROLLBACK=1; BAD=1; BAD_STICKY=1
run_main; rc=$?
assert_eq "코드 8" 8 "$rc"
assert_contains "수동 조치를 안내한다" "자동 롤백도 실패했습니다" "$(cat "$TMPD/out")"

echo "[main] AUTO_ROLLBACK=0 이면 코드 6 으로 끝나고 되돌리지 않는다"
new_env; changed_state; BAD=1
run_main; rc=$?; c="$(calls_str)"
assert_eq "코드 6" 6 "$rc"
assert_contains "수동 롤백 방법을 안내한다" "롤백하려면: ./ops/deploy.sh rollback" "$(cat "$TMPD/out")"
assert_eq "컨테이너는 한 번만 띄운다" 1 "$(count_of 'up -d --no-build --no-deps demo')"

echo "[main] 기동(up) 실패도 자동 롤백한다"
new_env; changed_state; AUTO_ROLLBACK=1; UP_RC=1
run_main; rc=$?
assert_eq "복구 불가(up 이 계속 실패)면 코드 8" 8 "$rc"

echo "[main] 교체 뒤 이미지 불일치도 자동 롤백한다"
new_env; changed_state; AUTO_ROLLBACK=1; STALE_AFTER_UP=1
run_main; rc=$?
assert_eq "코드 7" 7 "$rc"

echo "[main] nginx reload 만 실패하면 라우팅 점검은 건너뛰고 롤백하지 않는다(코드 5)"
new_env; changed_state; AUTO_ROLLBACK=1; NGINX_RC=1
run_main; rc=$?; c="$(calls_str)"
assert_eq "코드 5" 5 "$rc"
assert_not_contains "라우팅을 점검하지 않는다" "projects-nginx-1 curl" "$c"
assert_eq "지문은 기록한다(새 컨테이너는 떠 있다)" "$(fp_of new-img)" "$(cat "$STATE_DIR/$SERVICE.fp")"

echo "[main] 최초 배포처럼 이전 이미지가 없으면 자동 롤백할 수 없다"
new_env; BUILT="new-img"; RUNNING=""; AUTO_ROLLBACK=1; BAD=1; NO_PREV=1
run_main; rc=$?
assert_eq "코드 8" 8 "$rc"
assert_contains "되돌릴 이미지가 없음을 알린다" "되돌릴 이전 이미지가 없습니다" "$(cat "$TMPD/out")"

echo "[wait_ok] 교체 직후 잠깐 502 여도 재시도해서 통과한다"
new_env; changed_state; ROUTE_OK_AFTER=3
run_main; rc=$?
assert_eq "세 번째 시도에 통과" 0 "$rc"
assert_eq "라우팅을 세 번 시도" 3 "$(count_of 'projects-nginx-1 curl')"
new_env; changed_state; ROUTE_OK_AFTER=999
run_main; rc=$?
assert_eq "끝내 502 면 코드 6" 6 "$rc"
assert_eq "제한 시간만큼만 시도" 4 "$(count_of 'projects-nginx-1 curl')"

echo "[route_ok] 허용 응답 코드"
new_env
for c in 200 301 302; do ROUTE_CODE=$c; route_ok && ok "정상: $c" || bad "정상이어야 함: $c"; done
for c in 502 503 504 000 500 404 401; do ROUTE_CODE=$c; route_ok && bad "비정상이어야 함: $c" || ok "비정상: $c"; done
new_env; ROUTE_OK_CODES="200"; ROUTE_CODE=302; route_ok && bad "설정한 코드만 허용" || ok "설정한 코드만 허용"

echo "[main] 롤백 재점검은 짧은 별도 예산을 쓴다"
new_env; changed_state; AUTO_ROLLBACK=1; BAD=1; BAD_STICKY=1; ROLLBACK_WAIT_SEC=2
run_main; rc=$?
assert_eq "코드 8" 8 "$rc"
assert_eq "라우팅: 교체 직후 4번 + 롤백 뒤 2번" 6 "$(count_of 'projects-nginx-1 curl')"

echo "[main] 지문 기록이 실패해도 배포는 성공으로 끝난다"
new_env; changed_state; sync_state; chmod 400 "$STATE_DIR"/*.fp; chmod 500 "$STATE_DIR"
( set -euo pipefail; main ) >"$TMPD/out" 2>&1; rc=$?
chmod 700 "$STATE_DIR"; chmod 600 "$STATE_DIR"/*.fp
assert_eq "정상 종료" 0 "$rc"
assert_contains "경고" "배포 기록 실패" "$(cat "$TMPD/out")"
assert_not_contains "raw 오류를 노출하지 않는다" "Permission denied" "$(cat "$TMPD/out")"

echo "[main] 롤백용 태그를 못 만들면 교체하지 않는다"
new_env; changed_state; TAG_FAIL=1
run_main; rc=$?; c="$(calls_str)"
assert_eq "코드 1" 1 "$rc"
assert_not_contains "컨테이너를 지우지 않는다" "rm -f projects-demo-1" "$c"

echo "[main] DRY_RUN=1 은 컨테이너를 바꾸는 명령을 실행하지 않는다"
new_env; changed_state; DRY_RUN=1
run_main; rc=$?; c="$(calls_str)"
assert_eq "정상 종료" 0 "$rc"
assert_contains "교체하려던 것을 출력한다" "DRY_RUN: docker compose up -d --no-build --no-deps demo" "$(cat "$TMPD/out")"
assert_not_contains "빌드하지 않는다" "compose build" "$c"
assert_not_contains "rm 하지 않는다" "rm -f" "$c"
assert_not_contains "up 하지 않는다" "up -d" "$c"
assert_not_contains "nginx 를 건드리지 않는다" "nginx -s reload" "$c"
assert_contains "빌드를 건너뛴다고 알린다" "빌드를 건너뛰" "$(cat "$TMPD/out")"

echo "[acquire_lock] 배포/청소 공용 잠금"
new_env
( acquire_lock; flock -n "$LOCK_FILE" true ); assert_eq "잠금을 쥔 동안 다른 프로세스는 못 얻는다" 1 $?
flock -n "$LOCK_FILE" true; assert_eq "끝나면 풀린다" 0 $?
new_env; DRY_RUN=1
( acquire_lock; flock -n "$LOCK_FILE" true ); assert_eq "DRY_RUN=1 은 잠그지 않는다" 0 $?

echo "[replace_service] 강제 종료(SIGKILL) 전에 정상 종료(SIGTERM)를 먼저 시도한다"
new_env; changed_state
run_main
assert_contains "STOP_TIMEOUT 만큼 기다리는 stop" "docker stop -t 30 projects-demo-1" "$(calls_str)"
new_env; changed_state; STOP_TIMEOUT=45
run_main
assert_contains "STOP_TIMEOUT 을 따른다" "docker stop -t 45 projects-demo-1" "$(calls_str)"
new_env; changed_state; STOP_RC=1
run_main; rc=$?
assert_eq "stop 이 실패해도(이미 죽은 컨테이너 등) 교체는 계속한다" 0 "$rc"
assert_contains "rm -f 로 마무리한다" "rm -f projects-demo-1" "$(calls_str)"
new_env; changed_state; AUTO_ROLLBACK=1; BAD=1
run_main
assert_eq "자동 롤백의 교체도 정상 종료를 먼저 시도한다(stop 두 번)" 2 "$(count_of 'docker stop -t 30 projects-demo-1')"

echo "[guard] 있는데 실행할 수 없는 가드는 조용히 넘기지 않고 배포를 막는다"
new_env; printf '#!/bin/sh\nexit 0\n' > "$TMPD/guard.sh"; chmod -x "$TMPD/guard.sh"; GUARD="$TMPD/guard.sh"; changed_state
run_main; rc=$?
assert_eq "실행권한이 없으면 코드 3" 3 "$rc"
assert_contains "이유를 알린다" "실행 권한" "$(cat "$TMPD/out")"
assert_not_contains "빌드하지 않는다" "compose build" "$(calls_str)"
new_env; chmod +x "$TMPD/guard.sh"; GUARD="$TMPD/guard.sh"; FORCE=1; changed_state
run_main; rc=$?
assert_eq "FORCE=1 이면 실행권한이 없어도 건너뛴다" 0 "$rc"

echo "[rollback_service] 롤백도 배포와 같은 잠금 안에서 한다"
new_env; TAGS=$'latest\nprev-20260105-000001'; BUILT="rolled"
( set -euo pipefail; rollback_service ) >"$TMPD/out" 2>&1
assert_eq "컨테이너를 띄울 때 잠금을 쥐고 있다" "LOCKED" "$(head -1 "$LOCKSTATE")"

echo "[record] 성공한 배포만 설정 해시·지문을 기록한다"
new_env; changed_state; HASH_WANT="hh1"
run_main; rc=$?
assert_eq "정상 종료" 0 "$rc"
assert_eq "설정 해시를 기록한다" "hh1" "$(cat "$STATE_DIR/$SERVICE.cfg")"
new_env; changed_state; AUTO_ROLLBACK=1; BAD=1; HASH_WANT="hh1"; HASH_REC="hh0"
run_main; rc=$?
assert_eq "자동 롤백(7)" 7 "$rc"
assert_eq "실패한 배포는 설정 해시를 기록하지 않는다" "hh0" "$(cat "$STATE_DIR/$SERVICE.cfg")"

echo "[valid_image_tag] 허용·거절 집합"
for t in abc123def456 abc.def-1_2 0aedafff3613 a; do valid_image_tag "$t" && ok "허용: $t" || bad "허용해야 함: $t"; done
for t in ';;;abc' '../../abc' '$(id)abc' 'abc;rm -rf /' 'a b' 'ABC' '-abc' '.abc' '' 'abc/def' 'abc:latest'; do valid_image_tag "$t" && bad "거절해야 함: $t" || ok "거절: $t"; done
valid_image_tag $'abc\n' && bad "후행 개행은 거절" || ok "후행 개행은 거절"
echo "[valid_registry_image] 레지스트리 이미지 이름 검사"
for t in ghcr.io/goottjason/can-agent registry.example.com/team/app; do valid_registry_image "$t" && ok "허용: $t" || bad "허용해야 함: $t"; done
for t in 'a b' 'ghcr.io/x;y' '$(id)' '' '/abs' 'A/B'; do valid_registry_image "$t" && bad "거절해야 함: $t" || ok "거절: $t"; done

echo "[main] IMAGE_TAG 를 주면 서버에서 빌드하지 않고 GHCR 에서 pull 한 뒤 로컬 이름으로 다시 태그한다"
new_env; IMAGE_TAG="abc123def456"; changed_state
run_main; rc=$?; c="$(calls_str)"
assert_eq "정상 종료" 0 "$rc"
assert_not_contains "서버에서 빌드하지 않는다" "compose build" "$c"
assert_contains "pull 한다" "docker pull ghcr.io/demo/demo:abc123def456" "$c"
assert_contains "로컬 이름으로 다시 태그한다" "docker tag ghcr.io/demo/demo:abc123def456 demo-demo:latest" "$c"
assert_contains "서버에 쌓이지 않도록 레지스트리 이름표는 뗀다" "rmi ghcr.io/demo/demo:abc123def456" "$c"
assert_contains "교체한다" "up -d --no-build --no-deps demo" "$c"
assert_eq "pull 하는 동안 배포 잠금을 쥔다" "LOCKED" "$(head -1 "$LOCKSTATE")"
sn=$(index_of "docker tag demo-demo:latest demo-demo:pending-prev"); pl=$(index_of "docker pull ghcr.io/demo/demo"); tg=$(index_of "docker tag ghcr.io/demo/demo")
[ "$sn" -ge 0 ] && [ "$sn" -lt "$pl" ] && [ "$pl" -lt "$tg" ] && ok "순서: 임시 태그 → pull → 로컬 태그" || bad "순서가 틀림" "snap=$sn pull=$pl tag=$tg"

echo "[main] IMAGE_TAG 가 비면 옛 경로(서버 빌드)로 동작한다"
new_env; changed_state
run_main; c="$(calls_str)"
assert_contains "서버에서 빌드한다" "compose build demo" "$c"
assert_not_contains "pull 하지 않는다" "docker pull" "$c"

echo "[main] pull 이 실패하면 컨테이너를 건드리지 않고 받은 이름표를 정리한다"
new_env; IMAGE_TAG="abc123def456"; changed_state; PULL_RC=1
run_main; rc=$?; c="$(calls_str)"
assert_eq "코드 1" 1 "$rc"
assert_contains "이유를 알린다" "pull 실패" "$(cat "$TMPD/out")"
assert_not_contains "컨테이너를 지우지 않는다" "rm -f projects-demo-1" "$c"
assert_not_contains "로컬 latest 를 옮기지 않는다" "docker tag ghcr.io" "$c"
assert_contains "이름표를 정리한다" "rmi ghcr.io/demo/demo:abc123def456" "$c"

echo "[main] 로컬 이름으로 다시 태그하다 실패하면 컨테이너를 건드리지 않는다"
new_env; IMAGE_TAG="abc123def456"; changed_state; RETAG_FAIL=1
run_main; rc=$?; c="$(calls_str)"
assert_eq "코드 1" 1 "$rc"
assert_contains "이유를 알린다" "로컬 이름으로 태그하지 못했습니다" "$(cat "$TMPD/out")"
assert_not_contains "컨테이너를 지우지 않는다" "rm -f projects-demo-1" "$c"

echo "[main] 이미지 태그·레지스트리 이름이 이상하면 아무것도 하기 전에 거절한다"
new_env; IMAGE_TAG='abc;rm -rf /'; changed_state
run_main; rc=$?; c="$(calls_str)"
assert_eq "이상한 태그는 코드 2" 2 "$rc"
assert_not_contains "pull 하지 않는다" "docker pull" "$c"
new_env; IMAGE_TAG="abc123def456"; REGISTRY_IMAGE='ghcr.io/x;touch /tmp/pwn'; changed_state
run_main; rc=$?
assert_eq "이상한 레지스트리 이름은 코드 2" 2 "$rc"
assert_not_contains "pull 하지 않는다" "docker pull" "$(calls_str)"

echo "[main] IMAGE_PULL=0 이면 레지스트리에 가지 않고 로컬에 있는 태그를 쓴다"
new_env; IMAGE_TAG="abc123def456"; IMAGE_PULL=0; changed_state; LOCAL_REGISTRY_TAG=1
run_main; rc=$?; c="$(calls_str)"
assert_eq "정상 종료" 0 "$rc"
assert_not_contains "pull 하지 않는다" "docker pull" "$c"
assert_contains "로컬 태그를 다시 태그한다" "docker tag ghcr.io/demo/demo:abc123def456 demo-demo:latest" "$c"
new_env; IMAGE_TAG="abc123def456"; IMAGE_PULL=0; changed_state
run_main; rc=$?
assert_eq "로컬에 그 태그가 없으면 코드 1" 1 "$rc"

echo "[main] DRY_RUN 은 pull 도 실행하지 않는다"
new_env; IMAGE_TAG="abc123def456"; changed_state; DRY_RUN=1
run_main; rc=$?
assert_eq "정상 종료" 0 "$rc"
assert_contains "pull 하려던 것을 출력한다" "DRY_RUN: docker pull ghcr.io/demo/demo:abc123def456" "$(cat "$TMPD/out")"
assert_not_contains "실제로 pull 하지 않는다" "docker pull" "$(calls_str)"

echo "[rollback_service] 커밋 태그(12자리)면 레지스트리에서 받아 되돌린다(prev 태그가 없어도)"
new_env; TAGS=$'latest'; BUILT="rolled"
( set -euo pipefail; rollback_service abc123def456 ) >"$TMPD/out" 2>&1; rc=$?; c="$(calls_str)"
assert_eq "정상 종료" 0 "$rc"
assert_contains "레지스트리에서 받는다" "docker pull ghcr.io/demo/demo:abc123def456" "$c"
assert_contains "latest 로 다시 태그한다" "docker tag ghcr.io/demo/demo:abc123def456 demo-demo:latest" "$c"
assert_contains "라우팅을 점검한다" "projects-nginx-1 curl" "$c"
assert_eq "pull·기동이 모두 잠금 안에서 일어난다" 0 "$(grep -c UNLOCKED "$LOCKSTATE")"
assert_contains "잠금 안에서 실행했다" "LOCKED" "$(cat "$LOCKSTATE")"
new_env; TAGS=$'latest'; PULL_RC=1
( set -euo pipefail; rollback_service abc123def456 ) >"$TMPD/out" 2>&1; rc=$?
assert_eq "받지 못하면 코드 1" 1 "$rc"
assert_not_contains "컨테이너를 건드리지 않는다" "up -d" "$(calls_str)"
new_env; TAGS=$'latest\nprev-20260105-000001'; REGISTRY_IMAGE='ghcr.io/x;y'
( rollback_service abc123def456 ) >"$TMPD/out" 2>&1; assert_eq "이상한 레지스트리 이름은 롤백에서도 거절" 2 $?
new_env; TAGS=$'latest\nprev-20260105-000001'
( rollback_service 'zz;bad' ) >"$TMPD/out" 2>&1; assert_eq "형식이 이상한 태그는 로컬에서 찾다 실패" 1 $?
assert_not_contains "이상한 태그로 pull 하지 않는다" "docker pull" "$(calls_str)"

echo "[prune_prev_tags] 최근 KEEP_PREV 개만 보관"
new_env; touch "$TMPD/pending"
TAGS=$'latest\nprev-20260101-000001\nprev-20260102-000001\nprev-20260103-000001\nprev-20260104-000001'
tag_prev
c="$(calls_str)"
assert_contains "오래된 태그를 지운다" "rmi demo-demo:prev-20260101-000001" "$c"
assert_not_contains "최신 3개는 보관" "rmi demo-demo:prev-20260103-000001" "$c"
assert_not_contains "latest 는 지우지 않는다" "rmi demo-demo:latest" "$c"

echo "[rollback_service] 수동 롤백"
new_env; TAGS=$'latest\nprev-20260101-000001\nprev-20260105-000001'; BUILT="rolled"
( set -euo pipefail; rollback_service ) >"$TMPD/out" 2>&1; rc=$?; c="$(calls_str)"
assert_eq "정상 종료" 0 "$rc"
assert_contains "최신 prev 로 되돌린다" "docker tag demo-demo:prev-20260105-000001 demo-demo:latest" "$c"
assert_contains "라우팅을 점검한다" "projects-nginx-1 curl" "$c"
assert_eq "롤백한 내용의 지문을 기록한다" "$(fp_of rolled)" "$(cat "$STATE_DIR/$SERVICE.fp")"
new_env; TAGS=$'latest\nprev-20260101-000001\nprev-20260105-000001'; BUILT="rolled"
( set -euo pipefail; rollback_service prev-20260101-000001 ) >"$TMPD/out" 2>&1; rc=$?
assert_eq "지정한 태그로 정상 종료" 0 "$rc"
assert_contains "그 태그로 되돌린다" "docker tag demo-demo:prev-20260101-000001 demo-demo:latest" "$(calls_str)"
new_env; TAGS=$'latest'
( rollback_service ) >"$TMPD/out" 2>&1; assert_eq "prev 태그가 없으면 실패" 1 $?
new_env; TAGS=$'latest\nprev-20260105-000001'
( rollback_service prev-20260101-000001 ) >"$TMPD/out" 2>&1; assert_eq "없는 태그는 실패" 1 $?
new_env; TAGS=$'latest\nprev-20260105-000001'; BUILT="rolled"; BAD=1; BAD_STICKY=1
( set -euo pipefail; rollback_service ) >"$TMPD/out" 2>&1; rc=$?
assert_eq "롤백 뒤 비정상이면 코드 6" 6 "$rc"
assert_not_contains "롤백 완료라고 하지 않는다" "롤백 완료" "$(cat "$TMPD/out")"

echo
echo "결과: 통과 $PASS, 실패 $FAIL"
[ "$FAIL" = 0 ]

#!/bin/bash
# DART 차단이 풀리면 자동으로 수집 파이프라인을 돌린다.
#
# 차단 중에는 5분 간격으로 가볍게 1회만 확인한다 (다시 두들기지 않는다).
# 각 단계는 페이지/파일 캐시가 있어 중단되어도 이어서 진행된다.

cd "$(dirname "$0")" || exit 1
LOG=../data/pipeline.log
: > "$LOG"

log(){ echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

log "DART 차단 해제 대기 중 (5분 간격 확인)"
WAITED=0
until curl -s -m 10 -o /dev/null "https://dart.fss.or.kr/" 2>/dev/null; do
  WAITED=$((WAITED + 5))
  if [ "$WAITED" -ge 720 ]; then     # 12시간이면 포기
    log "12시간 대기했지만 여전히 차단. 중단."
    exit 1
  fi
  sleep 300
done
log "차단 해제 확인 (대기 ${WAITED}분). 수집 시작"

log "=== Phase 0: 기업 선정 + 정기보고서 rcp_no ==="
python3 step0_select_companies.py --top 100 >>"$LOG" 2>&1 || { log "Phase 0 실패"; exit 1; }

log "=== Phase 1: 정기보고서 주석 수집·분할 ==="
python3 step1_fetch_and_slice.py >>"$LOG" 2>&1 || log "Phase 1 일부 실패 (계속)"

log "=== Phase 1-b: 이벤트 공시 원문 수집 ==="
python3 step2_fetch_events.py >>"$LOG" 2>&1 || log "Phase 1-b 일부 실패 (계속)"

log "=== 수집 완료 ==="
log "slices 디렉토리: $(find ../slices -name '*.md' | wc -l | tr -d ' ')개 조각"
log "다음: 에이전트가 slices/ 를 직접 읽어 extracted/ 작성 (Phase 2)"

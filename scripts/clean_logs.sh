#!/usr/bin/env bash
# KataGo 엔진/벤치마크 실행 중 쌓이는 디버그 로그를 비운다.
# 전부 재생성 가능한 원시 로그이며(결과는 CSV/JSON으로 이미 요약돼 있음),
# git에도 커밋되지 않는다(.gitignore). 디렉터리 자체는 남겨서
# configs/*.cfg의 logDir 설정이 계속 유효하도록 한다.
set -euo pipefail
cd "$(dirname "$0")/.."

targets=(
  analysis_logs
  models/gtp_logs
  models/analysis_logs
)

for dir in "${targets[@]}"; do
  if [ -d "$dir" ]; then
    before=$(find "$dir" -type f | wc -l)
    find "$dir" -mindepth 1 -type f -delete
    echo "$dir: ${before}개 로그 삭제"
  fi
done

for f in scripts/bench_results/*.log scripts/output/*.log; do
  [ -e "$f" ] && rm -v "$f"
done

echo "완료."

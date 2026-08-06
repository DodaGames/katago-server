#!/usr/bin/env bash
# Test A 전체 실행 오케스트레이터: GPU 샘플러를 백그라운드로 띄운 채
# run_sweep.py(모델 x visits 메인 sweep) -> test_a_extra.py(스레드튜닝/배치효율)
# 순서로 실행하고 마지막에 GPU 샘플러를 종료한다.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python3
RESULTS_DIR=scripts/bench_results
LOG_DIR=scripts/bench_results
mkdir -p "$RESULTS_DIR"

echo "=== [1/3] GPU 샘플러 시작 ==="
$PY scripts/gpu_monitor.py --interval 1 --output "$RESULTS_DIR/gpu_samples_test_a.csv" &
GPU_PID=$!
echo "gpu_monitor pid=$GPU_PID"

cleanup() {
  echo "=== GPU 샘플러 종료 (pid=$GPU_PID) ==="
  kill "$GPU_PID" 2>/dev/null || true
  wait "$GPU_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== [2/3] run_sweep.py 메인 sweep (4모델 x visits 100,200,400,800 x 10게임) ==="
$PY scripts/run_sweep.py \
  --models b10c128,b20c256x2,b18c384nbt,b28c512nbt \
  --max-visits 100,200,400,800 \
  --positions scripts/bench_data/test_a_games.jsonl \
  --timeout 300 \
  --output "$RESULTS_DIR/test_a_baseline.csv" \
  2>&1 | tee "$LOG_DIR/test_a_baseline.log"

echo "=== [3/3] test_a_extra.py (스레드튜닝 + 배치효율, 대표조합 b18c384nbt/visits=200) ==="
$PY scripts/test_a_extra.py \
  --model b18c384nbt \
  --max-visits 200 \
  --output "$RESULTS_DIR/test_a_extra.json" \
  2>&1 | tee "$LOG_DIR/test_a_extra.log"

echo "=== Test A 전체 완료 ==="

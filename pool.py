import os
import sys
import threading
from katago_worker import KataGoWorker
from config import (
    SERVING_MODELS,
    base_model_path,
    config_path,
    NUM_WORKERS_PER_MODEL,
)

# 모델별 워커 풀 초기화
# 구조: { "level2": [worker1, worker2], "level3": [worker1, worker2] }
analysis_worker_map = {}
analysis_rr_indices = {}  # 라운드 로빈 인덱스 { "level2": 0, "level3": 0 }

print("Initializing Analysis Workers...")
for model_id, model_file in SERVING_MODELS.items():
    print(f"  - Loading model: {model_id} ({model_file})")
    model_full_path = base_model_path + model_file

    if not os.path.exists(model_full_path):
        error_msg = f"\n[CRITICAL ERROR] Failed to start server.\nModel file not found: {model_full_path}\nPlease ensure the model file is downloaded and located in the correct path."
        print(error_msg, file=sys.stderr)
        sys.exit(1)

    workers = [
        KataGoWorker(model_full_path, config_path) for _ in range(NUM_WORKERS_PER_MODEL)
    ]
    analysis_worker_map[model_id] = workers
    analysis_rr_indices[model_id] = 0

_pool_lock = threading.Lock()


def get_analysis_worker(model_id: str):
    """
    해당 model_id를 담당하는 워커를 반환합니다.
    """
    with _pool_lock:
        workers = analysis_worker_map.get(model_id)
        if not workers:
            return None

        idx = analysis_rr_indices[model_id]
        worker = workers[idx]
        analysis_rr_indices[model_id] = (idx + 1) % len(workers)
        return worker

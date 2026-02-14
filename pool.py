import threading
from katago_worker import KataGoWorker
from config import (
    analysis_model_path,
    human_model_path,
    config_path,
    NUM_ANALYSIS_WORKERS,
    NUM_HUMAN_WORKERS,
)

# 워커 풀 초기화
katago_analysis_pool = [
    KataGoWorker(analysis_model_path, config_path) for _ in range(NUM_ANALYSIS_WORKERS)
]

katago_human_pool = [
    KataGoWorker(analysis_model_path, config_path, human_model_path)
    for _ in range(NUM_HUMAN_WORKERS)
]

# 라운드 로빈 인덱스
_analysis_idx = 0
_human_idx = 0
_pool_lock = threading.Lock()


def get_analysis_worker():
    global _analysis_idx
    with _pool_lock:
        worker = katago_analysis_pool[_analysis_idx]
        _analysis_idx = (_analysis_idx + 1) % NUM_ANALYSIS_WORKERS
        return worker


def get_human_worker():
    global _human_idx
    with _pool_lock:
        worker = katago_human_pool[_human_idx]
        _human_idx = (_human_idx + 1) % NUM_HUMAN_WORKERS
        return worker

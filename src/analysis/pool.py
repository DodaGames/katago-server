import asyncio
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager, nullcontext

from .worker import KataGoWorker
from .metrics import LatencyTracker, get_gpu_stats
from .config import (
    SERVING_MODELS,
    base_model_path,
    config_path,
    NUM_WORKERS_PER_MODEL,
    MAX_CONCURRENT_REQUESTS,
)

logger = logging.getLogger("uvicorn.error")

# 게이트 대기가 이 값을 넘으면 로그로 남긴다. 대기 상황이 /status 폴링에만
# 노출돼 있어, 지나간 포화 구간을 사후에 확인할 방법이 없던 것을 보완한다.
GATE_WAIT_WARN_SECONDS = 5.0


class ConcurrencyGate:
    """model_id별 동시 처리 요청 수를 상한(N)으로 강제하는 FIFO 대기 게이트.

    asyncio.Semaphore의 내부 대기열이 FIFO라, 별도 우선순위 큐 구현 없이도
    상한을 넘는 요청은 먼저 온 순서대로 슬롯을 얻는다(복기끼리의 큐잉).
    """

    def __init__(self, limit: int, model_id: str = ""):
        self.limit = limit
        self.model_id = model_id  # 로그 식별용
        self._semaphore = asyncio.Semaphore(limit)
        self._waiting = 0
        self._in_flight = 0
        self._lock = threading.Lock()
        self.wait_latency = LatencyTracker()

    @asynccontextmanager
    async def acquire(self):
        with self._lock:
            self._waiting += 1
        t0 = time.perf_counter()
        async with self._semaphore:
            with self._lock:
                self._waiting -= 1
                self._in_flight += 1
                waiting_now = self._waiting
            wait_seconds = time.perf_counter() - t0
            self.wait_latency.record(wait_seconds)
            if wait_seconds >= GATE_WAIT_WARN_SECONDS:
                logger.warning(
                    f"[ConcurrencyGate] model={self.model_id} 슬롯 대기 {wait_seconds:.1f}s "
                    f"(limit={self.limit}, 뒤에 대기 중={waiting_now})"
                )
            try:
                yield
            finally:
                with self._lock:
                    self._in_flight -= 1

    def stats(self) -> dict:
        with self._lock:
            waiting, in_flight = self._waiting, self._in_flight
        return {
            "limit": self.limit,
            "in_flight": in_flight,
            "waiting": waiting,
            "wait_latency": self.wait_latency.percentiles(),
        }


# 모델별 워커 풀 초기화
# 구조: { "level2": [worker1, worker2], "level3": [worker1, worker2] }
analysis_worker_map = {}
analysis_rr_indices = {}  # 라운드 로빈 인덱스 { "level2": 0, "level3": 0 }

print("Initializing Analysis Workers...")
for model_id, model_info in SERVING_MODELS.items():
    # 이전 버전 호환 및 단순 문자열 모델명 지원
    if isinstance(model_info, str):
        model_info = {"is_human": False, "main_model": model_info}

    main_model_file = model_info.get("main_model")
    is_human = model_info.get("is_human", False)
    human_model_file = model_info.get("human_model")

    print(
        f"  - Loading model: {model_id} (main: {main_model_file}, human: {human_model_file})"
    )

    main_model_full_path = base_model_path + main_model_file
    if not os.path.exists(main_model_full_path):
        error_msg = f"\n[CRITICAL ERROR] Failed to start server.\nMain model file not found: {main_model_full_path}\nPlease ensure the model file is downloaded and located in the correct path."
        print(error_msg, file=sys.stderr)
        sys.exit(1)

    human_model_full_path = None
    if is_human and human_model_file:
        human_model_full_path = base_model_path + human_model_file
        if not os.path.exists(human_model_full_path):
            error_msg = f"\n[CRITICAL ERROR] Failed to start server.\nHuman model file not found: {human_model_full_path}\nPlease ensure the model file is downloaded and located in the correct path."
            print(error_msg, file=sys.stderr)
            sys.exit(1)

    workers = [
        KataGoWorker(
            main_model_path=main_model_full_path,
            config_path=config_path,
            is_human=is_human,
            human_model_path=human_model_full_path,
            model_id=model_id,
        )
        for _ in range(NUM_WORKERS_PER_MODEL)
    ]
    analysis_worker_map[model_id] = workers
    analysis_rr_indices[model_id] = 0

_pool_lock = threading.Lock()

# model_id별 동시성 게이트(설정된 모델만) + 요청 지연시간 트래커(전 모델)
_concurrency_gates = {
    model_id: ConcurrencyGate(limit, model_id=model_id)
    for model_id, limit in MAX_CONCURRENT_REQUESTS.items()
    if limit and model_id in analysis_worker_map
}
_request_latency = {model_id: LatencyTracker() for model_id in analysis_worker_map}


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


def acquire_concurrency_slot(model_id: str):
    """model_id에 동시성 상한이 설정돼 있으면 FIFO 게이트를, 아니면 no-op 컨텍스트를 반환."""
    gate = _concurrency_gates.get(model_id)
    return gate.acquire() if gate else nullcontext()


def record_request_latency(model_id: str, seconds: float):
    tracker = _request_latency.get(model_id)
    if tracker:
        tracker.record(seconds)


def get_pool_stats() -> dict:
    """모델별 워커 큐 depth, 요청 지연시간(p50/p95), 동시성 게이트 상태, GPU 사용률 스냅샷."""
    stats = {}
    for model_id, workers in analysis_worker_map.items():
        entry = {
            "workers": [worker.get_stats() for worker in workers],
            "latency": _request_latency[model_id].percentiles(),
        }
        gate = _concurrency_gates.get(model_id)
        if gate:
            entry["concurrency_gate"] = gate.stats()
        stats[model_id] = entry

    return {"models": stats, "gpu": get_gpu_stats()}
